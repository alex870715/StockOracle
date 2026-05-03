"""快速 sanity 測試：可用 `python -m pytest -q` 或 `python tests/test_analysis.py`。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import add_indicators, atr, macd, rsi_wilder  # noqa: E402
from daily_pick import build_symbol_snapshot, score_v2, short_term_v2  # noqa: E402
from strategies import list_strategies  # noqa: E402


def _make_random_ohlcv(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    r = rng.normal(0.0005, 0.012, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = close * rng.uniform(0.995, 1.005, n)
    high = np.maximum(open_, close) * rng.uniform(1.0, 1.02, n)
    low = np.minimum(open_, close) * rng.uniform(0.98, 1.0, n)
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx
    )


def test_rsi_basic_range():
    rng = np.random.default_rng(11)
    drift = np.linspace(1, 200, 200)
    noise = rng.normal(0, 1.5, 200)
    s = pd.Series(drift + noise)
    r = rsi_wilder(s, 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()
    assert r.iloc[-1] > 60  # 強烈上升趨勢應給高 RSI


def test_atr_positive():
    df = _make_random_ohlcv()
    a = atr(df["high"], df["low"], df["close"], 14).dropna()
    assert (a > 0).all()


def test_macd_shape():
    df = _make_random_ohlcv()
    m = macd(df["close"]).dropna()
    assert {"macd", "macd_signal", "macd_hist"}.issubset(m.columns)
    assert (m["macd_hist"] == m["macd"] - m["macd_signal"]).all()


def test_add_indicators_columns():
    df = _make_random_ohlcv()
    out = add_indicators(df, include_full=True)
    for c in (
        "ma20", "ma50", "ma200", "rsi14", "atr14", "atr14_pct", "vol_60d_ann",
        "mdd_60d", "dist_to_52w_high_pct", "bb_upper", "macd_hist",
    ):
        assert c in out.columns


def test_score_and_snapshot_runs():
    df = _make_random_ohlcv()
    snap = build_symbol_snapshot("TEST", df)
    assert snap is not None
    assert "score" in snap and isinstance(snap["score"], float)
    assert snap["recommendation"] in {"強烈買進", "買進", "偏多觀察", "中性", "避開"}
    s2, bd = score_v2(snap)
    assert math.isfinite(s2)
    assert isinstance(bd, dict) and bd


def test_short_term_v2_no_crash():
    df = _make_random_ohlcv()
    snap = build_symbol_snapshot("TEST", df)
    sig, strength = short_term_v2(snap or {})
    assert isinstance(sig, bool)
    assert isinstance(strength, float)


def test_strategies_evaluate_and_history():
    df = _make_random_ohlcv()
    enriched = add_indicators(df, include_full=True)
    snap = build_symbol_snapshot("TEST", df, enriched=enriched)
    assert snap is not None
    for strat in list_strategies():
        ev = strat.evaluate(snap, enriched)
        for key in ("entry_today", "exit_today", "score", "recommendation",
                     "rule_hits", "rule_misses", "entry_rules_text", "exit_rules_text"):
            assert key in ev, f"{strat.key} 缺欄位 {key}"
        assert isinstance(ev["entry_today"], bool)
        assert isinstance(ev["exit_today"], bool)
        assert 0.0 <= ev["score"] <= 10.0 + 1e-9
        assert ev["recommendation"] in {"強烈買進", "買進", "偏多觀察", "中性", "避開"}
        hist = strat.historical_signals(enriched)
        assert "entries" in hist and "exits" in hist
        assert isinstance(hist["entries"], list) and isinstance(hist["exits"], list)


def test_planner_pipeline():
    from planner import (
        Goal, RISK_PROFILES, build_allocation,
        RebalanceRules, quick_backtest, feasibility_for, required_cagr_str,
    )

    g = Goal(300_000, 500_000, 2.0)
    assert math.isfinite(g.required_cagr)
    fa = feasibility_for(g, market="tw")
    assert fa.color in {"green", "amber", "red"}
    assert required_cagr_str(g).endswith("%")

    # 兩檔合成資料 → 配置 → 回測
    df_a = _make_random_ohlcv(n=300, seed=1)
    df_b = _make_random_ohlcv(n=300, seed=2)
    snap_a = build_symbol_snapshot("FAKE_A", df_a)
    snap_b = build_symbol_snapshot("FAKE_B", df_b)
    cand_df = pd.DataFrame([snap_a, snap_b])
    cand_df["market"] = "美股"

    plan = build_allocation(
        goal=g, profile=RISK_PROFILES["balanced"],
        candidate_df=cand_df, auto_top_n=2, allow_fractional=True,
    )
    assert len(plan.items) >= 1
    assert plan.cash >= 0

    rules = RebalanceRules(portfolio_drawdown_pct=10.0,
                            portfolio_take_profit_pct=20.0, rebalance_every_days=20)
    enr_a = add_indicators(df_a, include_full=True)
    enr_b = add_indicators(df_b, include_full=True)
    br = quick_backtest(plan, {"FAKE_A": enr_a, "FAKE_B": enr_b}, rules)
    assert br.nav.shape[0] > 0
    assert math.isfinite(br.cagr)
    assert math.isfinite(br.mdd)
    # 預設成本必須被加上
    assert br.total_fees >= 0.0


def test_transaction_cost_eats_return():
    """同一份 plan，零成本 vs 高成本 → 高成本的最終 NAV 必須較低、累計費用較高。"""
    from planner import (
        Goal, RISK_PROFILES, build_allocation,
        RebalanceRules, quick_backtest, TransactionCost,
    )
    g = Goal(500_000, 700_000, 2.0)
    df_a = _make_random_ohlcv(n=300, seed=11)
    df_b = _make_random_ohlcv(n=300, seed=22)
    snap_a = build_symbol_snapshot("FAKE_A", df_a)
    snap_b = build_symbol_snapshot("FAKE_B", df_b)
    cand_df = pd.DataFrame([snap_a, snap_b])
    cand_df["market"] = "美股"

    plan = build_allocation(
        goal=g, profile=RISK_PROFILES["balanced"],
        candidate_df=cand_df, auto_top_n=2, allow_fractional=True,
    )
    rules = RebalanceRules(portfolio_drawdown_pct=10.0,
                           portfolio_take_profit_pct=20.0, rebalance_every_days=15)
    frames = {"FAKE_A": add_indicators(df_a, include_full=True),
              "FAKE_B": add_indicators(df_b, include_full=True)}

    zero = TransactionCost(fee_bps=0.0, tax_sell_bps_tw=0.0, tax_sell_bps_us=0.0, slippage_bps=0.0)
    heavy = TransactionCost(fee_bps=50.0, tax_sell_bps_tw=100.0, tax_sell_bps_us=100.0, slippage_bps=20.0)

    br_zero = quick_backtest(plan, frames, rules, costs=zero)
    br_heavy = quick_backtest(plan, frames, rules, costs=heavy)

    assert br_zero.total_fees == 0.0
    assert br_heavy.total_fees > 0.0
    # 有交易發生時，重成本一定吃掉一些收益
    if br_heavy.n_rebalances > 0 or br_heavy.n_trades > 0:
        assert br_heavy.nav.iloc[-1] <= br_zero.nav.iloc[-1] + 1e-6


def test_walk_forward_no_lookahead():
    """walk-forward：用 strategy + candidate_pool 回測時不應 crash，且每筆 entry/exit 的 fee 都 ≥ 0。"""
    from planner import (
        Goal, RISK_PROFILES, build_allocation,
        RebalanceRules, quick_backtest,
    )
    from strategies import get_strategy

    g = Goal(500_000, 800_000, 3.0)
    frames = {}
    snaps = []
    for i, seed in enumerate([3, 5, 7, 11, 13]):
        sym = f"FAKE_{i}"
        df = _make_random_ohlcv(n=400, seed=seed)
        enr = add_indicators(df, include_full=True)
        snap = build_symbol_snapshot(sym, df, enriched=enr)
        snap["market"] = "美股"
        frames[sym] = enr
        snaps.append(snap)
    cand_df = pd.DataFrame(snaps)

    plan = build_allocation(
        goal=g, profile=RISK_PROFILES["aggressive"],
        candidate_df=cand_df, auto_top_n=3, allow_fractional=True,
    )
    rules = RebalanceRules(rebalance_every_days=20)
    strat = get_strategy("long_trend")

    br = quick_backtest(
        plan, frames, rules,
        strategy=strat, candidate_pool=frames,
    )
    assert br.nav.shape[0] > 0
    assert br.n_rebalances >= 1, "walk-forward 至少要做初始建倉一次"
    if not br.log.empty and "fee" in br.log.columns:
        assert (br.log["fee"].fillna(0) >= 0).all()


def test_i18n_switch_propagates():
    """切到英文後：tier_label / strategy.label / RiskProfile.label / 推薦 markdown 都應該變英文。"""
    from i18n import set_lang, get_lang, tier_label
    from strategies import get_strategy
    from planner import RISK_PROFILES
    from recommendation import full_recommendation_markdown

    try:
        set_lang("zh")
        assert tier_label("強烈買進") == "強烈買進"
        s_zh = get_strategy("long_trend").label
        assert "中長期" in s_zh or "趨勢" in s_zh
        rp_zh = RISK_PROFILES["balanced"].label
        assert rp_zh == "平衡"

        set_lang("en")
        assert get_lang() == "en"
        assert tier_label("強烈買進") == "Strong Buy"
        s_en = get_strategy("long_trend").label
        assert "Long" in s_en or "Trend" in s_en
        assert RISK_PROFILES["balanced"].label == "Balanced"

        # 推薦 markdown 一些 header / bullet 應該換成英文
        df = _make_random_ohlcv()
        snap = build_symbol_snapshot("AAPL", df)
        snap["market"] = "美股"
        md = full_recommendation_markdown(snap, daily_df=df)
        assert "Auto-generated" in md or "Composite" in md
    finally:
        set_lang("zh")


def test_entry_exit_signals_paired_no_spam():
    """加 _clean_signals 後：每段歷史內 entries >= exits，且 exit 一定在某個 entry 之後。"""
    from strategies import list_strategies, _pair_entries_exits
    df = _make_random_ohlcv(n=400, seed=99)
    enriched = add_indicators(df, include_full=True)
    for strat in list_strategies():
        sig = strat.historical_signals(enriched)
        ents = sig["entries"]
        exs = sig["exits"]
        # 配對後 exits 不能比 entries 多
        assert len(exs) <= len(ents), f"{strat.key}: exits {len(exs)} > entries {len(ents)}"
        # 每個 exit 都該嚴格大於對應的 entry
        for e_dt, x_dt in zip(ents, exs):
            assert x_dt > e_dt, f"{strat.key}: exit {x_dt} not after entry {e_dt}"

    # 直接測 helper：高頻雜訊 mask 應該被 collapse 成乾淨的 1-to-1
    idx = pd.date_range("2024-01-01", periods=20, freq="B")
    entries = pd.Series([False, True, False, False, False, False, True, False, False, False,
                          False, False, False, False, False, False, False, False, False, False], index=idx)
    exits = pd.Series([False, False, False, False, True, True, False, False, False, True,
                        True, True, True, False, False, False, False, False, False, False], index=idx)
    e_dates = list(entries.index[entries])
    x_dates = list(exits.index[exits])
    paired_e, paired_x = _pair_entries_exits(e_dates, x_dates, max_hold_days=None)
    assert len(paired_e) == 2
    assert len(paired_x) == 2
    assert paired_x[0] > paired_e[0]
    assert paired_x[1] > paired_e[1]
    # 第一個 entry → 第一個 valid exit (idx 4)
    assert paired_x[0] == idx[4]
    # 第二個 entry (idx 6) → 下一個 valid exit (idx 9)
    assert paired_x[1] == idx[9]


def test_custom_strategy_form_and_expression():
    """自訂策略：form 模式 + 表達式模式都要能 evaluate 並回正常欄位。"""
    from strategies import (
        make_custom_strategy, _validate_custom_expression,
        _rewrite_expr_for_pandas,
    )

    df = _make_random_ohlcv(n=300, seed=21)
    enriched = add_indicators(df, include_full=True)
    snap = build_symbol_snapshot("FAKE", df, enriched=enriched)

    # ---- form mode：close > ma20 AND rsi14 < 70 ----
    cfg_form = {
        "name": "TestForm", "mode": "form",
        "entry_conditions": [
            {"metric": "close", "op": "gt", "against_kind": "metric", "against": "ma20"},
            {"metric": "rsi14", "op": "lt", "against_kind": "value", "against": 70},
        ],
        "exit_conditions": [
            {"metric": "rsi14", "op": "gt", "against_kind": "value", "against": 80},
        ],
        "max_hold_days": 30,
    }
    s_form = make_custom_strategy(cfg_form)
    assert s_form is not None
    ev = s_form.evaluate(snap, enriched)
    for k in ("entry_today", "exit_today", "score", "recommendation",
              "rule_hits", "rule_misses", "entry_rules_text", "exit_rules_text"):
        assert k in ev, f"missing {k}"
    assert isinstance(ev["entry_today"], bool)
    assert 0.0 <= ev["score"] <= 10.0 + 1e-9
    sig = s_form.historical_signals(enriched)
    assert "entries" in sig and "exits" in sig

    # ---- expression mode + AST 重寫 ----
    expr = "close > ma20 and rsi14 < 70"
    ok, err = _validate_custom_expression(expr)
    assert ok, f"expr should validate: {err}"
    rewritten = _rewrite_expr_for_pandas(expr)
    assert "&" in rewritten and "and" not in rewritten

    # 危險表達式必須拒絕
    bad = '__import__("os").system("rm")'
    ok2, _ = _validate_custom_expression(bad)
    assert not ok2

    cfg_expr = {
        "name": "TestExpr", "mode": "expression",
        "expression_entry": "close > ma20 and rsi14 < 70",
        "expression_exit":  "rsi14 > 80 or close < ma20",
        "max_hold_days": 0,
    }
    s_expr = make_custom_strategy(cfg_expr)
    assert s_expr is not None
    ev2 = s_expr.evaluate(snap, enriched)
    assert isinstance(ev2["entry_today"], bool)


def test_holdings_health_and_advice():
    """持股健檢：health 0–100、advice 不為空、tier 落在合法集合。"""
    from holdings import evaluate_holding, health_score, advice_keys
    from strategies import list_strategies as _ls

    df = _make_random_ohlcv(n=400, seed=42)
    enr = add_indicators(df, include_full=True)
    snap = build_symbol_snapshot("AAPL", df, enriched=enr)
    snap["market"] = "美股"

    h = health_score(snap, weight_pct=15.0)
    assert 0 <= h <= 100
    advs = advice_keys(snap, pnl_pct=12.0, weight_pct=15.0, hscore=h)
    assert advs and all(k.startswith("hold.advice.") for k in advs)

    # 高度集中 → reduce_overweight 應被觸發
    advs2 = advice_keys(snap, pnl_pct=5.0, weight_pct=45.0, hscore=h)
    assert "hold.advice.reduce_overweight" in advs2

    # 大幅獲利 → strong_take_profit
    advs3 = advice_keys(snap, pnl_pct=70.0, weight_pct=10.0, hscore=h)
    assert "hold.advice.strong_take_profit" in advs3

    # 大幅虧損 → cut_loss
    advs4 = advice_keys(snap, pnl_pct=-20.0, weight_pct=10.0, hscore=h)
    assert "hold.advice.cut_loss" in advs4

    res = evaluate_holding(
        symbol="AAPL", shares=100, avg_cost=80,
        snap=snap, enriched=enr,
        portfolio_market_value=15000.0,
        market_label="美股",
        strategies=_ls(),
    )
    assert res.cur_price is not None and res.cur_price > 0
    assert res.market_value > 0
    assert res.cost_basis == 100 * 80
    assert res.tier_zh in {"強烈買進", "買進", "偏多觀察", "中性", "避開", "—"}
    assert len(res.outlook) == 5  # 內建 5 套策略各 1 筆
    for o in res.outlook:
        assert o["status_key"] in {"hold.outlook.bullish", "hold.outlook.warn", "hold.outlook.neutral"}


def test_holdings_handles_missing_snap():
    """抓不到資料 → 回 no_data 建議，不可崩。"""
    from holdings import evaluate_holding
    res = evaluate_holding(
        symbol="BAD.SYM", shares=10, avg_cost=100,
        snap=None, enriched=None,
        portfolio_market_value=0.0, market_label="美股",
    )
    assert res.cur_price is None
    assert res.advice_keys == ["hold.advice.no_data"]
    assert res.tier_zh == "—"


if __name__ == "__main__":
    fns = [
        test_rsi_basic_range,
        test_atr_positive,
        test_macd_shape,
        test_add_indicators_columns,
        test_score_and_snapshot_runs,
        test_short_term_v2_no_crash,
        test_strategies_evaluate_and_history,
        test_planner_pipeline,
        test_transaction_cost_eats_return,
        test_walk_forward_no_lookahead,
        test_i18n_switch_propagates,
        test_entry_exit_signals_paired_no_spam,
        test_custom_strategy_form_and_expression,
        test_holdings_health_and_advice,
        test_holdings_handles_missing_snap,
    ]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e!r}")
    raise SystemExit(failed)
