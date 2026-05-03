"""
持股健檢模組。

對單一持股給：
- 損益（含 % / $）
- 體檢分數 0–100（多因子加權）
- 現股 / 融資；融資以「借款占進場成本％」自股數×均價推算欠款，再估權益比 proxy（可選名目年利率僅紀錄）
- 建議 + 多策略展望
- 組合層級綜合評價 portfolio_verdict()

模組不依賴 streamlit，方便 unit test。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from i18n import t

BulletFmt = tuple[str, dict[str, Any]]


# ---------- 常數（資料層統一英文 key；UI 用 i18n） ----------
POSITION_CASH = "cash"
POSITION_MARGIN = "margin"


def position_label(pos: str | None) -> str:
    """顯示用標籤（依 i18n）。"""
    if pos == POSITION_MARGIN:
        return t("hold.pos.margin")
    return t("hold.pos.cash")


# ---------- 融資維持率 ----------

# 未填融資占比時預設 60％（六成融資、四成自備之常見口語）
DEFAULT_MARGIN_FINANCE_SHARE_PCT = 60.0


def estimate_margin_principal(
    shares: float,
    avg_cost: float,
    *,
    finance_share_pct: float | None,
) -> float | None:
    """股數 × 均價 × 「借款占進場成本％」→ 推算融資欠款本金（近似）。"""
    if shares <= 0 or avg_cost <= 0:
        return None
    cost = float(shares) * float(avg_cost)
    if cost <= 0:
        return None
    if finance_share_pct is None or (isinstance(finance_share_pct, float) and math.isnan(finance_share_pct)):
        fpct = DEFAULT_MARGIN_FINANCE_SHARE_PCT
    else:
        try:
            fpct = float(finance_share_pct)
        except (TypeError, ValueError):
            fpct = DEFAULT_MARGIN_FINANCE_SHARE_PCT
        if fpct <= 0:
            return None
        fpct = min(100.0, fpct)
    loan = cost * fpct / 100.0
    return loan if loan > 0 else None


def margin_equity_ratio_pct(market_value: float, margin_loan: float | None) -> float | None:
    """
    簡化『單檔融資部位』權益比率 proxy（非券商整戶維持率）。
    公式：(市值 − 推算欠款本金) / 市值 × 100；欠款由股數×均價×融資成數算出；非融資或未推算出則 None。
    """
    if market_value <= 0:
        return None
    if margin_loan is None or margin_loan <= 0:
        return None
    equity = market_value - margin_loan
    return 100.0 * equity / market_value


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
            score += 25
        elif ma20 < ma50 < ma200:
            score -= 10
    rsi = _safe_float(snap.get("rsi14"))
    if rsi is not None:
        if 50 <= rsi <= 70:
            score += 10
        elif rsi < 30:
            score -= 10
    return max(0.0, min(100.0, score))


def health_score(snap: dict, weight_pct: float | None = None) -> float:
    """多因子體檢分 0–100"""
    s = 0.0
    close = _safe_float(snap.get("close"))
    ma20 = _safe_float(snap.get("ma20"))
    ma50 = _safe_float(snap.get("ma50"))
    ma200 = _safe_float(snap.get("ma200"))

    if close is not None and ma20 is not None:
        s += 12 if close > ma20 else 0
    if close is not None and ma50 is not None:
        s += 13 if close > ma50 else 0
    if close is not None and ma200 is not None:
        s += 15 if close > ma200 else 0

    macd_h = _safe_float(snap.get("macd_hist"))
    if macd_h is not None:
        s += 8 if macd_h > 0 else 0
    rsi = _safe_float(snap.get("rsi14"))
    if rsi is not None:
        if 50 <= rsi <= 70:
            s += 7
        elif 40 <= rsi < 50 or 70 < rsi <= 80:
            s += 3

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

    vr = _safe_float(snap.get("volume_ratio"))
    if vr is not None and vr > 0.7:
        s += 5

    if weight_pct is not None:
        if weight_pct <= 25:
            s += 10
        elif weight_pct <= 35:
            s += 5
        elif weight_pct > 50:
            s -= 10

    return max(0.0, min(100.0, s))


# ---------- 建議 ----------


def advice_keys(
    snap: dict,
    pnl_pct: float,
    weight_pct: float,
    hscore: float,
    *,
    position_type: str = POSITION_CASH,
    margin_equity_pct: float | None = None,
) -> list[str]:
    out: list[str] = []
    tscore = trend_score(snap)
    close = _safe_float(snap.get("close"))
    ma50 = _safe_float(snap.get("ma50"))

    if pnl_pct >= 50:
        out.append("hold.advice.strong_take_profit")
    elif pnl_pct >= 20 and tscore < 50:
        out.append("hold.advice.take_profit_trend_weak")

    if pnl_pct <= -15:
        out.append("hold.advice.cut_loss")
    elif pnl_pct <= -8 and (close is not None and ma50 is not None and close < ma50):
        out.append("hold.advice.cut_loss")

    if -5 <= pnl_pct <= 0 and tscore >= 70:
        out.append("hold.advice.add_on_strength")

    if weight_pct > 30:
        out.append("hold.advice.reduce_overweight")

    if position_type == POSITION_MARGIN and margin_equity_pct is not None and margin_equity_pct < 35:
        out.append("hold.advice.margin_equity_low")

    if not out:
        if hscore >= 60:
            out.append("hold.advice.hold_strong")
        else:
            out.append("hold.advice.hold_neutral")

    return out


# ---------- 多策略展望 ----------


def strategy_outlook(snap: dict, enriched: pd.DataFrame, strategies: list) -> list[dict]:
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


# ---------- 單檔結果 ----------


@dataclass
class HoldingResult:
    symbol: str
    market: str
    shares: float
    avg_cost: float
    cur_price: float | None
    market_value: float
    cost_basis: float
    pnl: float
    pnl_pct: float
    weight_pct: float
    health: float
    tier_zh: str
    advice_keys: list[str]
    outlook: list[dict] = field(default_factory=list)
    note: str = ""
    position_type: str = POSITION_CASH
    margin_loan: float | None = None  # 依股數／均價／融資成數推算之欠款近似值
    margin_equity_pct: float | None = None
    margin_finance_share_pct: float | None = None  # 推算時使用之借款占進場成本％
    margin_interest_rate_apy_pct: float | None = None  # 名目年利率％（選填，僅顯示）


def _tier_from_health(h: float) -> str:
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
    position_type: str = POSITION_CASH,
    margin_finance_share_pct: float | None = None,
    margin_interest_rate_apy_pct: float | None = None,
    strategies: list | None = None,
) -> HoldingResult:
    cur = _safe_float(snap.get("close")) if snap else None
    cost_basis = float(shares) * float(avg_cost) if shares and avg_cost else 0.0
    market_value = float(shares) * float(cur) if shares and cur else 0.0
    pnl = market_value - cost_basis
    pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
    weight_pct = (market_value / portfolio_market_value * 100.0) if portfolio_market_value > 0 else 0.0

    _ppt = str(position_type).strip().lower()
    pos = POSITION_CASH if _ppt in ("", "cash", "現股", POSITION_CASH) else (
        POSITION_MARGIN if _ppt in (POSITION_MARGIN, "融資", "margin") else POSITION_CASH
    )

    fin_share: float | None = None
    if margin_finance_share_pct is not None:
        try:
            fin_share = float(margin_finance_share_pct)
            if not math.isfinite(fin_share) or fin_share <= 0:
                fin_share = None
        except (TypeError, ValueError):
            fin_share = None
    used_fin_share = fin_share if fin_share is not None else DEFAULT_MARGIN_FINANCE_SHARE_PCT

    ir_apy: float | None = None
    if margin_interest_rate_apy_pct is not None:
        try:
            ir_apy = float(margin_interest_rate_apy_pct)
            if not math.isfinite(ir_apy) or ir_apy <= 0:
                ir_apy = None
        except (TypeError, ValueError):
            ir_apy = None

    loan = None
    if pos == POSITION_MARGIN:
        loan = estimate_margin_principal(
            float(shares), float(avg_cost), finance_share_pct=margin_finance_share_pct,
        )

    m_eq = margin_equity_ratio_pct(market_value, loan) if pos == POSITION_MARGIN else None

    if snap is None:
        return HoldingResult(
            symbol=symbol, market=market_label, shares=float(shares),
            avg_cost=float(avg_cost), cur_price=None,
            market_value=0.0, cost_basis=cost_basis, pnl=0.0, pnl_pct=0.0,
            weight_pct=0.0, health=0.0, tier_zh="—",
            advice_keys=["hold.advice.no_data"], outlook=[], note=note,
            position_type=pos, margin_loan=loan, margin_equity_pct=m_eq,
            margin_finance_share_pct=used_fin_share if pos == POSITION_MARGIN else None,
            margin_interest_rate_apy_pct=ir_apy if pos == POSITION_MARGIN else None,
        )

    h = health_score(snap, weight_pct=weight_pct)
    tier = _tier_from_health(h)
    advice = advice_keys(
        snap, pnl_pct, weight_pct, h,
        position_type=pos, margin_equity_pct=m_eq,
    )
    enr_ok = enriched if enriched is not None else pd.DataFrame()
    outlook = strategy_outlook(snap, enr_ok, strategies or [])

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
        position_type=pos,
        margin_loan=loan,
        margin_equity_pct=m_eq,
        margin_finance_share_pct=used_fin_share if pos == POSITION_MARGIN else None,
        margin_interest_rate_apy_pct=ir_apy if pos == POSITION_MARGIN else None,
    )


# ---------- 組合綜合評價 ----------


@dataclass
class PortfolioVerdict:
    """給 UI 顯示的綜合結論。"""
    score: float
    tier_key: str
    bullets: list[BulletFmt] = field(default_factory=list)


def owner_equity_market_value(r: HoldingResult) -> float:
    """
    計入組合「淨資產（市值基礎）」之每檔自有約當額：
    - **現股**：目前市值。
    - **融資**：**市值 − 推算欠款本金**（欠款以進場×融資％近似）。
    """
    mv = float(r.market_value) if r.market_value else 0.0
    if mv <= 0:
        return 0.0
    if r.position_type == POSITION_MARGIN and r.margin_loan is not None and r.margin_loan > 0:
        try:
            return max(0.0, mv - float(r.margin_loan))
        except (TypeError, ValueError):
            return mv
    return mv


def owner_entry_capital(r: HoldingResult) -> float:
    """
    進場時自備資金（用於組合『成本』加總／損益％分母）：股數×均價視為進場總價，
    **融資檔僅計入自有部分**：總價 × (100 − 借款占進場％) / 100（與推算欠款同一融資％；未給％時預設 60％借款→40％自有）。
    """
    cb = float(r.cost_basis) if r.cost_basis else 0.0
    if cb <= 0:
        return 0.0
    if r.position_type != POSITION_MARGIN:
        return cb
    fp = r.margin_finance_share_pct
    if fp is None or (isinstance(fp, float) and (not math.isfinite(fp) or fp <= 0)):
        fp = DEFAULT_MARGIN_FINANCE_SHARE_PCT
    fp = float(min(100.0, max(0.0, fp)))
    return cb * (100.0 - fp) / 100.0


def portfolio_verdict(
    results: list[HoldingResult],
    *,
    cash: float,
) -> PortfolioVerdict | None:
    """依多檔加權結果產出 0–100 綜合分 + 分級 + 要點 bullets（i18n key）。
    「資金運用率 deploy」＝Σ 市值基礎自有部位 ÷ 淨資產（融資檔為 市值−推算欠款）。"""
    if not results:
        return None

    equity_mv = sum(owner_equity_market_value(r) for r in results)
    net_worth = float(cash) + equity_mv

    avg_h = sum(r.health for r in results) / len(results)
    w_sum = sum(r.weight_pct or 0 for r in results)
    w_health = (
        sum(r.health * r.weight_pct for r in results) / w_sum if w_sum > 0 else avg_h
    )

    max_w = max((r.weight_pct for r in results), default=0.0)
    n_margin = sum(1 for r in results if r.position_type == POSITION_MARGIN)
    lows = [
        r.margin_equity_pct for r in results
        if r.margin_equity_pct is not None
    ]
    min_eq = min(lows, default=None)

    cost_den = sum(owner_entry_capital(r) for r in results)
    unrealized_pct = (sum(r.pnl for r in results) / cost_den * 100.0) if cost_den > 0 else 0.0

    deploy = (equity_mv / net_worth * 100.0) if net_worth > 0 else 0.0

    score = (
        0.48 * w_health
        + 0.22 * max(0.0, 100.0 - min(max_w, 55.0) * 1.55)
        + (8.0 if -5 <= unrealized_pct <= 25 else (-10.0 if unrealized_pct < -12 else -4.0 if unrealized_pct < -8 else 3.0))
    )
    if max_w > 42:
        score -= 14
    if min_eq is not None:
        if min_eq < 26:
            score -= 18
        elif min_eq < 38:
            score -= 8
        elif min_eq < 48:
            score -= 4
    if deploy >= 92:
        score -= 4
    score = max(0.0, min(100.0, score))

    bullets_li: list[BulletFmt] = [("hold.verdict.bullet.avg_health_pp", {"h": avg_h})]
    if deploy >= 95:
        bullets_li.append(("hold.verdict.bullet.deploy_high", {"d": deploy}))
    elif deploy < 40 and deploy > 0:
        bullets_li.append(("hold.verdict.bullet.deploy_low", {"d": deploy}))
    if max_w > 38:
        bullets_li.append(("hold.verdict.bullet.concentrated", {"w": max_w}))
    if n_margin > 0:
        bullets_li.append(("hold.verdict.bullet.has_margin", {}))
    if min_eq is not None and min_eq < 40:
        bullets_li.append(("hold.verdict.bullet.margin_tight", {"m": min_eq}))

    if score >= 75:
        tier_key = "hold.verdict.tier.strong"
    elif score >= 58:
        tier_key = "hold.verdict.tier.balance"
    elif score >= 42:
        tier_key = "hold.verdict.tier.watch"
    else:
        tier_key = "hold.verdict.tier.risk"

    return PortfolioVerdict(score=score, tier_key=tier_key, bullets=bullets_li)


def format_verdict_markdown(pv: PortfolioVerdict) -> str:
    """組合顯示用 markdown。"""
    fmts = pv.bullets
    head = (
        f"### {t('hold.verdict.title')}\n**{t(pv.tier_key)}** · {t('hold.verdict.composite')} "
        f"**{pv.score:.0f}/100**\n\n"
    )
    parts = []
    for bk, fmt in fmts:
        parts.append(f"- {t(bk, **fmt)}")
    return head + "\n".join(parts) + "\n\n" + f"*{t('hold.verdict.disclaimer')}*"


def format_advice(keys: list[str]) -> str:
    if not keys:
        return ""
    return "  |  ".join(t(k) for k in keys)


def format_outlook(outlook: list[dict], limit: int = 2) -> str:
    if not outlook:
        return ""
    top = sorted(outlook, key=lambda x: x.get("score", 0), reverse=True)[:limit]
    parts = []
    for o in top:
        status = t(o["status_key"])
        parts.append(f"{o['strategy_label']}：{status}")
    return "  |  ".join(parts)
