"""
持股健檢模組。

對單一持股給：
- 損益（含 % / $）
- 體檢分數 0–100（多因子加權：趨勢、動能、距高、回撤、量、權重 …）
- 分層建議（停利 / 續抱 / 停損 / 加碼 / 減碼）
- 多策略展望（在內建 5 套策略下，這檔『現在』是進場 / 出場 / 中性）

模組刻意不依賴 streamlit，方便 unit test。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from i18n import t


# ---------- 體檢評分 ----------


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except TypeError:
        pass
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def trend_score(snap: dict) -> float:
    """0–100 分趨勢健康度，給 advice 用。"""
    close = _safe_float(snap.get("close"))
    ma20 = _safe_float(snap.get("ma20"))
    ma50 = _safe_float(snap.get("ma50"))
    ma200 = _safe_float(snap.get("ma200"))
    score = 0.0
    if close is not None and ma20 is not None:
        score += 20 if close > ma20 else 0
    if close is not None and ma50 is not None:
        score += 20 if close > ma50 else 0
    if close is not None and ma200 is not None:
        score += 25 if close > ma200 else 0
    if ma20 is not None and ma50 is not None and ma200 is not None:
        if ma20 > ma50 > ma200:
            score += 25  # 多頭排列
        elif ma20 < ma50 < ma200:
            score -= 10  # 空頭排列
    rsi = _safe_float(snap.get("rsi14"))
    if rsi is not None:
        if 50 <= rsi <= 70:
            score += 10
        elif rsi < 30:
            score -= 10  # 超賣，但這在 holding 來看是利空
    return max(0.0, min(100.0, score))


def health_score(snap: dict, weight_pct: float | None = None) -> float:
    """
    多因子體檢分（0–100）：
      趨勢 (40)：close 對 MA20/50/200 + 多頭排列
      動能 (15)：MACD hist > 0、RSI 在 50–70 不超買
      回撤 (15)：60D MDD > -10% / vs 52W high
      風險 (15)：ATR% 不太誇張、波動率合理
      流動性 (5) ：量比 > 0.7
      集中度 (10)：權重 < 25% 給滿；25–35% 部分；>35% 嚴重扣分
    """
    s = 0.0
    close = _safe_float(snap.get("close"))
    ma20 = _safe_float(snap.get("ma20"))
    ma50 = _safe_float(snap.get("ma50"))
    ma200 = _safe_float(snap.get("ma200"))

    # 趨勢
    if close is not None and ma20 is not None:
        s += 12 if close > ma20 else 0
    if close is not None and ma50 is not None:
        s += 13 if close > ma50 else 0
    if close is not None and ma200 is not None:
        s += 15 if close > ma200 else 0

    # 動能
    macd_h = _safe_float(snap.get("macd_hist"))
    if macd_h is not None:
        s += 8 if macd_h > 0 else 0
    rsi = _safe_float(snap.get("rsi14"))
    if rsi is not None:
        if 50 <= rsi <= 70:
            s += 7
        elif 40 <= rsi < 50 or 70 < rsi <= 80:
            s += 3

    # 回撤 / 距高
    mdd = _safe_float(snap.get("mdd_60d"))
    if mdd is not None:
        if mdd > -0.05:
            s += 8
        elif mdd > -0.10:
            s += 5
        elif mdd < -0.20:
            s -= 3
    dist = _safe_float(snap.get("dist_to_52w_high_pct"))
    if dist is not None:
        if dist > -5:
            s += 7
        elif dist > -15:
            s += 3
        elif dist < -30:
            s -= 5

    # 風險（ATR% 過大 = 嚇人）
    atrp = _safe_float(snap.get("atr14_pct"))
    if atrp is not None:
        if atrp < 1.5:
            s += 8
        elif atrp < 3.0:
            s += 5
        elif atrp > 6.0:
            s -= 3
    vol = _safe_float(snap.get("vol_60d_ann"))
    if vol is not None:
        if vol < 0.25:
            s += 7
        elif vol < 0.40:
            s += 3
        elif vol > 0.70:
            s -= 3

    # 流動性
    vr = _safe_float(snap.get("volume_ratio"))
    if vr is not None and vr > 0.7:
        s += 5

    # 集中度
    if weight_pct is not None:
        if weight_pct <= 25:
            s += 10
        elif weight_pct <= 35:
            s += 5
        elif weight_pct > 50:
            s -= 10

    return max(0.0, min(100.0, s))


# ---------- 建議生成 ----------


def advice_keys(snap: dict, pnl_pct: float, weight_pct: float, hscore: float) -> list[str]:
    """
    回 i18n key 陣列（hold.advice.*），會被 UI 翻譯成中/英。
    多條會排序 → 最重要的擺第一。
    """
    out: list[str] = []
    tscore = trend_score(snap)
    close = _safe_float(snap.get("close"))
    ma50 = _safe_float(snap.get("ma50"))

    # 強烈停利
    if pnl_pct >= 50:
        out.append("hold.advice.strong_take_profit")
    elif pnl_pct >= 20 and tscore < 50:
        out.append("hold.advice.take_profit_trend_weak")

    # 停損
    if pnl_pct <= -15:
        out.append("hold.advice.cut_loss")
    elif pnl_pct <= -8 and (close is not None and ma50 is not None and close < ma50):
        out.append("hold.advice.cut_loss")

    # 趨勢 OK 但回測支撐 → 加碼候選（簡化：pnl 微負且趨勢仍強）
    if -5 <= pnl_pct <= 0 and tscore >= 70:
        out.append("hold.advice.add_on_strength")

    # 集中度警示
    if weight_pct > 30:
        out.append("hold.advice.reduce_overweight")

    # 預設兜底
    if not out:
        if hscore >= 60:
            out.append("hold.advice.hold_strong")
        else:
            out.append("hold.advice.hold_neutral")

    return out


# ---------- 多策略展望 ----------


def strategy_outlook(snap: dict, enriched: pd.DataFrame, strategies: list) -> list[dict]:
    """
    對每個 strategy 跑 evaluate，回 status：
      - bullish 若 entry_today=True
      - warn    若 exit_today=True
      - neutral 其他
    output 依策略順序回傳，UI 自己取 top N 顯示。
    """
    out: list[dict] = []
    if enriched is None or enriched.empty:
        return out
    for strat in strategies:
        try:
            ev = strat.evaluate(snap, enriched)
        except Exception:
            continue
        if ev.get("entry_today"):
            status_key = "hold.outlook.bullish"
        elif ev.get("exit_today"):
            status_key = "hold.outlook.warn"
        else:
            status_key = "hold.outlook.neutral"
        out.append({
            "strategy_key": strat.key,
            "strategy_label": strat.label,
            "score": float(ev.get("score") or 0.0),
            "status_key": status_key,
            "entry_today": bool(ev.get("entry_today")),
            "exit_today": bool(ev.get("exit_today")),
        })
    return out


# ---------- 完整評估 dataclass ----------


@dataclass
class HoldingResult:
    symbol: str
    market: str          # "美股" / "台股" / ""
    shares: float
    avg_cost: float
    cur_price: float | None
    market_value: float
    cost_basis: float
    pnl: float
    pnl_pct: float
    weight_pct: float
    health: float        # 0-100
    tier_zh: str         # 強烈買進 / 買進 / 偏多觀察 / 中性 / 避開
    advice_keys: list[str]
    outlook: list[dict] = field(default_factory=list)
    note: str = ""

    def to_row(self) -> dict:
        """轉成 DataFrame 一列；advice / outlook 在 UI 端再 i18n。"""
        return {
            "symbol": self.symbol,
            "market": self.market,
            "shares": self.shares,
            "avg_cost": self.avg_cost,
            "cur_price": self.cur_price,
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "weight_pct": self.weight_pct,
            "health": self.health,
            "tier_zh": self.tier_zh,
            "advice_keys": self.advice_keys,
            "outlook": self.outlook,
            "note": self.note,
        }


def _tier_from_health(h: float) -> str:
    """100 分對應強烈買進，純粹給展示用，不是策略 tier。"""
    if h >= 80:
        return "強烈買進"
    if h >= 65:
        return "買進"
    if h >= 50:
        return "偏多觀察"
    if h >= 30:
        return "中性"
    return "避開"


def evaluate_holding(
    symbol: str,
    shares: float,
    avg_cost: float,
    snap: dict | None,
    enriched: pd.DataFrame | None,
    *,
    portfolio_market_value: float = 0.0,
    market_label: str = "",
    note: str = "",
    strategies: list | None = None,
) -> HoldingResult:
    """
    對單檔持股算出損益 + 體檢分 + 建議 + 策略展望。
    portfolio_market_value：用來算 weight_pct（一定要先算過所有持股的市值才知道）。
    """
    cur = _safe_float(snap.get("close")) if snap else None
    cost_basis = float(shares) * float(avg_cost) if shares and avg_cost else 0.0
    market_value = float(shares) * float(cur) if shares and cur else 0.0
    pnl = market_value - cost_basis
    pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
    weight_pct = (market_value / portfolio_market_value * 100.0) if portfolio_market_value > 0 else 0.0

    if snap is None:
        return HoldingResult(
            symbol=symbol, market=market_label, shares=float(shares),
            avg_cost=float(avg_cost), cur_price=None,
            market_value=0.0, cost_basis=cost_basis, pnl=0.0, pnl_pct=0.0,
            weight_pct=0.0, health=0.0, tier_zh="—",
            advice_keys=["hold.advice.no_data"], outlook=[], note=note,
        )

    h = health_score(snap, weight_pct=weight_pct)
    tier = _tier_from_health(h)
    advice = advice_keys(snap, pnl_pct, weight_pct, h)
    outlook = strategy_outlook(snap, enriched, strategies or [])

    return HoldingResult(
        symbol=symbol,
        market=market_label or str(snap.get("market") or ""),
        shares=float(shares),
        avg_cost=float(avg_cost),
        cur_price=cur,
        market_value=market_value,
        cost_basis=cost_basis,
        pnl=pnl,
        pnl_pct=pnl_pct,
        weight_pct=weight_pct,
        health=h,
        tier_zh=tier,
        advice_keys=advice,
        outlook=outlook,
        note=note,
    )


def format_advice(keys: list[str]) -> str:
    """把 advice_keys 翻譯成『｜』分隔的字串，給表格顯示。"""
    if not keys:
        return ""
    return "  |  ".join(t(k) for k in keys)


def format_outlook(outlook: list[dict], limit: int = 2) -> str:
    """挑分數最高的前 limit 條策略，回 'TF/MR/...' 縮寫條列字串。"""
    if not outlook:
        return ""
    top = sorted(outlook, key=lambda x: x.get("score", 0), reverse=True)[:limit]
    parts = []
    for o in top:
        status = t(o["status_key"])
        parts.append(f"{o['strategy_label']}：{status}")
    return "  |  ".join(parts)
