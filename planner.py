"""
資產規劃（Goal-based Allocator + Rebalancing Backtester）。

四個核心元件：
  1. Goal & FeasibilityAssessment：把「目前資金 / 目標資金 / 期間」轉成所需 CAGR，
     並對照市場合理區間給出「合理 / 偏激進 / 過度樂觀」判讀。
  2. RiskProfile：保守 / 平衡 / 積極 → 影響現金緩衝、最大持股數、單檔上限、單筆風險。
  3. AllocationPlan：依當前策略排名（自動 Top N）或手動勾選，產出每檔
     「權重 / 股數 / 名目市值 / ATR 停損 / 風險預算」，並計算組合層級的曝險、預估波動、預估 MDD。
  4. Backtester：以日 K 對組合做向量化回測，三層再平衡規則：
        - 個股：跌破 ATR 停損、或漲到 +Nx ATR 觸停利 → 出場、現金留著等下一輪
        - 組合：總值較高點回撤 ≥ X% → 砍半 / 全出；總值較起點漲 ≥ Y% → 部分獲利落袋
        - 時間：每 N 天例行重排（按起始權重）
     回傳 NAV 曲線、CAGR、Sharpe、MDD、勝率、與基準比較。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from i18n import t, t_lang, get_lang


# ============== Goal ==============


@dataclass
class Goal:
    current_capital: float
    target_capital: float
    horizon_years: float

    @property
    def required_cagr(self) -> float:
        if self.current_capital <= 0 or self.target_capital <= 0 or self.horizon_years <= 0:
            return float("nan")
        return (self.target_capital / self.current_capital) ** (1.0 / self.horizon_years) - 1.0


@dataclass
class FeasibilityAssessment:
    required_cagr: float
    market_baseline_cagr: float
    verdict: str
    note: str
    color: str  # "green" / "amber" / "red"


_FEAS_VERDICT = {
    "ok": {"zh": "合理", "en": "Reasonable"},
    "active": {"zh": "可達成（需主動）", "en": "Achievable (active mgmt)"},
    "aggr": {"zh": "偏激進", "en": "Aggressive"},
    "unrealistic": {"zh": "過度樂觀", "en": "Unrealistic"},
    "invalid": {"zh": "輸入無效", "en": "Invalid input"},
}


def _t_dict(d: dict[str, str]) -> str:
    return d.get(get_lang()) or d.get("zh") or ""


def feasibility_for(goal: Goal, *, market: str = "tw") -> FeasibilityAssessment:
    is_us = market.lower().startswith(("us", "美"))
    base = 0.10 if is_us else 0.08
    req = goal.required_cagr
    if not np.isfinite(req):
        return FeasibilityAssessment(
            req, base, _t_dict(_FEAS_VERDICT["invalid"]),
            _t_dict({"zh": "請填入正確的資金與期間。", "en": "Please enter valid capital and horizon."}),
            "red",
        )
    pct = req * 100.0
    base_pct = base * 100.0
    market_zh = "美" if is_us else "台"
    market_en = "US" if is_us else "TW"
    if req <= base:
        return FeasibilityAssessment(
            req, base, _t_dict(_FEAS_VERDICT["ok"]),
            _t_dict({
                "zh": f"所需年化 {pct:.2f}%，低於{market_zh}股長期合理 {base_pct:.0f}% → 多數年份可達成；維持低費用、紀律執行即可。",
                "en": f"Required CAGR {pct:.2f}% is below the long-run {market_en} mean {base_pct:.0f}%. Achievable in most years with low fees and disciplined execution.",
            }),
            "green",
        )
    if req <= base + 0.05:
        return FeasibilityAssessment(
            req, base, _t_dict(_FEAS_VERDICT["active"]),
            _t_dict({
                "zh": f"所需年化 {pct:.2f}%，略高於市場長期 {base_pct:.0f}%。可達成但需要選股精準或擇時，且某幾年要承受 −15~25% 的回撤。",
                "en": f"Required CAGR {pct:.2f}% slightly above market long-run {base_pct:.0f}%. Doable but needs precise stock-picking or timing; expect −15~25% drawdowns in some years.",
            }),
            "amber",
        )
    if req <= base + 0.15:
        return FeasibilityAssessment(
            req, base, _t_dict(_FEAS_VERDICT["aggr"]),
            _t_dict({
                "zh": f"所需年化 {pct:.2f}%。屬於主動操作的高水準（巴菲特 60 年約 20%）。預期會經歷數次 −25~40% 的回撤；不建議全壓單一策略。",
                "en": f"Required CAGR {pct:.2f}%. This is high-end active management (Buffett ~20% over 60 years). Expect multiple −25~40% drawdowns; don't put everything in one strategy.",
            }),
            "amber",
        )
    return FeasibilityAssessment(
        req, base, _t_dict(_FEAS_VERDICT["unrealistic"]),
        _t_dict({
            "zh": f"所需年化 {pct:.2f}%，遠高於任何長期可持續的水準。建議：拉長期間或調降目標，避免被「衝高目標」逼著做高槓桿與短線。",
            "en": f"Required CAGR {pct:.2f}% far exceeds any sustainable long-term level. Suggestion: lengthen horizon or lower target, otherwise the goal forces high leverage / short-term gambling.",
        }),
        "red",
    )


# ============== Risk Profile ==============


@dataclass(frozen=True)
class RiskProfile:
    key: str
    cash_buffer_pct: float  # 現金緩衝佔總資金 %
    max_positions: int
    max_position_pct: float  # 單檔上限 %
    risk_per_trade_pct: float  # 單筆風險 %（給 ATR 停損用）
    atr_stop_mult: float  # ATR 停損倍數
    take_profit_R: float  # 多少 R 停利
    _label_zh: str = ""
    _label_en: str = ""
    _note_zh: str = ""
    _note_en: str = ""

    @property
    def label(self) -> str:
        if get_lang() == "en" and self._label_en:
            return self._label_en
        return self._label_zh

    @property
    def note(self) -> str:
        if get_lang() == "en" and self._note_en:
            return self._note_en
        return self._note_zh


RISK_PROFILES: dict[str, RiskProfile] = {
    "conservative": RiskProfile(
        key="conservative",
        cash_buffer_pct=25.0, max_positions=5, max_position_pct=25.0,
        risk_per_trade_pct=0.5, atr_stop_mult=2.5, take_profit_R=4.0,
        _label_zh="保守", _label_en="Conservative",
        _note_zh="現金多、持股少、單檔停損遠 → 適合防守型策略 / ETF 為主。",
        _note_en="More cash, fewer positions, looser stops → suits defensive strategies / ETFs.",
    ),
    "balanced": RiskProfile(
        key="balanced",
        cash_buffer_pct=15.0, max_positions=8, max_position_pct=15.0,
        risk_per_trade_pct=1.0, atr_stop_mult=2.0, take_profit_R=3.0,
        _label_zh="平衡", _label_en="Balanced",
        _note_zh="主流配置；股債/個股平均分配，停損與停利取 1:3 風險報酬比。",
        _note_en="Mainstream allocation; balanced positions with 1:3 risk-reward stops.",
    ),
    "aggressive": RiskProfile(
        key="aggressive",
        cash_buffer_pct=5.0, max_positions=12, max_position_pct=12.0,
        risk_per_trade_pct=1.5, atr_stop_mult=1.5, take_profit_R=2.5,
        _label_zh="積極", _label_en="Aggressive",
        _note_zh="高曝險高翻倉；停損緊、停利近，需要嚴格紀律避免被連續打小停損。",
        _note_en="High exposure / high turnover; tight stops, near-term TP — needs strict discipline.",
    ),
}


# ============== Allocation ==============


@dataclass
class AllocationItem:
    symbol: str
    weight_pct: float
    shares: int
    notional: float
    atr_pct: float | None
    stop_price: float | None
    take_profit_price: float | None
    risk_dollar: float
    score: float | None


@dataclass
class AllocationPlan:
    goal: Goal
    risk_profile: RiskProfile
    cash: float
    items: list[AllocationItem]
    note: str

    @property
    def total_notional(self) -> float:
        return sum(i.notional for i in self.items)

    @property
    def total_risk(self) -> float:
        return sum(i.risk_dollar for i in self.items)

    def to_dataframe(self) -> pd.DataFrame:
        rows = [
            {
                "symbol": i.symbol,
                "weight_pct": i.weight_pct,
                "shares": i.shares,
                "notional": i.notional,
                "stop_price": i.stop_price,
                "take_profit_price": i.take_profit_price,
                "atr_pct": i.atr_pct,
                "risk_dollar": i.risk_dollar,
                "score": i.score,
            }
            for i in self.items
        ]
        return pd.DataFrame(rows)


def _round_shares(market: str, raw_shares: float, *, allow_fractional: bool = True) -> int:
    """
    台股若 allow_fractional=True 則走零股（小資族主流，2020 後盤中零股已普及）。
    False 時走整張（1 張 = 1000 股）。美股一律 1 股單位。
    """
    if raw_shares <= 0:
        return 0
    if (market or "").startswith("台") and not allow_fractional:
        return max(int(raw_shares // 1000) * 1000, 0)
    return max(int(raw_shares), 0)


def build_allocation(
    *,
    goal: Goal,
    profile: RiskProfile,
    candidate_df: pd.DataFrame,
    manual_picks: list[str] | None = None,
    auto_top_n: int = 6,
    allow_fractional: bool = True,
) -> AllocationPlan:
    """
    candidate_df 必須含欄位：symbol、market、close、score、atr14（或 atr14_pct）。
    若指定 manual_picks 則只用這些；否則取 score 由高到低 Top N。
    權重：用 score 線性加權，再受 max_position_pct 限制；最後剩餘 = 現金。
    allow_fractional=True：台股走零股；False 走整張（1 張 = 1000 股）。
    """
    if candidate_df.empty:
        return AllocationPlan(goal, profile, cash=goal.current_capital, items=[], note="無候選標的。")

    df = candidate_df.copy()
    if manual_picks:
        wanted = [s.upper() for s in manual_picks]
        df = df[df["symbol"].str.upper().isin(wanted)].copy()
        if df.empty:
            return AllocationPlan(
                goal, profile, cash=goal.current_capital, items=[],
                note="手動勾選的標的不在候選資料中（可能未抓到資料）。",
            )
    else:
        df = df.sort_values("score", ascending=False).head(int(auto_top_n)).copy()

    df = df[df["close"].fillna(0) > 0]
    if df.empty:
        return AllocationPlan(goal, profile, cash=goal.current_capital, items=[], note="候選都缺收盤價。")

    invest_cash = goal.current_capital * (1.0 - profile.cash_buffer_pct / 100.0)
    if invest_cash <= 0:
        return AllocationPlan(goal, profile, cash=goal.current_capital, items=[], note="現金緩衝設定 100%，沒有可投入資金。")

    # 權重：以 score 為基底（min-max scale），全部 ≤ max_position_pct
    s = pd.to_numeric(df["score"], errors="coerce").fillna(0)
    if s.max() == s.min():
        weights = np.ones(len(df)) / len(df)
    else:
        adj = (s - s.min()) + 0.1  # 避免 0
        weights = (adj / adj.sum()).values
    cap = profile.max_position_pct / 100.0
    weights = np.minimum(weights, cap)
    if weights.sum() <= 0:
        weights = np.ones(len(df)) / len(df)
    else:
        weights = weights / weights.sum()  # 重新標準化

    items: list[AllocationItem] = []
    used_cash = 0.0
    risk_budget_per_pos = goal.current_capital * (profile.risk_per_trade_pct / 100.0)

    for (_, row), w in zip(df.iterrows(), weights):
        market = str(row.get("market", ""))
        close = float(row.get("close", 0.0))
        if close <= 0:
            continue
        atr_pct = row.get("atr14_pct")
        atr_pct_v = float(atr_pct) if pd.notna(atr_pct) else None
        atr_abs = (atr_pct_v / 100.0) * close if atr_pct_v else None

        if atr_abs and atr_abs > 0:
            stop = max(close - profile.atr_stop_mult * atr_abs, 0.0)
        else:
            stop = close * (1 - 0.06)  # fallback ‑6%

        risk_per_share = max(close - stop, 1e-9)
        tp = close + profile.take_profit_R * risk_per_share

        # 名目資金 = 投入資金 × weight；張數轉換
        target_notional = invest_cash * w
        raw_shares = target_notional / close
        shares = _round_shares(market, raw_shares, allow_fractional=allow_fractional)
        notional = shares * close
        risk_dollar = shares * risk_per_share
        # 若超過單筆風險預算 → 調降到風險預算上限（仍尊重零股／整張規則）
        if risk_dollar > risk_budget_per_pos and risk_per_share > 0:
            cap_shares = _round_shares(
                market, risk_budget_per_pos / risk_per_share, allow_fractional=allow_fractional
            )
            shares = min(shares, cap_shares)
            notional = shares * close
            risk_dollar = shares * risk_per_share
        used_cash += notional
        items.append(
            AllocationItem(
                symbol=str(row["symbol"]),
                weight_pct=float(notional / max(goal.current_capital, 1e-9) * 100.0),
                shares=int(shares),
                notional=float(notional),
                atr_pct=atr_pct_v,
                stop_price=float(stop),
                take_profit_price=float(tp),
                risk_dollar=float(risk_dollar),
                score=float(row.get("score", 0.0)) if pd.notna(row.get("score")) else None,
            )
        )

    cash = goal.current_capital - used_cash
    if get_lang() == "en":
        lot_note = "TW fractional shares" if allow_fractional else "TW lots (1 lot = 1000 shares)"
        note = (
            f"Risk profile '{profile.label}': cash buffer target {profile.cash_buffer_pct:.0f}%, "
            f"max {profile.max_positions} positions, per-pos cap {profile.max_position_pct:.0f}%, "
            f"risk-per-trade ≤ {profile.risk_per_trade_pct:.2f}%. Actually invested ${used_cash:,.0f} "
            f"({used_cash / max(goal.current_capital, 1):.1%}), cash ${cash:,.0f}. Order unit: {lot_note}."
        )
    else:
        lot_note = "台股零股交易" if allow_fractional else "台股整張交易（1 張 = 1000 股）"
        note = (
            f"風險偏好「{profile.label}」：現金緩衝目標 {profile.cash_buffer_pct:.0f}%、"
            f"最多 {profile.max_positions} 檔、單檔上限 {profile.max_position_pct:.0f}%、"
            f"單筆風險上限 {profile.risk_per_trade_pct:.2f}%。實際投入 ${used_cash:,.0f}（{used_cash / max(goal.current_capital, 1):.1%}）、"
            f"現金 ${cash:,.0f}。下單單位：{lot_note}。"
        )
    return AllocationPlan(goal=goal, risk_profile=profile, cash=cash, items=items, note=note)


# ============== Rebalance Rules ==============


@dataclass
class RebalanceRules:
    # 個股層級
    stock_use_atr_stop: bool = True
    stock_use_take_profit: bool = True

    # 組合層級
    portfolio_drawdown_pct: float | None = 8.0  # 帳戶較高點回撤 ≥ X% → 砍半轉現金
    portfolio_take_profit_pct: float | None = 20.0  # 較起點漲 ≥ Y% → 1/3 落袋

    # 時間
    rebalance_every_days: int | None = 30  # 例行重排 N 天


# ============== 交易成本 ==============


@dataclass(frozen=True)
class TransactionCost:
    """
    台股慣例：
      - 券商手續費 = 0.1425%（買 + 賣各一次），多數券商實際打 5–6 折 → 0.07~0.085%
      - 證券交易稅 = 0.3%（賣方支付，ETF 0.1%）
      - 滑價 ≈ 5–10 bps（流動性差更大）
    美股慣例：
      - 多數券商免手續費（0%）
      - SEC 費 + 交易活動費 ≈ 0.003%（極小可忽略）
      - 滑價 ≈ 1–3 bps
    """
    fee_bps: float = 14.25  # 手續費 bps（買 / 賣都收）
    tax_sell_bps_tw: float = 30.0  # 台股賣方證交稅 bps
    tax_sell_bps_us: float = 0.3
    slippage_bps: float = 5.0  # 進出場各被吃 5 bps


def default_tw_costs() -> TransactionCost:
    """台股小資族常見：券商打 5 折、零股交易低限免略，滑價 5 bps。"""
    return TransactionCost(fee_bps=14.25 * 0.5, tax_sell_bps_tw=30.0,
                           tax_sell_bps_us=0.3, slippage_bps=5.0)


def default_us_costs() -> TransactionCost:
    """美股零佣金：手續費 0、滑價 2 bps。"""
    return TransactionCost(fee_bps=0.0, tax_sell_bps_tw=30.0,
                           tax_sell_bps_us=0.3, slippage_bps=2.0)


def _is_tw(symbol: str) -> bool:
    return ".TW" in symbol or ".TWO" in symbol


def _buy_cost(symbol: str, qty: int, price: float, costs: TransactionCost) -> tuple[float, float]:
    """
    回傳 (實際付出現金, 純費用)。買入：滑價（買在較貴）+ 手續費。
    """
    if qty <= 0 or price <= 0:
        return 0.0, 0.0
    eff_price = price * (1.0 + costs.slippage_bps / 10000.0)
    notional = qty * eff_price
    fee = notional * (costs.fee_bps / 10000.0)
    return notional + fee, fee


def _sell_cost(symbol: str, qty: int, price: float, costs: TransactionCost) -> tuple[float, float]:
    """
    回傳 (實際拿到現金, 純費用)。賣出：滑價（賣在較便宜）+ 手續費 + 證交稅。
    """
    if qty <= 0 or price <= 0:
        return 0.0, 0.0
    eff_price = price * (1.0 - costs.slippage_bps / 10000.0)
    notional = qty * eff_price
    fee = notional * (costs.fee_bps / 10000.0)
    tax_bps = costs.tax_sell_bps_tw if _is_tw(symbol) else costs.tax_sell_bps_us
    tax = notional * (tax_bps / 10000.0)
    total_cost = fee + tax
    return notional - total_cost, total_cost


# ============== Walk-forward 快照 ==============


def _snapshot_at(symbol: str, enriched: pd.DataFrame, ts) -> dict | None:
    """
    把 enriched 截至 ts（含當日）後，取最後一根算成 snap dict 給 strategy.evaluate 用。
    這是 walk-forward 的核心：絕對不偷看 ts 之後的資訊。
    """
    if enriched is None or enriched.empty:
        return None
    sub = enriched.loc[:ts]
    if sub.empty:
        return None
    last = sub.iloc[-1]
    return {
        "market": "台股" if _is_tw(symbol) else "美股",
        "symbol": symbol,
        "close": float(last.get("close", np.nan)) if pd.notna(last.get("close")) else None,
        "ret_1d": float(last.get("ret_1d")) if pd.notna(last.get("ret_1d")) else None,
        "ma20": float(last.get("ma20")) if pd.notna(last.get("ma20")) else None,
        "ma50": float(last.get("ma50")) if pd.notna(last.get("ma50")) else None,
        "ma200": float(last.get("ma200")) if pd.notna(last.get("ma200")) else None,
        "rsi14": float(last.get("rsi14")) if pd.notna(last.get("rsi14")) else None,
        "macd_hist": float(last.get("macd_hist")) if pd.notna(last.get("macd_hist")) else None,
        "volume_ratio": float(last.get("volume_ratio")) if pd.notna(last.get("volume_ratio")) else None,
        "atr14": float(last.get("atr14")) if pd.notna(last.get("atr14")) else None,
        "atr14_pct": float(last.get("atr14_pct")) if pd.notna(last.get("atr14_pct")) else None,
        "vol_60d_ann": float(last.get("vol_60d_ann")) if pd.notna(last.get("vol_60d_ann")) else None,
        "mdd_60d": float(last.get("mdd_60d")) if pd.notna(last.get("mdd_60d")) else None,
        "dist_to_52w_high_pct": float(last.get("dist_to_52w_high_pct")) if pd.notna(last.get("dist_to_52w_high_pct")) else None,
        "dist_to_52w_low_pct": float(last.get("dist_to_52w_low_pct")) if pd.notna(last.get("dist_to_52w_low_pct")) else None,
        "rs_60d_pct": float(last.get("rs_60d_pct")) if pd.notna(last.get("rs_60d_pct")) else None,
        "day_close_loc": float(last.get("day_close_loc")) if pd.notna(last.get("day_close_loc")) else None,
        "bb_pct": float(last.get("bb_pct")) if pd.notna(last.get("bb_pct")) else None,
    }


# ============== Backtester ==============


@dataclass
class BacktestResult:
    nav: pd.Series  # 帳戶總值（含現金）
    benchmark_nav: pd.Series | None
    cagr: float
    sharpe: float
    mdd: float
    win_rate: float
    n_trades: int
    n_rebalances: int
    log: pd.DataFrame  # 事件記錄
    total_fees: float = 0.0  # 累計交易成本（手續費 + 證交稅 + 滑價）


def _annualize(daily_ret: pd.Series) -> tuple[float, float, float]:
    r = daily_ret.dropna()
    if r.empty:
        return 0.0, 0.0, 0.0
    cagr = (1 + r).prod() ** (252 / len(r)) - 1
    vol = r.std() * np.sqrt(252)
    sharpe = (r.mean() / r.std()) * np.sqrt(252) if r.std() > 0 else 0.0
    return float(cagr), float(vol), float(sharpe)


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    rmax = nav.cummax()
    dd = nav / rmax - 1.0
    return float(dd.min())


def quick_backtest(
    plan: AllocationPlan,
    daily_frames: dict[str, pd.DataFrame],
    rules: RebalanceRules,
    *,
    benchmark_close: pd.Series | None = None,
    allow_fractional: bool = True,
    strategy=None,                       # 若給，再平衡時 walk-forward 重新挑 Top N
    costs: TransactionCost | None = None,  # 交易成本；None → 依市場自動帶入
    candidate_pool: dict[str, pd.DataFrame] | None = None,  # walk-forward 換股的候選池（含 enriched 欄位）
) -> BacktestResult:
    """
    把 plan 套到各標的的歷史 OHLCV（daily_frames）上：
      1. 第 1 天按 plan 配置股數；其餘為現金
      2. 每天計算 NAV
      3. 套規則：個股停損/停利、組合回撤、組合大漲、時間例行
      4. 觸發 → 記錄事件、調整持股或現金
      5. 結束時計算績效

    *** Look-ahead 修正 ***
    過去版本在「例行再平衡」時直接套 plan.items 的權重，但那組權重是用「回測結束日」的 score 算出來
    的——對 backtest 而言相當於每次都偷看到未來的「今天」。新版改為：

      - 若有給 `strategy` 與 `candidate_pool`：每次再平衡時用「截至當天的 enriched」重新跑 strategy
        評分 → 重新挑 Top N → 重新分權重，完全不偷看 ts 之後的資料。
      - 若沒給 strategy：仍維持原 plan.items 的固定持股（不換股），但權重也會用「當天的 close」重排。

    daily_frames：每檔 symbol 對應的 OHLCV（含 close / atr14 欄位最佳；沒有就現算）。
    candidate_pool：walk-forward 換股時可挑選的標的（key=symbol, value=enriched DataFrame）；
                    若 None，則退回只在 plan.items 裡換權重。
    """
    items = plan.items
    if not items:
        return BacktestResult(pd.Series(dtype=float), None, 0, 0, 0, 0, 0, 0, pd.DataFrame())

    # 預設成本：依市場自動挑（plan 第一檔即可）
    if costs is None:
        first_sym = items[0].symbol
        costs = default_tw_costs() if _is_tw(first_sym) else default_us_costs()

    # walk-forward 換股的候選池：若使用者沒給就退回到原本的 plan.items（仍會用 walk-forward 重排權重）
    use_walk_forward_pick = strategy is not None and candidate_pool is not None and len(candidate_pool) > 0
    pool_frames: dict[str, pd.DataFrame] = candidate_pool if use_walk_forward_pick else {it.symbol: daily_frames.get(it.symbol) for it in items}
    pool_frames = {k: v for k, v in pool_frames.items() if v is not None and not v.empty}

    # 對齊時間軸：採「聯集」，這樣不同上市日的標的也能進回測；個別缺資料的天就 NaN 不交易
    all_indices: list[pd.DatetimeIndex] = []
    for sym, df in pool_frames.items():
        all_indices.append(df.index)
    if not all_indices:
        return BacktestResult(pd.Series(dtype=float), None, 0, 0, 0, 0, 0, 0, pd.DataFrame())
    common = all_indices[0]
    for idx in all_indices[1:]:
        common = common.union(idx)
    common = common.sort_values()
    if len(common) < 5:
        return BacktestResult(pd.Series(dtype=float), None, 0, 0, 0, 0, 0, 0, pd.DataFrame())

    # 收盤價/ATR 矩陣（columns=symbols, rows=日期）。先聯集 reindex，避免單檔交集後變空。
    close_mat = pd.DataFrame(index=common)
    atr_mat = pd.DataFrame(index=common)
    for sym, df in pool_frames.items():
        d = df.reindex(common)
        close_mat[sym] = pd.to_numeric(d["close"], errors="coerce")
        if "atr14" in d.columns:
            atr_mat[sym] = pd.to_numeric(d["atr14"], errors="coerce")
        else:
            atr_mat[sym] = close_mat[sym].rolling(14).std()
    close_mat = close_mat.ffill()
    atr_mat = atr_mat.ffill()

    # 持股 / 停損 / 停利 / 成本基礎 / 現金
    holdings: dict[str, int] = {sym: 0 for sym in pool_frames.keys()}
    stops: dict[str, float] = {sym: 0.0 for sym in pool_frames.keys()}
    tps: dict[str, float] = {sym: 0.0 for sym in pool_frames.keys()}
    initial_cash_basis: dict[str, float] = {sym: 0.0 for sym in pool_frames.keys()}
    cash = float(plan.goal.current_capital)
    target_n_positions = max(1, len([it for it in items if it.shares > 0]))

    nav_series = pd.Series(np.nan, index=common, dtype=float)
    high_water = plan.goal.current_capital
    last_rebalance_day = -1  # 用 -1 讓「第 0 天」就會建倉
    n_trades = 0
    n_rebalances = 0
    n_wins = 0
    closed_trades: list[dict] = []
    events: list[dict] = []
    total_fees: float = 0.0  # 累計交易成本

    def _walk_forward_topn(ts) -> list[tuple[str, float, float, float]]:
        """
        用「截至 ts 的 enriched」對候選池跑一次 strategy.evaluate，回傳 [(sym, score, close, atr14), ...]。
        若 strategy=None 或 candidate_pool=None：退回 plan.items 排序（仍用當日 close 做有效性過濾）。
        """
        results: list[tuple[str, float, float, float]] = []
        if use_walk_forward_pick:
            for sym, enr in pool_frames.items():
                snap = _snapshot_at(sym, enr, ts)
                if not snap or snap.get("close") in (None, 0):
                    continue
                try:
                    ev = strategy.evaluate(snap)
                    sc = float(ev.get("score", 0.0)) if isinstance(ev, dict) else 0.0
                except Exception:
                    sc = 0.0
                px = float(snap["close"])
                atr_v = float(snap.get("atr14") or 0.0)
                results.append((sym, sc, px, atr_v))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:target_n_positions]
        # 沒給 strategy → 維持 plan.items，但仍以當日 close/atr 為基準
        for it in items:
            sym = it.symbol
            px = float(close_mat.loc[ts, sym]) if (sym in close_mat.columns and pd.notna(close_mat.loc[ts, sym])) else 0.0
            atr_v = float(atr_mat.loc[ts, sym]) if (sym in atr_mat.columns and pd.notna(atr_mat.loc[ts, sym])) else 0.0
            if px <= 0:
                continue
            results.append((sym, float(it.score or 0.0), px, atr_v))
        return results

    def _portfolio_value(day_close: pd.Series) -> float:
        v = cash
        for sym, sh in holdings.items():
            if sh <= 0:
                continue
            px = day_close.get(sym, np.nan)
            if pd.notna(px):
                v += sh * float(px)
        return v

    def _sell(sym: str, qty: int, px: float, ts, reason: str, *, log_as_exit: bool) -> None:
        """通用賣出：扣手續費 + 證交稅 + 滑價，回收現金。"""
        nonlocal cash, n_trades, n_wins, total_fees
        if qty <= 0 or px <= 0:
            return
        proceeds, fee = _sell_cost(sym, qty, px, costs)
        cash += proceeds
        total_fees += fee
        basis = initial_cash_basis.get(sym, 0.0)
        # 賣的部分 basis 按比例攤
        sh_old = holdings.get(sym, 0)
        if sh_old > 0:
            partial_basis = basis * (qty / sh_old)
        else:
            partial_basis = 0.0
        pnl = proceeds - partial_basis
        if log_as_exit:
            n_trades += 1
            if pnl > 0:
                n_wins += 1
            closed_trades.append({"symbol": sym, "qty": qty, "proceeds": proceeds, "basis": partial_basis, "pnl": pnl, "fee": fee})
            events.append({"date": ts, "type": "exit", "symbol": sym, "reason": reason,
                           "price": px, "qty": qty, "pnl": pnl, "fee": fee})
        holdings[sym] = sh_old - qty
        initial_cash_basis[sym] = max(basis - partial_basis, 0.0)

    def _buy(sym: str, qty: int, px: float, ts, reason: str) -> None:
        """通用買進：扣手續費 + 滑價。"""
        nonlocal cash, total_fees
        if qty <= 0 or px <= 0:
            return
        outflow, fee = _buy_cost(sym, qty, px, costs)
        if outflow > cash:
            # 現金不夠 → 縮小到能買的數量（避免負現金）
            max_qty = int(cash // (px * (1.0 + costs.slippage_bps / 10000.0) * (1.0 + costs.fee_bps / 10000.0)))
            if max_qty <= 0:
                return
            qty = max_qty
            outflow, fee = _buy_cost(sym, qty, px, costs)
        cash -= outflow
        total_fees += fee
        holdings[sym] = holdings.get(sym, 0) + qty
        initial_cash_basis[sym] = initial_cash_basis.get(sym, 0.0) + outflow
        events.append({"date": ts, "type": "entry", "symbol": sym, "reason": reason,
                       "price": px, "qty": qty, "pnl": 0.0, "fee": fee})

    def _close_position(sym: str, px: float, reason: str, ts) -> None:
        sh = holdings.get(sym, 0)
        if sh <= 0:
            return
        _sell(sym, sh, px, ts, reason, log_as_exit=True)

    def _walk_forward_rebalance(day_close: pd.Series, ts, reason: str) -> None:
        """
        Walk-forward 再平衡：用「截至 ts 的 enriched」重新挑 Top N + 重新分權重，
        賣掉不在新名單裡的、買進新進的、調整既有部位。完全不偷看 ts 之後的資料。
        """
        nonlocal cash, n_rebalances
        nav_before = _portfolio_value(day_close)
        topn = _walk_forward_topn(ts)
        if not topn:
            return
        # 由 score 算權重（min-max + 上限 cap）
        scores = np.array([max(s, 0.0) for _, s, _, _ in topn], dtype=float)
        if scores.max() == scores.min():
            w = np.ones(len(topn)) / len(topn)
        else:
            adj = (scores - scores.min()) + 0.1
            w = adj / adj.sum()
        cap = plan.risk_profile.max_position_pct / 100.0
        w = np.minimum(w, cap)
        if w.sum() <= 0:
            w = np.ones(len(topn)) / len(topn)
        else:
            w = w / w.sum()
        target_weights = {sym: float(wi) for (sym, _, _, _), wi in zip(topn, w)}
        target_prices = {sym: px for sym, _, px, _ in topn}
        target_atrs = {sym: a for sym, _, _, a in topn}

        # 1) 不在新名單裡的全賣（記為 exit）
        for sym in list(holdings.keys()):
            if holdings[sym] > 0 and sym not in target_weights:
                px = float(day_close.get(sym, np.nan)) if pd.notna(day_close.get(sym, np.nan)) else 0.0
                if px > 0:
                    _close_position(sym, px, "再平衡-換股", ts)

        invest_total = nav_before * (1.0 - plan.risk_profile.cash_buffer_pct / 100.0)

        # 2) 既有持股縮量 / 新增持股加倉
        for sym, w_i in target_weights.items():
            px = float(target_prices.get(sym, 0.0))
            if px <= 0:
                continue
            target_notional = invest_total * w_i
            market = "台" if _is_tw(sym) else "美"
            target_qty = _round_shares(market, target_notional / px, allow_fractional=allow_fractional)
            cur_qty = holdings.get(sym, 0)
            diff = target_qty - cur_qty
            if diff > 0:
                _buy(sym, diff, px, ts, "再平衡-加碼")
            elif diff < 0:
                _sell(sym, -diff, px, ts, "再平衡-減碼", log_as_exit=False)

            # 重設停損 / 停利錨點為「再平衡日」
            atr_p = float(target_atrs.get(sym, 0.0))
            if atr_p > 0:
                stop = max(px - plan.risk_profile.atr_stop_mult * atr_p, 0.0)
            else:
                stop = px * (1 - 0.06)
            stops[sym] = stop
            tps[sym] = px + plan.risk_profile.take_profit_R * max(px - stop, 1e-9)

        n_rebalances += 1
        nav_after = _portfolio_value(day_close)
        events.append({"date": ts, "type": "rebalance", "symbol": "*", "reason": reason,
                       "price": nav_after, "qty": 0, "pnl": nav_after - nav_before, "fee": 0.0})

    def _trim_winners(day_close: pd.Series, ts, fraction: float = 1 / 3) -> None:
        """組合大漲：對所有持股賣 fraction，把錢轉現金（含交易成本）。"""
        nonlocal n_rebalances
        for sym in list(holdings.keys()):
            sh = holdings[sym]
            if sh <= 0:
                continue
            px = float(day_close.get(sym, np.nan)) if pd.notna(day_close.get(sym, np.nan)) else 0.0
            if px <= 0:
                continue
            cut = int(sh * fraction)
            if cut > 0:
                _sell(sym, cut, px, ts, "組合獲利-部分落袋", log_as_exit=False)
        n_rebalances += 1
        nav_after = _portfolio_value(day_close)
        events.append({"date": ts, "type": "trim_winners", "symbol": "*", "reason": "組合獲利",
                       "price": nav_after, "qty": 0, "pnl": 0.0, "fee": 0.0})

    def _cut_half(day_close: pd.Series, ts) -> None:
        """組合大回撤：對所有持股賣半，避免進一步損失（含交易成本）。"""
        nonlocal n_rebalances
        for sym in list(holdings.keys()):
            sh = holdings[sym]
            if sh <= 0:
                continue
            px = float(day_close.get(sym, np.nan)) if pd.notna(day_close.get(sym, np.nan)) else 0.0
            if px <= 0:
                continue
            cut = sh // 2
            if cut > 0:
                _sell(sym, cut, px, ts, "組合回撤-砍半", log_as_exit=False)
        n_rebalances += 1
        nav_after = _portfolio_value(day_close)
        events.append({"date": ts, "type": "cut_half", "symbol": "*", "reason": "組合回撤",
                       "price": nav_after, "qty": 0, "pnl": 0.0, "fee": 0.0})

    # 主迴圈
    for i, ts in enumerate(common):
        day_close = close_mat.loc[ts]

        # === 第 0 天：walk-forward 建倉（用 first_ts 當天可知的資訊評分） ===
        if i == 0:
            _walk_forward_rebalance(day_close, ts, "初始建倉")
            last_rebalance_day = 0
            nav = _portfolio_value(day_close)
            nav_series.loc[ts] = nav
            if nav > high_water:
                high_water = nav
            continue

        # 個股停損／停利
        if rules.stock_use_atr_stop or rules.stock_use_take_profit:
            for sym in list(holdings.keys()):
                if holdings[sym] <= 0:
                    continue
                px = day_close.get(sym, np.nan)
                if pd.isna(px):
                    continue
                px_v = float(px)
                if rules.stock_use_atr_stop and stops.get(sym) is not None and stops[sym] > 0 and px_v <= stops[sym]:
                    _close_position(sym, px_v, "ATR 停損", ts)
                elif rules.stock_use_take_profit and tps.get(sym) is not None and tps[sym] > 0 and px_v >= tps[sym]:
                    _close_position(sym, px_v, "停利達標", ts)

        nav = _portfolio_value(day_close)
        nav_series.loc[ts] = nav
        if nav > high_water:
            high_water = nav

        # 組合層級觸發（以重新評估後的 nav）
        if rules.portfolio_drawdown_pct is not None:
            dd_now = (nav / high_water - 1.0) * 100.0
            if dd_now <= -float(rules.portfolio_drawdown_pct):
                _cut_half(day_close, ts)
                # 砍完重置高點，避免一直被觸發
                high_water = _portfolio_value(day_close)

        if rules.portfolio_take_profit_pct is not None:
            up_now = (nav / plan.goal.current_capital - 1.0) * 100.0
            if up_now >= float(rules.portfolio_take_profit_pct):
                _trim_winners(day_close, ts)

        # 時間例行（walk-forward）
        if rules.rebalance_every_days and (i - last_rebalance_day) >= int(rules.rebalance_every_days):
            _walk_forward_rebalance(day_close, ts, "例行再平衡")
            last_rebalance_day = i

    nav_series = nav_series.dropna()
    cagr, vol, sharpe = _annualize(nav_series.pct_change())
    mdd = _max_drawdown(nav_series)
    win_rate = (n_wins / n_trades) if n_trades else 0.0

    bench_nav = None
    if benchmark_close is not None and not benchmark_close.empty:
        bench = pd.to_numeric(benchmark_close.reindex(nav_series.index).ffill(), errors="coerce").dropna()
        if not bench.empty:
            bench_nav = (bench / float(bench.iloc[0])) * float(nav_series.iloc[0])

    log_df = pd.DataFrame(events) if events else pd.DataFrame(columns=["date", "type", "symbol", "reason", "price", "qty", "pnl", "fee"])

    return BacktestResult(
        nav=nav_series, benchmark_nav=bench_nav,
        cagr=cagr, sharpe=sharpe, mdd=mdd, win_rate=win_rate,
        n_trades=n_trades, n_rebalances=n_rebalances, log=log_df,
        total_fees=float(total_fees),
    )


# ============== UI helpers ==============


def required_cagr_str(goal: Goal) -> str:
    r = goal.required_cagr
    if not np.isfinite(r):
        return "—"
    return f"{r * 100:+.2f}%"
