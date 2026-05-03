"""
StockOracle 網頁介面（v2）：

- 首次進入自動跑一次預設清單
- 頂部「重新計算」、欄位篩選與彩色排名
- 個股頁：K 線 + MA20/50/200 + 布林 + MACD + RSI + 短期訊號 + 基準對照 + log 切換
- 推薦解讀：多週期視角、停損／部位試算、失效條件、基本面
- 顯示美股／台股各自最後一根 K 的時間，與失敗清單
- 點表格列直接在「個股分析」開啟該檔

執行（於 StockOracle 目錄）：

    streamlit run app.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis import add_indicators  # noqa: E402
from charts import build_ohlcv_figure  # noqa: E402
from daily_pick import build_symbol_snapshot, run_full_report  # noqa: E402
from i18n import LANGS, get_lang, market_label, set_lang, t, tier_label  # noqa: E402
from data_loader import (  # noqa: E402
    clear_cache,
    fetch_fast_info,
    fetch_history,
    normalize_yahoo_symbol,
)
from holdings import (  # noqa: E402
    evaluate_holding,
    format_advice,
    format_outlook,
)
from planner import (  # noqa: E402
    RISK_PROFILES,
    TransactionCost,
    default_tw_costs,
    default_us_costs,
    Goal,
    RebalanceRules,
    build_allocation,
    feasibility_for,
    quick_backtest,
    required_cagr_str,
)
from recommendation import full_recommendation_markdown  # noqa: E402
from strategies import (  # noqa: E402
    _CUSTOM_ALLOWED_COLS,
    DEFAULT_STRATEGY_KEY,
    get_strategy,
    list_strategies,
    make_custom_strategy,
)
from symbol_meta import DISPLAY_MODES, format_symbol, reload_names  # noqa: E402
from universe import (  # noqa: E402
    UNIVERSE_SIZES,
    benchmark_for_symbol,
    has_full_market_data,
    universe,
)


# ----- 快取 -----


@st.cache_data(ttl=300, show_spinner=False)
def _cached_full_report(
    symbols_tuple: tuple[str, ...],
    period: str,
    cache_buster: int,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict]:
    return run_full_report(symbols=list(symbols_tuple), period=period)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_chart_bundle(symbol: str, period: str, cache_buster: int):
    raw = fetch_history(symbol, period=period)
    if raw is None or raw.empty:
        return None
    bench_sym = benchmark_for_symbol(symbol)
    bench_df = fetch_history(bench_sym, period=period)
    bench_close = bench_df["close"] if not bench_df.empty else None
    enriched = add_indicators(raw, bench_close=bench_close, include_full=True)
    snap = build_symbol_snapshot(symbol, raw, enriched=enriched, bench_close=bench_close)
    if snap is None:
        return None
    fast = fetch_fast_info(symbol)
    return raw, enriched, snap, bench_close, bench_sym, fast


# ----- 表格輔助 -----

_PCT_COLS = {"ret_1d", "mdd_60d", "mdd_252d", "vol_60d_ann", "dist_to_52w_high_pct", "rs_60d_pct", "rs_120d_pct"}
_TIER_ORDER = ["強烈買進", "買進", "偏多觀察", "中性", "避開"]  # 資料層 keys（中文）
_TIER_COLORS = {
    "強烈買進": "#1b5e20",
    "買進": "#2e7d32",
    "偏多觀察": "#388e3c",
    "中性": "#616161",
    "避開": "#b71c1c",
}


def _raw_col_labels() -> dict[str, str]:
    """依當前語言生成欄位中英對照（zh / en）。"""
    if get_lang() == "en":
        return {
            "market": "Market", "symbol": "Symbol", "as_of": "Date",
            "close": "Close", "ret_1d": "Today %", "ma20": "MA20", "ma50": "MA50", "ma200": "MA200",
            "rsi14": "RSI14", "macd_hist": "MACD-hist", "volume_ratio": "Vol Ratio",
            "atr14_pct": "ATR %", "vol_60d_ann": "60D Vol", "mdd_60d": "60D MDD",
            "dist_to_52w_high_pct": "vs 52W High %", "rs_60d_pct": "RS60 %", "rs_120d_pct": "RS120 %",
            "score": "Composite", "recommendation": "Tier",
            "short_term_signal": "ST", "short_term_score": "ST Strength",
        }
    return {
        "market": "市場", "symbol": "代號", "as_of": "資料日",
        "close": "收盤", "ret_1d": "今日%", "ma20": "MA20", "ma50": "MA50", "ma200": "MA200",
        "rsi14": "RSI14", "macd_hist": "MACD柱", "volume_ratio": "量比",
        "atr14_pct": "ATR%", "vol_60d_ann": "60D波動", "mdd_60d": "60D MDD",
        "dist_to_52w_high_pct": "距52W高%", "rs_60d_pct": "RS60%", "rs_120d_pct": "RS120%",
        "score": "綜合分數", "recommendation": "等級",
        "short_term_signal": "短期", "short_term_score": "短期強度",
    }


def _styled_table(df: pd.DataFrame, *, display_mode: str = "名稱 代號") -> "pd.io.formats.style.Styler":
    if df.empty:
        return df.style
    show = df.copy()
    if "symbol" in show.columns:
        show["symbol"] = show["symbol"].map(lambda s: format_symbol(s, display_mode))
    if "ret_1d" in show.columns:
        show["ret_1d"] = pd.to_numeric(show["ret_1d"], errors="coerce") * 100.0
    for c in ("mdd_60d", "mdd_252d", "vol_60d_ann"):
        if c in show.columns:
            show[c] = pd.to_numeric(show[c], errors="coerce") * 100.0
    if "short_term_signal" in show.columns:
        show["short_term_signal"] = show["short_term_signal"].map(lambda x: "✅" if x is True else "")
    if "market" in show.columns:
        show["market"] = show["market"].map(market_label)
    if "recommendation" in show.columns:
        show["recommendation_zh"] = show["recommendation"]  # 留 raw key 給樣式 lookup 用
        show["recommendation"] = show["recommendation"].map(tier_label)

    col_labels = _raw_col_labels()
    rename = {k: v for k, v in col_labels.items() if k in show.columns}
    show = show.rename(columns=rename)
    tier_col = col_labels["recommendation"]
    score_col = col_labels["score"]
    vr_col = col_labels["volume_ratio"]
    pct_col_keys = ["ret_1d", "rs_60d_pct", "rs_120d_pct", "dist_to_52w_high_pct"]

    fmt: dict[str, str] = {}
    pct_label_set = {col_labels[k] for k in ("ret_1d", "mdd_60d", "vol_60d_ann", "dist_to_52w_high_pct", "rs_60d_pct", "rs_120d_pct", "atr14_pct")}
    price_label_set = {col_labels[k] for k in ("close", "ma20", "ma50", "ma200", "macd_hist")}
    decim2_label_set = {col_labels[k] for k in ("rsi14", "volume_ratio")}
    score_label_set = {col_labels[k] for k in ("score", "short_term_score")}
    for col in show.columns:
        if col in pct_label_set:
            fmt[col] = "{:+.2f}%"
        elif col in price_label_set:
            fmt[col] = "{:.2f}"
        elif col in decim2_label_set:
            fmt[col] = "{:.2f}"
        elif col in score_label_set:
            fmt[col] = "{:.2f}"

    def _color_tier(v):
        # v 可能是已翻譯的 "Strong Buy"，先反查回 zh 取色
        zh_key = next((k for k, en in [
            ("強烈買進", "Strong Buy"), ("買進", "Buy"), ("偏多觀察", "Watch (bullish)"),
            ("中性", "Neutral"), ("避開", "Avoid"), ("減碼", "Reduce")
        ] if en == str(v)), str(v))
        return f"background-color: {_TIER_COLORS.get(zh_key, 'transparent')}; color: white; font-weight: 600;"

    def _color_pct(v):
        try:
            x = float(v)
        except Exception:
            return ""
        if pd.isna(x):
            return ""
        if x > 0:
            return "color: #66bb6a;"
        if x < 0:
            return "color: #ef5350;"
        return ""

    if "recommendation_zh" in show.columns:
        show = show.drop(columns=["recommendation_zh"])
    styler = show.style.format(fmt, na_rep="—")

    def _apply_map(st_, fn, subset):
        m = getattr(st_, "map", None)
        return m(fn, subset=subset) if m else st_.applymap(fn, subset=subset)

    if tier_col in show.columns:
        styler = _apply_map(styler, _color_tier, [tier_col])

    pct_cols_in = [col_labels[k] for k in pct_col_keys if col_labels[k] in show.columns]
    if pct_cols_in:
        styler = _apply_map(styler, _color_pct, pct_cols_in)

    def _gradient(values: pd.Series, base_rgb: tuple[int, int, int]) -> list[str]:
        s = pd.to_numeric(values, errors="coerce")
        if s.dropna().empty:
            return ["" for _ in values]
        lo, hi = float(s.min()), float(s.max())
        rng = max(hi - lo, 1e-9)
        out = []
        for v in s:
            if pd.isna(v):
                out.append("")
                continue
            tt = max(0.0, min(1.0, (float(v) - lo) / rng))
            r, g, b = base_rgb
            out.append(f"background-color: rgba({r}, {g}, {b}, {0.15 + 0.55 * tt:.2f});")
        return out

    if score_col in show.columns:
        styler = styler.apply(lambda s: _gradient(s, (102, 187, 106)), subset=[score_col])
    if vr_col in show.columns:
        styler = styler.apply(lambda s: _gradient(s, (255, 167, 38)), subset=[vr_col])
    return styler


def _parse_custom_symbols(text: str) -> list[str] | None:
    t = (text or "").strip()
    if not t:
        return None
    return [s.strip().upper() for s in t.replace("，", ",").split(",") if s.strip()]


def _resolve_symbols(market_key: str, size: str, custom_text: str) -> tuple[list[str], str]:
    custom = _parse_custom_symbols(custom_text)
    if custom:
        return [normalize_yahoo_symbol(s) for s in custom], t("source.custom")
    syms = universe(market_key, size)
    label_map_zh = {"all": "全部市場", "us": "美股", "tw": "台股"}
    label_map_en = {"all": "All markets", "us": "US", "tw": "TW"}
    label = (label_map_en if get_lang() == "en" else label_map_zh).get(market_key, "default")
    return syms, f"{label}・{size}"


# ----- 資產規劃分頁 -----


@st.cache_data(ttl=300, show_spinner=False)
def _planner_load_frames(symbols_tuple: tuple[str, ...], period: str, cache_buster: int) -> dict:
    """為規劃頁的回測撈每檔的 enriched DataFrame。重用主清單的快取機制。"""
    bench_us_df = fetch_history("^GSPC", period=period)
    bench_tw_df = fetch_history("^TWII", period=period)
    bench_us = bench_us_df["close"] if not bench_us_df.empty else None
    bench_tw = bench_tw_df["close"] if not bench_tw_df.empty else None
    out: dict[str, pd.DataFrame] = {}
    for s in symbols_tuple:
        raw = fetch_history(s, period=period)
        if raw is None or raw.empty:
            continue
        bench = bench_tw if (".TW" in s or ".TWO" in s) else bench_us
        out[s] = add_indicators(raw, bench_close=bench, include_full=True)
    return {"frames": out, "bench_us": bench_us, "bench_tw": bench_tw}


def _planner_format_money(x: float) -> str:
    return f"${x:,.0f}"


def _render_planner_tab(all_df: pd.DataFrame, period: str, dmode: str, active_strategy) -> None:
    if all_df.empty:
        st.warning(t("plan.no_candidates"))
        return

    st.markdown(t("plan.section_a"))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cur_cap = st.number_input(t("plan.cur_cap"), min_value=10_000, value=300_000, step=10_000, key="plan_cur")
    with c2:
        tgt_cap = st.number_input(t("plan.tgt_cap"), min_value=10_000, value=500_000, step=10_000, key="plan_tgt")
    with c3:
        horizon = st.number_input(t("plan.horizon"), min_value=0.25, value=2.0, step=0.25, key="plan_horizon")
    with c4:
        market_options = ["台", "美"]
        market_for_base = st.radio(
            t("plan.market_base"), market_options,
            horizontal=True, key="plan_mkt",
            format_func=lambda x: ("TW" if x == "台" else "US") if get_lang() == "en" else x,
        )

    goal = Goal(current_capital=float(cur_cap), target_capital=float(tgt_cap), horizon_years=float(horizon))
    fa = feasibility_for(goal, market="tw" if market_for_base == "台" else "us")
    cag1, cag2 = st.columns([1, 3])
    cag1.metric(t("plan.required_cagr"), required_cagr_str(goal))
    box = {"green": st.success, "amber": st.warning, "red": st.error}.get(fa.color, st.info)
    box(f"**{fa.verdict}** — {fa.note}")

    st.markdown("---")
    st.markdown(t("plan.section_b"))

    cR1, cR2 = st.columns([1, 3])
    with cR1:
        profile_key = st.radio(
            t("plan.risk_profile"),
            options=list(RISK_PROFILES.keys()),
            format_func=lambda k: RISK_PROFILES[k].label,
            index=1,
            key="plan_profile",
        )
    profile = RISK_PROFILES[profile_key]
    with cR2:
        st.caption(t(
            "plan.profile_caption",
            label=profile.label, cb=profile.cash_buffer_pct, n=profile.max_positions,
            cap=profile.max_position_pct, rt=profile.risk_per_trade_pct,
            sm=profile.atr_stop_mult, tp=profile.take_profit_R,
        ))
        st.caption(profile.note)

    cM1, cM2, cM3 = st.columns([1, 2, 1])
    pick_options = ["自動 Top N", "手動勾選"]
    pick_label_map = {"自動 Top N": t("plan.pick_auto"), "手動勾選": t("plan.pick_manual")}
    with cM1:
        pick_mode = st.radio(
            t("plan.pick_mode"), pick_options,
            horizontal=True, key="plan_pick_mode",
            format_func=lambda x: pick_label_map[x],
        )
    with cM2:
        if pick_mode == "自動 Top N":
            cur_topn = st.session_state.get("plan_topn", min(6, profile.max_positions))
            top_n = st.slider(
                t("plan.top_n"),
                min_value=2,
                max_value=profile.max_positions,
                value=min(cur_topn, profile.max_positions),
                key="plan_topn",
            )
            manual_picks = None
        else:
            sym_all = all_df["symbol"].tolist()
            mc1, mc2 = st.columns([6, 1])
            with mc1:
                manual_picks = st.multiselect(
                    t("plan.manual_pick"),
                    options=sym_all,
                    format_func=lambda s: format_symbol(s, dmode),
                    key="plan_manual_pick",
                )
            with mc2:
                st.caption(" ")
                if st.button(t("plan.clear_picks_btn"), help=t("plan.clear_picks_help")):
                    st.session_state["plan_manual_pick"] = []
                    st.rerun()
            cnt = len(manual_picks)
            cap = profile.max_positions
            if cnt > cap:
                st.warning(t("plan.over_cap_warn", cnt=cnt, label=profile.label, cap=cap))
                manual_picks = manual_picks[:cap]
            else:
                st.caption(t("plan.picked_count", cnt=cnt, cap=cap))
            top_n = profile.max_positions
    with cM3:
        allow_fractional = st.checkbox(
            t("plan.allow_fractional"),
            value=True,
            help=t("plan.allow_fractional_help"),
            key="plan_fractional",
        )

    plan = build_allocation(
        goal=goal, profile=profile, candidate_df=all_df,
        manual_picks=manual_picks if pick_mode == "手動勾選" else None,
        auto_top_n=int(top_n), allow_fractional=allow_fractional,
    )
    st.caption(plan.note)

    if not plan.items:
        st.warning(t("plan.empty_alloc_warn"))
        return

    # 配置表格
    show_df = plan.to_dataframe().copy()
    show_df.insert(0, t("plan.col.display"), [format_symbol(s, dmode) for s in show_df["symbol"]])
    col_rename = {
        "weight_pct": t("plan.col.weight"),
        "shares": t("plan.col.shares"),
        "notional": t("plan.col.notional"),
        "stop_price": t("plan.col.stop"),
        "take_profit_price": t("plan.col.tp"),
        "atr_pct": t("plan.col.atr_pct"),
        "risk_dollar": t("plan.col.risk"),
        "score": t("plan.col.score"),
    }
    show_df = show_df.rename(columns=col_rename)
    fmt_map = {
        col_rename["weight_pct"]: "{:.2f}%",
        col_rename["notional"]: "{:,.0f}",
        col_rename["stop_price"]: "{:.2f}",
        col_rename["take_profit_price"]: "{:.2f}",
        col_rename["atr_pct"]: "{:.2f}%",
        col_rename["risk_dollar"]: "{:,.0f}",
        col_rename["score"]: "{:.2f}",
    }
    st.dataframe(show_df.drop(columns=["symbol"]).style.format(fmt_map, na_rep="—"),
                 width="stretch", hide_index=True)

    cP1, cP2, cP3, cP4 = st.columns(4)
    cP1.metric(t("plan.metric.invested"), _planner_format_money(plan.total_notional))
    cP2.metric(t("plan.metric.cash"), _planner_format_money(plan.cash))
    cP3.metric(t("plan.metric.total_risk"), _planner_format_money(plan.total_risk),
               help=t("plan.metric.total_risk_help"))
    cP4.metric(t("plan.metric.n_pos"),
               f"{len(plan.items)} " + ("" if get_lang() == "en" else "檔"))

    st.markdown("---")
    st.markdown(t("plan.section_c"))

    cE1, cE2, cE3 = st.columns(3)
    with cE1:
        st.markdown(t("plan.rb_stock"))
        rb_atr = st.checkbox(t("plan.rb_stock_atr"), value=True, key="plan_rb_atr")
        rb_tp = st.checkbox(t("plan.rb_stock_tp", r=profile.take_profit_R), value=True, key="plan_rb_tp")
    with cE2:
        st.markdown(t("plan.rb_port"))
        rb_dd_on = st.checkbox(t("plan.rb_port_dd"), value=True, key="plan_rb_dd_on")
        rb_dd_pct = st.slider(t("plan.rb_port_dd_pct"), 3.0, 25.0, 8.0, step=0.5, key="plan_rb_dd",
                              disabled=not rb_dd_on, help=t("plan.rb_port_dd_help"))
        rb_tp_on = st.checkbox(t("plan.rb_port_tp"), value=True, key="plan_rb_tp_on")
        rb_tp_pct = st.slider(t("plan.rb_port_tp_pct"), 5.0, 60.0, 20.0, step=1.0, key="plan_rb_tp_pct",
                              disabled=not rb_tp_on, help=t("plan.rb_port_tp_help"))
    with cE3:
        st.markdown(t("plan.rb_time"))
        rb_time_on = st.checkbox(t("plan.rb_time_on"), value=True, key="plan_rb_time_on")
        rb_time_n = st.slider(t("plan.rb_time_n"), 5, 90, 30, step=5, key="plan_rb_time",
                              disabled=not rb_time_on, help=t("plan.rb_time_n_help"))

    rules = RebalanceRules(
        stock_use_atr_stop=rb_atr,
        stock_use_take_profit=rb_tp,
        portfolio_drawdown_pct=float(rb_dd_pct) if rb_dd_on else None,
        portfolio_take_profit_pct=float(rb_tp_pct) if rb_tp_on else None,
        rebalance_every_days=int(rb_time_n) if rb_time_on else None,
    )

    st.markdown("---")
    st.markdown(t("plan.section_d"))
    is_tw_mkt = (market_for_base == "台")
    default_costs = default_tw_costs() if is_tw_mkt else default_us_costs()
    cF1, cF2, cF3, cF4 = st.columns(4)
    with cF1:
        fee_bps = st.number_input(
            t("plan.fee_bps"), min_value=0.0, max_value=50.0,
            value=float(default_costs.fee_bps), step=0.5,
            help=t("plan.fee_bps_help"),
            key="plan_fee_bps",
        )
    with cF2:
        tax_default = default_costs.tax_sell_bps_tw if is_tw_mkt else default_costs.tax_sell_bps_us
        tax_bps = st.number_input(
            t("plan.tax_bps_tw") if is_tw_mkt else t("plan.tax_bps_us"),
            min_value=0.0, max_value=50.0, value=float(tax_default), step=0.5,
            help=t("plan.tax_bps_help"),
            key="plan_tax_bps",
        )
    with cF3:
        slip_bps = st.number_input(
            t("plan.slip_bps"), min_value=0.0, max_value=30.0,
            value=float(default_costs.slippage_bps), step=0.5,
            help=t("plan.slip_bps_help"),
            key="plan_slip_bps",
        )
    with cF4:
        use_walk_forward = st.checkbox(
            t("plan.use_wf"), value=True,
            help=t("plan.use_wf_help"),
            key="plan_use_wf",
        )
    st.caption(t("plan.adjust_caption"))

    if use_walk_forward:
        cP1, cP2 = st.columns([1, 3])
        with cP1:
            pool_size = st.slider(
                t("plan.pool_size"), min_value=max(5, len(plan.items)),
                max_value=min(60, max(8, len(all_df))), value=min(20, len(all_df)),
                key="plan_pool_size",
                help=t("plan.pool_size_help"),
            )
        with cP2:
            st.caption(t("plan.pool_size_caption", n=pool_size))
    else:
        pool_size = len(plan.items)

    costs = TransactionCost(
        fee_bps=float(fee_bps),
        tax_sell_bps_tw=float(tax_bps) if is_tw_mkt else default_costs.tax_sell_bps_tw,
        tax_sell_bps_us=float(tax_bps) if not is_tw_mkt else default_costs.tax_sell_bps_us,
        slippage_bps=float(slip_bps),
    )

    st.markdown("---")
    st.markdown(t("plan.section_e"))

    import hashlib
    plan_sig_str = (
        f"{cur_cap}|{tgt_cap}|{horizon}|{profile_key}|{pick_mode}|{top_n}|"
        f"{','.join(it.symbol for it in plan.items)}|{rb_atr}|{rb_tp}|{rb_dd_on}|{rb_dd_pct}|"
        f"{rb_tp_on}|{rb_tp_pct}|{rb_time_on}|{rb_time_n}|{fee_bps}|{tax_bps}|{slip_bps}|"
        f"{use_walk_forward}|{pool_size}|{period}|{allow_fractional}"
    )
    plan_sig = hashlib.md5(plan_sig_str.encode()).hexdigest()

    bt_run = st.button(t("plan.run_btn"), type="primary", key="plan_run_bt")
    if bt_run:
        plan_syms = [it.symbol for it in plan.items]
        ranked_syms = all_df["symbol"].tolist()[:pool_size] if use_walk_forward else plan_syms
        pool_syms = list(dict.fromkeys(plan_syms + ranked_syms))

        with st.spinner(t("plan.spinner_run", n=len(pool_syms))):
            cache_bust = st.session_state.get("cache_buster", 0)
            data_bundle = _planner_load_frames(tuple(pool_syms), period, cache_bust)
            frames = data_bundle["frames"]
            first_sym = plan.items[0].symbol
            bench_close = data_bundle["bench_tw"] if (".TW" in first_sym or ".TWO" in first_sym) else data_bundle["bench_us"]
            cand_pool = frames if use_walk_forward else None
            br = quick_backtest(
                plan, frames, rules,
                benchmark_close=bench_close,
                allow_fractional=allow_fractional,
                strategy=active_strategy,
                costs=costs,
                candidate_pool=cand_pool,
            )
        st.session_state["plan_bt"] = {
            "result": br,
            "bench_label": "^TWII" if (".TW" in first_sym or ".TWO" in first_sym) else "^GSPC",
            "period": period,
            "sig": plan_sig,
            "pool_size": len(pool_syms),
            "wf": use_walk_forward,
            "strategy": active_strategy.label,
        }

    bt = st.session_state.get("plan_bt")
    if bt and bt.get("sig") and bt["sig"] != plan_sig:
        st.warning(t("plan.stale_warn"))
    if bt and not bt["result"].nav.empty:
        br = bt["result"]
        bench_label = bt["bench_label"]
        cBT1, cBT2, cBT3, cBT4, cBT5, cBT6 = st.columns(6)
        cBT1.metric(t("plan.bt.end_nav"), _planner_format_money(br.nav.iloc[-1]),
                    delta=f"{(br.nav.iloc[-1] / br.nav.iloc[0] - 1) * 100:+.2f}%")
        cBT2.metric(t("plan.bt.cagr"), f"{br.cagr * 100:+.2f}%",
                    help=t("plan.bt.cagr_help"))
        cBT3.metric(t("plan.bt.sharpe"), f"{br.sharpe:.2f}")
        cBT4.metric(t("plan.bt.mdd"), f"{br.mdd * 100:.2f}%")
        cBT5.metric(t("plan.bt.trades_winrate"), f"{br.n_trades} / {br.win_rate * 100:.0f}%")
        cBT6.metric(t("plan.bt.fees"), _planner_format_money(getattr(br, "total_fees", 0.0)),
                    help=t("plan.bt.fees_help"))

        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=br.nav.index, y=br.nav.values,
                                 name=t("plan.bt.nav_label"), line=dict(color="#26a69a", width=2)))
        if br.benchmark_nav is not None and not br.benchmark_nav.empty:
            fig.add_trace(go.Scatter(x=br.benchmark_nav.index, y=br.benchmark_nav.values,
                                     name=t("plan.bt.bench_label", label=bench_label),
                                     line=dict(color="#ff8a65", width=1.5, dash="dash")))
        fig.add_hline(y=br.nav.iloc[0], line_dash="dot", line_color="rgba(200,200,200,0.5)",
                      annotation_text=t("plan.bt.start_annot", money=_planner_format_money(br.nav.iloc[0])))
        fig.update_layout(
            template="plotly_dark", height=380,
            paper_bgcolor="#131722", plot_bgcolor="#131722",
            margin=dict(l=52, r=52, t=40, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            yaxis_title=t("plan.bt.yaxis"),
        )
        st.plotly_chart(fig, use_container_width=True, config=_plotly_config(_is_likely_mobile()))

        if not br.log.empty:
            with st.expander(t("plan.bt.event_log", n=len(br.log)), expanded=False):
                show_log = br.log.copy()
                if "date" in show_log.columns:
                    show_log["date"] = pd.to_datetime(show_log["date"]).dt.date
                st.dataframe(show_log, width="stretch", hide_index=True)
        st.caption(t(
            "plan.bt.period_caption",
            start=br.nav.index[0].date(), end=br.nav.index[-1].date(), n=len(br.nav),
        ))
    elif bt:
        st.warning(t("plan.bt.empty_warn"))


# ----- 自訂策略 UI -----


def _render_custom_strategy_form():
    """sidebar 內的自訂策略 expander；建構 CustomStrategy 並回傳（無效 → None）。"""
    op_keys = ["gt", "gte", "lt", "lte", "eq", "cross_above", "cross_below"]
    op_label_keys = {
        "gt": "custom.op.gt", "gte": "custom.op.gte",
        "lt": "custom.op.lt", "lte": "custom.op.lte",
        "eq": "custom.op.eq",
        "cross_above": "custom.op.cross_above",
        "cross_below": "custom.op.cross_below",
    }

    if "custom_cfg" not in st.session_state:
        st.session_state["custom_cfg"] = {
            "name": "",
            "mode": "form",
            "entry_conditions": [],
            "exit_conditions": [],
            "expression_entry": "",
            "expression_exit": "",
            "max_hold_days": 0,
        }
    cfg = st.session_state["custom_cfg"]

    with st.expander(t("custom.section"), expanded=False):
        st.caption(t("custom.section_caption"))
        cfg["name"] = st.text_input(
            t("custom.name"),
            value=cfg.get("name", "") or t("custom.preview_label"),
            key="_custom_name",
        )

        # 模式
        mode = st.radio(
            t("custom.mode"),
            options=["form", "expression"],
            index=0 if cfg.get("mode", "form") == "form" else 1,
            format_func=lambda m: t("custom.mode_form") if m == "form" else t("custom.mode_expr"),
            horizontal=True,
            key="_custom_mode",
        )
        cfg["mode"] = mode

        if mode == "form":
            _render_cond_block(
                cfg, "entry_conditions",
                title=t("custom.entry_block"),
                add_btn_label=t("custom.add_entry"),
                op_keys=op_keys, op_label_keys=op_label_keys,
            )
            _render_cond_block(
                cfg, "exit_conditions",
                title=t("custom.exit_block"),
                add_btn_label=t("custom.add_exit"),
                op_keys=op_keys, op_label_keys=op_label_keys,
            )
        else:
            cfg["expression_entry"] = st.text_area(
                t("custom.expr_entry"),
                value=cfg.get("expression_entry", ""),
                height=68,
                key="_custom_expr_entry",
            )
            cfg["expression_exit"] = st.text_area(
                t("custom.expr_exit"),
                value=cfg.get("expression_exit", ""),
                height=68,
                key="_custom_expr_exit",
            )
            st.caption(t("custom.expr_help"))

        cfg["max_hold_days"] = int(st.number_input(
            t("custom.max_hold"),
            min_value=0, max_value=365,
            value=int(cfg.get("max_hold_days", 0)),
            step=1, key="_custom_max_hold",
        ))

    # 建構 strategy
    try:
        strat = make_custom_strategy(cfg)
    except ValueError as e:
        with st.sidebar:
            st.error(t("custom.expr_invalid", err=str(e)))
        return None
    if strat is None:
        return None
    return strat


def _render_cond_block(
    cfg: dict, key: str, *,
    title: str, add_btn_label: str,
    op_keys: list[str], op_label_keys: dict,
) -> None:
    """條件列：每列一個 select(metric) + select(op) + radio(value/metric) + input。"""
    st.markdown(f"**{title}**")
    conds: list = cfg.get(key) or []
    new_conds: list = []
    metric_choices = list(_CUSTOM_ALLOWED_COLS)

    to_remove = -1
    for i, c in enumerate(conds):
        c = dict(c)  # 防止直接寫 session
        cols = st.columns([1.4, 1, 1.6, 0.4])
        with cols[0]:
            c["metric"] = st.selectbox(
                t("custom.metric"), metric_choices,
                index=metric_choices.index(c.get("metric", "close")) if c.get("metric") in metric_choices else 0,
                key=f"_cust_{key}_metric_{i}",
                label_visibility="collapsed",
            )
        with cols[1]:
            c["op"] = st.selectbox(
                t("custom.op"), op_keys,
                index=op_keys.index(c.get("op", "gt")) if c.get("op") in op_keys else 0,
                format_func=lambda k: t(op_label_keys[k]),
                key=f"_cust_{key}_op_{i}",
                label_visibility="collapsed",
            )
        with cols[2]:
            kind = st.radio(
                t("custom.against"),
                options=["value", "metric"],
                index=0 if c.get("against_kind", "value") == "value" else 1,
                format_func=lambda k: t("custom.against_value") if k == "value" else t("custom.against_metric"),
                horizontal=True,
                key=f"_cust_{key}_kind_{i}",
                label_visibility="collapsed",
            )
            c["against_kind"] = kind
            if kind == "value":
                c["against"] = float(st.number_input(
                    t("custom.value"),
                    value=float(c.get("against") or 0.0),
                    step=1.0,
                    key=f"_cust_{key}_val_{i}",
                    label_visibility="collapsed",
                ))
            else:
                default_m = c.get("against") if c.get("against") in metric_choices else "ma20"
                c["against"] = st.selectbox(
                    t("custom.value"), metric_choices,
                    index=metric_choices.index(default_m),
                    key=f"_cust_{key}_metric2_{i}",
                    label_visibility="collapsed",
                )
        with cols[3]:
            if st.button(t("custom.remove"), key=f"_cust_{key}_rm_{i}", use_container_width=True):
                to_remove = i
        new_conds.append(c)

    if to_remove >= 0:
        new_conds.pop(to_remove)

    if st.button(add_btn_label, key=f"_cust_{key}_add", use_container_width=True):
        new_conds.append({
            "metric": "close", "op": "gt",
            "against_kind": "metric", "against": "ma20",
        })

        cfg[key] = new_conds


# ----- Streamlit 版本相容（持股健檢／舊版無 width=stretch） -----


def _data_editor_stretch(
    data: pd.DataFrame,
    *,
    column_config=None,
    hide_index: bool = False,
    num_rows: str | None = None,
    key: str | None = None,
) -> pd.DataFrame:
    """舊版 Streamlit 不支援 width='stretch' 時退回 use_container_width。"""
    kw: dict = dict(
        data=data,
        hide_index=hide_index,
        num_rows=num_rows,
        column_config=column_config or {},
        key=key,
    )
    try:
        return st.data_editor(**kw, width="stretch")
    except TypeError:
        return st.data_editor(**kw, use_container_width=True)


# ----- 持股健檢 tab -----


@st.cache_data(ttl=300, show_spinner=False)
def _cached_holding_data(symbol: str, period: str, cache_buster: int):
    """單檔抓 history + indicators + snap，給持股健檢用。回 (snap, enriched) 或 None。"""
    raw = fetch_history(symbol, period=period)
    if raw is None or raw.empty:
        return None
    bench_sym = benchmark_for_symbol(symbol)
    bench_df = fetch_history(bench_sym, period=period)
    bench_close = bench_df["close"] if bench_df is not None and not bench_df.empty else None
    enriched = add_indicators(raw, bench_close=bench_close, include_full=True)
    snap = build_symbol_snapshot(symbol, raw, enriched=enriched, bench_close=bench_close)
    if snap is None:
        return None
    return snap, enriched


def _render_holdings_tab(period: str, dmode: str) -> None:
    st.subheader(t("hold.section_title"))
    st.caption(t("hold.intro"))

    # ---- 持股編輯（session state）----
    if "holdings_df" not in st.session_state:
        st.session_state["holdings_df"] = pd.DataFrame(
            {"symbol": [], "shares": [], "avg_cost": [], "note": []}
        )
    if "holdings_cash" not in st.session_state:
        st.session_state["holdings_cash"] = 0.0

    cIO1, cIO2, cIO3 = st.columns([1.3, 1.3, 1])
    with cIO1:
        up = st.file_uploader(
            t("hold.import_csv"), type=["csv"],
            help=t("hold.import_help"), key="_hold_csv_up",
        )
        # 不可用 session_state["_hold_csv_up"]=None — 這是 widget key，會直接觸發 StreamlitAPIException
        # 改用「確認匯入」按鈕，只在點擊時讀取 bytes，並避免無限重複匯入
        if up is not None and st.button(
            t("hold.import_confirm"),
            help=t("hold.import_help_button"),
            key="_hold_csv_confirm",
            use_container_width=True,
        ):
            try:
                raw = up.getvalue()
                df_in = pd.read_csv(io.BytesIO(raw))
                df_in.columns = [c.strip().lower() for c in df_in.columns]
                need = {"symbol", "shares", "avg_cost"}
                if not need.issubset(df_in.columns):
                    raise ValueError(f"missing columns: {need - set(df_in.columns)}")
                if "note" not in df_in.columns:
                    df_in["note"] = ""
                df_in = df_in[["symbol", "shares", "avg_cost", "note"]].fillna({"note": ""})
                st.session_state["holdings_df"] = df_in.reset_index(drop=True)
                st.success(t("hold.import_ok", n=len(df_in)))
                # 清上一次健檢結果，避免 stale
                if "holdings_result" in st.session_state:
                    del st.session_state["holdings_result"]
            except Exception as e:
                st.error(t("hold.import_fail", err=str(e)))
    with cIO2:
        df_now = st.session_state.get("holdings_df", pd.DataFrame())
        if not df_now.empty:
            csv_bytes = df_now.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                t("hold.export_csv"), data=csv_bytes,
                file_name="stockoracle_holdings.csv",
                use_container_width=True,
            )
    with cIO3:
        if st.button(t("hold.clear_btn"), use_container_width=True):
            st.session_state["holdings_df"] = pd.DataFrame(
                {"symbol": [], "shares": [], "avg_cost": [], "note": []}
            )
            st.session_state.pop("holdings_result", None)
            st.toast(t("hold.clear_confirm"), icon="🧹")

    add_col1, add_col2 = st.columns([1, 2])
    with add_col1:
        if st.button(t("hold.add_blank_row"), help=t("hold.add_blank_hint"), key="_hold_add_row"):
            base = st.session_state["holdings_df"].copy()
            new_row = pd.DataFrame(
                [{"symbol": "", "shares": float("nan"), "avg_cost": float("nan"), "note": ""}]
            )
            st.session_state["holdings_df"] = pd.concat([base, new_row], ignore_index=True)
            st.rerun()
    with add_col2:
        st.caption(t("hold.add_blank_hint"))

    # 編輯器
    edited = _data_editor_stretch(
        st.session_state["holdings_df"],
        num_rows="dynamic",
        column_config={
            "symbol": st.column_config.TextColumn(t("hold.col.symbol"), required=False),
            "shares": st.column_config.NumberColumn(t("hold.col.shares"), min_value=0.0, step=1.0),
            "avg_cost": st.column_config.NumberColumn(t("hold.col.avg_cost"), min_value=0.0, format="%.4f"),
            "note": st.column_config.TextColumn(t("hold.col.note")),
        },
        hide_index=True,
        key="_hold_editor",
    )
    st.session_state["holdings_df"] = edited
    st.caption(t("hold.editor_caption"))

    cash_col, run_col = st.columns([1, 1])
    with cash_col:
        st.session_state["holdings_cash"] = float(st.number_input(
            t("hold.cash_label"),
            value=float(st.session_state.get("holdings_cash", 0.0)),
            min_value=0.0, step=1000.0,
            help=t("hold.cash_help"),
        ))
    with run_col:
        st.write("")  # spacer
        run_btn = st.button(t("hold.run_btn"), type="primary", use_container_width=True)

    df_h = edited.copy()
    for req in ("symbol", "shares", "avg_cost"):
        if req not in df_h.columns:
            st.error(("Missing column: " if get_lang() == "en" else "缺少欄位：") + req)
            return
    df_h["symbol"] = df_h["symbol"].fillna("").astype(str).str.strip()
    df_h = df_h[df_h["symbol"].ne("")]
    df_h["symbol"] = df_h["symbol"].map(normalize_yahoo_symbol)
    df_h["shares"] = pd.to_numeric(df_h["shares"], errors="coerce")
    df_h["avg_cost"] = pd.to_numeric(df_h["avg_cost"], errors="coerce")
    df_h = df_h[(df_h["shares"].fillna(0) > 0) & (df_h["avg_cost"].fillna(0) > 0)]

    if df_h.empty:
        st.info(t("hold.no_rows"))
        return

    if not run_btn and "holdings_result" not in st.session_state:
        return

    if run_btn:
        with st.spinner(t("hold.fetching", n=len(df_h))):
            results = []
            failed: list[str] = []
            cb = st.session_state.get("cache_buster", 0)
            # 第一輪先抓資料 + 算市值
            raw_holdings = []
            for _, row in df_h.iterrows():
                sym = str(row["symbol"]).strip()
                bundle = _cached_holding_data(sym, period, cb)
                if bundle is None:
                    failed.append(sym)
                    raw_holdings.append((sym, row, None, None))
                    continue
                snap, enriched = bundle
                raw_holdings.append((sym, row, snap, enriched))
            # 算總市值（給 weight%）
            total_mv = 0.0
            for sym, row, snap, _enr in raw_holdings:
                if snap is None:
                    continue
                cur = snap.get("close")
                if cur is not None:
                    total_mv += float(row["shares"]) * float(cur)
            # 第二輪：完整 evaluate（需要 portfolio_market_value）
            from strategies import list_strategies as _ls_builtin
            built_in = _ls_builtin()  # 用內建 5 套，不含 custom（持股展望統一比較）
            for sym, row, snap, enriched in raw_holdings:
                market = "台股" if (sym.endswith(".TW") or sym.endswith(".TWO")) else "美股"
                res = evaluate_holding(
                    symbol=sym,
                    shares=float(row["shares"]),
                    avg_cost=float(row["avg_cost"]),
                    snap=snap,
                    enriched=enriched,
                    portfolio_market_value=total_mv,
                    market_label=market,
                    note=str(row.get("note", "") or ""),
                    strategies=built_in,
                )
                results.append(res)

            st.session_state["holdings_result"] = {
                "results": results,
                "failed": failed,
                "total_mv": total_mv,
                "cash": st.session_state["holdings_cash"],
            }
            for s in failed:
                st.warning(t("hold.fetch_failed", sym=s))

    bundle = st.session_state.get("holdings_result")
    if not bundle:
        return

    results = bundle["results"]
    total_mv = bundle["total_mv"]
    cash = bundle["cash"]
    total_capital = total_mv + cash
    cost_total = sum(r.cost_basis for r in results)
    pnl_total = sum(r.pnl for r in results)
    pnl_pct = (pnl_total / cost_total * 100.0) if cost_total > 0 else 0.0
    health_avg = (sum(r.health for r in results) / len(results)) if results else 0.0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric(t("hold.metric.total_capital"), _planner_format_money(total_capital))
    m2.metric(t("hold.metric.market_value"), _planner_format_money(total_mv))
    m3.metric(t("hold.metric.cash"), _planner_format_money(cash))
    m4.metric(t("hold.metric.unrealized"), _planner_format_money(pnl_total),
              delta=f"{pnl_pct:+.2f}%")
    m5.metric(t("hold.metric.holdings_count"), str(len(results)))
    m6.metric(t("hold.metric.health_avg"), f"{health_avg:.0f}")

    if not results:
        return

    rows = []
    for r in results:
        rows.append({
            t("hold.col.symbol"): format_symbol(r.symbol, dmode),
            t("hold.col.market"): market_label(r.market),
            t("hold.col.shares"): r.shares,
            t("hold.col.avg_cost"): r.avg_cost,
            t("hold.col.cur_price"): r.cur_price if r.cur_price is not None else float("nan"),
            t("hold.col.market_value"): r.market_value,
            t("hold.col.cost"): r.cost_basis,
            t("hold.col.pnl"): r.pnl,
            t("hold.col.pnl_pct"): r.pnl_pct,
            t("hold.col.weight"): r.weight_pct,
            t("hold.col.health"): r.health,
            t("hold.col.tier"): tier_label(r.tier_zh),
            t("hold.col.advice"): format_advice(r.advice_keys),
            t("hold.col.outlook"): format_outlook(r.outlook, limit=2),
        })
    df_show = pd.DataFrame(rows)

    pct_cols = [t("hold.col.pnl_pct"), t("hold.col.weight")]
    money_cols = [
        t("hold.col.cur_price"), t("hold.col.avg_cost"),
        t("hold.col.market_value"), t("hold.col.cost"), t("hold.col.pnl"),
    ]

    def _color_pnl(v):
        if v is None or pd.isna(v):
            return ""
        return "color: #66bb6a" if v > 0 else ("color: #ef5350" if v < 0 else "")

    def _color_health(v):
        if v is None or pd.isna(v):
            return ""
        if v >= 70:
            return "background-color: #1b5e20; color: white"
        if v >= 50:
            return "background-color: #689f38; color: white"
        if v >= 30:
            return "background-color: #f9a825; color: black"
        return "background-color: #c62828; color: white"

    sty = df_show.style
    sty = sty.format({c: "{:,.2f}" for c in money_cols if c in df_show.columns}, na_rep="—")
    sty = sty.format({c: "{:+.2f}%" for c in pct_cols if c in df_show.columns}, na_rep="—")
    sty = sty.format({t("hold.col.shares"): "{:,.0f}", t("hold.col.health"): "{:.0f}"})
    pn_cols = [c for c in (t("hold.col.pnl"), t("hold.col.pnl_pct")) if c in df_show.columns]
    hh_cols = [c for c in (t("hold.col.health"),) if c in df_show.columns]
    if pn_cols:
        if hasattr(sty, "map"):
            sty = sty.map(_color_pnl, subset=pn_cols)
        else:
            sty = sty.applymap(_color_pnl, subset=pn_cols)
    if hh_cols:
        if hasattr(sty, "map"):
            sty = sty.map(_color_health, subset=hh_cols)
        else:
            sty = sty.applymap(_color_health, subset=hh_cols)

    try:
        try:
            st.dataframe(sty, width="stretch", hide_index=True)
        except TypeError:
            st.dataframe(sty, use_container_width=True, hide_index=True)
    except Exception:
        try:
            st.dataframe(df_show, width="stretch", hide_index=True)
        except TypeError:
            st.dataframe(df_show, use_container_width=True, hide_index=True)


    # ---- 個別持股展望 ----
    with st.expander(t("hold.detail_title"), expanded=False):
        st.caption(t("hold.outlook_caption"))
        for r in results:
            if not r.outlook:
                continue
            st.markdown(f"**{format_symbol(r.symbol, dmode)}** &nbsp; — &nbsp; "
                        f"{t('hold.col.health')}: **{r.health:.0f}** &nbsp; · &nbsp; "
                        f"{t('hold.col.advice')}：{format_advice(r.advice_keys)}")
            ocols = st.columns(len(r.outlook))
            for ic, o in enumerate(r.outlook):
                tone = {"hold.outlook.bullish": "✅", "hold.outlook.warn": "⚠️",
                        "hold.outlook.neutral": "·"}.get(o["status_key"], "·")
                ocols[ic].markdown(
                    f"<small>{tone} {o['strategy_label']}<br/>"
                    f"<b>{t(o['status_key'])}</b> &nbsp; ({o['score']:.1f}/10)</small>",
                    unsafe_allow_html=True,
                )
            st.divider()


# ----- 手機偵測（Streamlit ≥ 1.30 才有 st.context.headers） -----


def _is_likely_mobile() -> bool:
    """簡易手機偵測；新版 Streamlit 才有 st.context.headers。失敗就回 False。"""
    try:
        ctx = getattr(st, "context", None)
        if ctx is None:
            return False
        headers = getattr(ctx, "headers", None)
        if not headers:
            return False
        ua = (headers.get("user-agent") or headers.get("User-Agent") or "").lower()
        return any(k in ua for k in ("iphone", "android", "ipad", "mobile"))
    except Exception:
        return False


def _plotly_config(mobile: bool = False) -> dict:
    """共用的 Plotly config：手機上隱藏 modebar 多餘按鈕、開 responsive。"""
    return {
        "responsive": True,
        "displayModeBar": False if mobile else True,
        "displaylogo": False,
        "modeBarButtonsToRemove": [
            "lasso2d", "select2d", "toggleSpikelines",
            "hoverClosestCartesian", "hoverCompareCartesian",
        ],
    }


# ----- 主程式 -----


def main() -> None:
    # 語言初始化（必須在所有 t() 呼叫之前）
    if "lang" not in st.session_state:
        st.session_state["lang"] = "zh"

    st.set_page_config(
        page_title=t("app.page_title"),
        layout="wide",
        # 手機進來預設收起 sidebar，騰出寬度
        initial_sidebar_state="collapsed" if _is_likely_mobile() else "expanded",
    )

    st.markdown(
        """
        <style>
        .small-meta { color: #9e9e9e; font-size: 0.85rem; margin-top: -10px; }
        div[data-testid="stMetricValue"] { font-size: 1.05rem; }

        /* 手機（<= 768 px）：縮字、metric 寬鬆換行、表格水平 scroll、tab 字級小 */
        @media (max-width: 768px) {
          html, body, [class*="css"] { font-size: 14px !important; }
          h1 { font-size: 1.4rem !important; }
          h2 { font-size: 1.15rem !important; }
          h3 { font-size: 1.0rem !important; }
          .small-meta { font-size: 0.75rem !important; }
          /* metric value 縮一點，避免長數字爆版 */
          div[data-testid="stMetricValue"] { font-size: 0.95rem !important; }
          div[data-testid="stMetricLabel"] { font-size: 0.7rem !important; }
          /* 多欄縮間距 */
          div[data-testid="stHorizontalBlock"] { gap: 0.5rem !important; }
          /* DataFrame 內滑動 */
          div[data-testid="stDataFrame"] { overflow-x: auto !important; }
          /* tab 列水平捲、字小 */
          div[data-baseweb="tab-list"] {
              overflow-x: auto !important;
              white-space: nowrap !important;
              flex-wrap: nowrap !important;
          }
          button[data-baseweb="tab"] { font-size: 0.85rem !important; padding: 0.4rem 0.6rem !important; }
          /* sidebar 開啟時佔滿 90% 寬避免擠壓 */
          section[data-testid="stSidebar"] { min-width: 90% !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    head_left, head_right = st.columns([6, 2])
    with head_left:
        st.title(t("app.title"))
        st.markdown(
            f"<div class='small-meta'>{t('app.subtitle')}</div>",
            unsafe_allow_html=True,
        )
    refresh_btn = head_right.button(t("common.refresh"), width="stretch", type="primary")

    if "cache_buster" not in st.session_state:
        st.session_state["cache_buster"] = 0

    with st.sidebar:
        st.subheader(t("sidebar.market_section"))
        market_options_zh = ["全部", "美股", "台股"]
        market = st.radio(
            t("sidebar.market"), market_options_zh,
            index=2, horizontal=True,
            format_func=market_label,
        )
        market_key = {"全部": "all", "美股": "us", "台股": "tw"}[market]

        size = st.selectbox(
            t("sidebar.size"),
            options=UNIVERSE_SIZES,
            index=1,
            help=t("sidebar.size_help"),
        )

        listed_ok, otc_ok = has_full_market_data()
        if size.startswith("全 TW") and not (listed_ok or otc_ok):
            st.warning(t("sidebar.full_tw_warn"))
        if size.startswith("全 TW"):
            est = t("sidebar.full_tw_size_listed") if "上櫃" not in size else t("sidebar.full_tw_size_all")
            st.caption(t("sidebar.full_tw_estimate", est=est))

        sync_col1, sync_col2 = st.columns([1, 1])
        if sync_col1.button(t("sidebar.sync_tw"), help=t("sidebar.sync_tw_help")):
            with st.spinner(t("sidebar.sync_running")):
                try:
                    from tools.sync_tw_universe import main as _sync_main
                    _sync_main([])
                    n = reload_names()
                    listed_ok2, otc_ok2 = has_full_market_data()
                    msg = []
                    if listed_ok2:
                        msg.append(t("sidebar.sync_listed_ok"))
                    if otc_ok2:
                        msg.append(t("sidebar.sync_otc_ok"))
                    st.success(t("sidebar.sync_done", which="、".join(msg), n=n))
                except Exception as e:
                    st.error(t("sidebar.sync_failed", err=str(e)))
        if sync_col2.button(t("sidebar.sync_status_btn")):
            st.info(t(
                "sidebar.sync_status_msg",
                listed=t("sidebar.exists") if listed_ok else t("sidebar.missing"),
                otc=t("sidebar.exists") if otc_ok else t("sidebar.missing"),
            ))

        custom = st.text_area(
            t("sidebar.custom_symbols"),
            placeholder=t("sidebar.custom_symbols_ph"),
            help=t("sidebar.custom_symbols_help"),
            height=72,
        )

        st.subheader(t("sidebar.history_section"))
        period_label_keys = {"6mo": "sidebar.period.6mo", "1y": "sidebar.period.1y",
                              "2y": "sidebar.period.2y", "5y": "sidebar.period.5y"}
        period = st.selectbox(
            t("sidebar.period"),
            ["6mo", "1y", "2y", "5y"],
            index=2,
            format_func=lambda x: t(period_label_keys[x]),
            help=t("sidebar.period_help"),
        )

        st.subheader(t("sidebar.strategy_section"))

        # ---- 自訂策略（折疊在策略選單上方）----
        custom_strat = _render_custom_strategy_form()

        extra_strats = [custom_strat] if custom_strat is not None else []
        strategies = list_strategies(extra=extra_strats)
        strat_keys = [s.key for s in strategies]
        strat_default = strat_keys.index(DEFAULT_STRATEGY_KEY) if DEFAULT_STRATEGY_KEY in strat_keys else 0
        strat_key = st.selectbox(
            t("sidebar.strategy_pick"),
            options=strat_keys,
            index=strat_default,
            format_func=lambda k: next(
                (
                    f"{s.label}  ·  {s.timeframe} / {t('common.market')}: {s.risk_label}"
                    if get_lang() == "en"
                    else f"{s.label}（{s.timeframe}／風險{s.risk_label}）"
                )
                for s in strategies if s.key == k
            ),
            help=t("sidebar.strategy_help"),
        )
        active_strategy = get_strategy(strat_key, extra=extra_strats)
        with st.expander(t("sidebar.strategy_expander"), expanded=False):
            st.markdown(f"**{active_strategy.label}**\n\n{active_strategy.description}")
            st.markdown(f"**{t('common.entry_rules')}**")
            for r in active_strategy.entry_rules_text:
                st.markdown(f"- {r}")
            st.markdown(f"**{t('common.exit_rules')}**")
            for r in active_strategy.exit_rules_text:
                st.markdown(f"- {r}")

        # ---- 設定 expander（含語言、顯示模式、進階）----
        with st.expander(t("settings.section"), expanded=False):
            st.caption(t("settings.section_help"))

            # 語言
            lang_labels = {"zh": "繁體中文", "en": "English"}
            cur_lang = get_lang()
            new_lang = st.radio(
                t("sidebar.language"),
                options=list(LANGS),
                index=list(LANGS).index(cur_lang),
                format_func=lambda k: lang_labels[k],
                horizontal=True,
                key="_lang_picker_settings",
            )
            if new_lang != cur_lang:
                set_lang(new_lang)
                st.rerun()

            # 顯示模式
            display_label_map_zh = {"名稱 代號": t("display.name_symbol"),
                                    "代號": t("display.symbol_only"),
                                    "名稱": t("display.name_only")}
            display_mode = st.radio(
                t("sidebar.symbol_display_mode"),
                DISPLAY_MODES,
                index=0,
                horizontal=False,
                format_func=lambda x: display_label_map_zh.get(x, x),
                help=t("sidebar.symbol_display_help"),
            )

            # 進階
            short_top = st.number_input(t("sidebar.short_top"), 0, 50, 15, help=t("sidebar.short_top_help"))
            force_refresh = st.checkbox(t("sidebar.force_refresh"), value=False)
            if force_refresh:
                st.session_state["cache_buster"] += 1
            if st.button(t("sidebar.clear_cache_btn")):
                n = clear_cache()
                st.cache_data.clear()
                st.session_state["cache_buster"] += 1
                st.success(t("sidebar.clear_cache_done", n=n))

    if refresh_btn:
        st.session_state["cache_buster"] += 1

    symbols, source_label = _resolve_symbols(market_key, size, custom)

    needs_run = (
        refresh_btn
        or "all_df" not in st.session_state
        or st.session_state.get("_last_signature") != (tuple(symbols), period)
    )
    if needs_run:
        spinner_msg = (
            f"Fetching {len(symbols)} symbols and computing indicators… (first run / new universe takes longer)"
            if get_lang() == "en" else
            f"正在抓取 {len(symbols)} 檔資料並計算指標…（首次/換清單會稍久）"
        )
        with st.spinner(spinner_msg):
            try:
                all_df, short_df, failed, meta = _cached_full_report(
                    tuple(symbols), period, st.session_state["cache_buster"]
                )
            except Exception as e:
                st.error(("Run failed: " if get_lang() == "en" else "執行失敗：") + str(e))
                return
        st.session_state.update(
            {
                "all_df": all_df,
                "short_df": short_df,
                "failed": failed,
                "meta": meta,
                "_last_signature": (tuple(symbols), period),
                "ui_meta": {
                    "market": market,
                    "source_label": source_label,
                    "period": period,
                    "n_total": len(symbols),
                    "short_top": int(short_top),
                    "display_mode": display_mode,
                },
            }
        )

    all_df: pd.DataFrame = st.session_state["all_df"]
    short_df: pd.DataFrame = st.session_state["short_df"]
    failed: list[str] = st.session_state["failed"]
    meta: dict = st.session_state["meta"]
    ui_meta: dict = st.session_state["ui_meta"]
    # 即時切換顯示模式不需要重抓
    ui_meta["display_mode"] = display_mode
    dmode = display_mode

    c1, c2, c3, c4, c5 = st.columns([1.1, 1.4, 1.1, 1.4, 1])
    c1.metric(t("top.market"), market_label(ui_meta.get("market", "")))
    c2.metric(t("top.source"), ui_meta.get("source_label", ""))
    c3.metric(t("top.range"), str(ui_meta.get("period", "")))
    c4.metric(
        t("top.last_data"),
        t("top.last_data_value", us=(meta.get("us_last") or "—"), tw=(meta.get("tw_last") or "—")),
    )
    c5.metric(t("top.success_count"), f"{meta.get('n_ok', 0)} / {ui_meta.get('n_total', 0)}")

    if failed:
        with st.expander(t("top.failed_list", n=len(failed)), expanded=False):
            st.write(", ".join(failed))

    tab_rank, tab_one, tab_short, tab_plan, tab_holdings = st.tabs(
        [t("tabs.rank"), t("tabs.one"), t("tabs.short"), t("tabs.plan"), t("tabs.holdings")]
    )

    # ---- 排名與篩選 ----
    with tab_rank:
        if all_df.empty:
            st.warning(t("rank.no_data"))
        else:
            f1, f2, f3, f4, f5 = st.columns([1, 1.4, 1, 1, 1.2])
            with f1:
                mkt_filter = st.multiselect(
                    t("rank.filter_market"), ["美股", "台股"],
                    default=["美股", "台股"], format_func=market_label,
                )
            with f2:
                tier_filter = st.multiselect(
                    t("rank.filter_tier"), _TIER_ORDER,
                    default=["強烈買進", "買進", "偏多觀察"],
                    format_func=tier_label,
                )
            with f3:
                min_score = st.number_input(t("rank.filter_min_score"), value=-5.0, step=0.5)
            with f4:
                min_vr = st.number_input(t("rank.filter_min_vr"), value=0.0, step=0.1)
            with f5:
                only_short = st.checkbox(t("rank.filter_only_short"), value=False)

            df = all_df.copy()
            if mkt_filter:
                df = df[df["market"].isin(mkt_filter)]
            if tier_filter and "recommendation" in df.columns:
                df = df[df["recommendation"].isin(tier_filter)]
            if "score" in df.columns:
                df = df[df["score"] >= float(min_score)]
            if "volume_ratio" in df.columns:
                df = df[df["volume_ratio"].fillna(0) >= float(min_vr)]
            if only_short and "short_term_signal" in df.columns:
                df = df[df["short_term_signal"]]

            st.caption(t("rank.caption", n=len(df)))
            jump_cnt = min(16, len(df))
            if jump_cnt:
                sym_btns = st.columns(min(8, jump_cnt))
                for i, sym in enumerate(df["symbol"].head(jump_cnt).tolist()):
                    label = format_symbol(sym, dmode)
                    if sym_btns[i % len(sym_btns)].button(label, key=f"jump_{sym}"):
                        st.session_state["selected_symbol"] = sym
                        st.toast(t("rank.toast_jump", label=label), icon="✅")

            st.dataframe(_styled_table(df, display_mode=dmode), width="stretch", hide_index=True)

            csv_all = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(t("rank.download_csv"), data=csv_all, file_name="stockoracle_filtered.csv")

    # ---- 短期推薦 ----
    with tab_short:
        if short_df.empty:
            st.info(t("short.empty"))
        else:
            top_n = ui_meta.get("short_top", 15)
            show = short_df if top_n == 0 else short_df.head(int(top_n))
            st.dataframe(_styled_table(show, display_mode=dmode), width="stretch", hide_index=True)
            csv = short_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(t("short.download_csv"), data=csv, file_name="stockoracle_short.csv")

    # ---- 資產規劃 ----
    with tab_plan:
        _render_planner_tab(all_df, period, dmode, active_strategy)

    # ---- 持股健檢 ----
    with tab_holdings:
        _render_holdings_tab(period=str(ui_meta.get("period", "1y")), dmode=dmode)

    # ---- 個股分析 ----
    with tab_one:
        if all_df.empty:
            st.warning(t("one.no_data"))
        else:
            all_syms = all_df["symbol"].tolist()
            search = st.text_input(
                t("one.search"),
                value="",
                placeholder=t("one.search_ph"),
            )
            if search.strip():
                q = search.strip().upper()
                syms = [
                    s for s in all_syms
                    if q in s.upper() or q in format_symbol(s, dmode).upper()
                ]
                if not syms:
                    st.info(t("one.search_no_match", q=search))
                    syms = all_syms
            else:
                syms = all_syms

            default_sym = st.session_state.get("selected_symbol") or syms[0]
            if default_sym not in syms:
                default_sym = syms[0]

            cA, cB = st.columns([2, 3])
            with cA:
                pick = st.selectbox(
                    t("one.pick", shown=len(syms), total=len(all_syms)),
                    syms,
                    index=syms.index(default_sym),
                    format_func=lambda s: format_symbol(s, dmode),
                )
            with cB:
                _ma_options = [5, 10, 20, 30, 60, 120, 200]
                _ma_default = st.session_state.get("ma_periods", [20, 60, 200])
                _ma_default = [p for p in _ma_default if p in _ma_options]
                ma_periods = st.multiselect(
                    t("one.ma_label"),
                    options=_ma_options,
                    default=_ma_default or [20, 60, 200],
                    format_func=lambda x: f"MA{x}",
                    help=t("one.ma_help"),
                )
                st.session_state["ma_periods"] = ma_periods

            cC, cD, cE, cF, cG = st.columns(5)
            with cC:
                show_bb = st.checkbox(t("one.show_bb"), value=False)
            with cD:
                log_scale = st.checkbox(t("one.log_axis"), value=False)
            with cE:
                show_macd = st.checkbox(t("one.show_macd"), value=True)
            with cF:
                show_rsi = st.checkbox(t("one.show_rsi"), value=True)
            with cG:
                show_signals = st.checkbox(t("one.show_signals"), value=True)

            bundle = _cached_chart_bundle(pick, str(ui_meta.get("period", "1y")), st.session_state["cache_buster"])
            if bundle is None:
                st.warning(t("one.no_history", sym=pick))
            else:
                raw, enriched, snap, bench_close, bench_sym, fast = bundle
                title_label = format_symbol(pick, dmode)

                strat_eval = active_strategy.evaluate(snap, enriched)
                hist_sigs = active_strategy.historical_signals(enriched)

                fig = build_ohlcv_figure(
                    enriched,
                    title_label,
                    ma_periods=ma_periods,
                    show_bb=show_bb,
                    show_signals=show_signals,
                    show_macd=show_macd,
                    show_rsi=show_rsi,
                    log_scale=log_scale,
                    bench_close=bench_close,
                    bench_name=bench_sym,
                    entry_dates=hist_sigs.get("entries", []),
                    exit_dates=hist_sigs.get("exits", []),
                    strategy_label=active_strategy.label,
                )
                st.plotly_chart(fig, use_container_width=True, config=_plotly_config(_is_likely_mobile()))

                m1, m2, m3, m4 = st.columns(4)
                m1.metric(
                    t("one.metric.composite"), f"{snap.get('score', 0):.2f}",
                    help=t("one.metric.composite_help"),
                )
                m2.metric(t("one.metric.tier"), tier_label(snap.get("recommendation")),
                          help=t("one.metric.tier_help"))
                m3.metric(t("one.metric.today_pct"), f"{(snap.get('ret_1d') or 0) * 100:+.2f}%")
                m4.metric(t("one.metric.dist_52w_high"), f"{(snap.get('dist_to_52w_high_pct') or 0):+.2f}%")

                s1, s2, s3, s4 = st.columns(4)
                s1.metric(
                    t("one.strategy_metric_label", label=active_strategy.label),
                    tier_label(strat_eval.get("recommendation")),
                    help=f"{active_strategy.timeframe} / {active_strategy.risk_label}",
                )
                s2.metric(
                    t("one.metric.strategy_score"), f"{strat_eval.get('score', 0):.2f}",
                    help=t("one.metric.strategy_score_help"),
                )
                s3.metric(
                    t("one.metric.entry_today"),
                    t("one.metric.met") if strat_eval.get("entry_today") else t("one.metric.dash"),
                )
                s4.metric(
                    t("one.metric.exit_today"),
                    t("one.metric.exit_met") if strat_eval.get("exit_today") else t("one.metric.dash"),
                )

                with st.expander(t("one.expander.rules", label=active_strategy.label), expanded=True):
                    cL, cR = st.columns(2)
                    with cL:
                        st.markdown(f"**{t('common.entry_rules')}**")
                        for r in active_strategy.entry_rules_text:
                            st.markdown(f"- {r}")
                        hits = strat_eval.get("rule_hits", [])
                        misses = strat_eval.get("rule_misses", [])
                        sep = "、" if get_lang() == "zh" else ", "
                        if hits:
                            st.success(t("one.rules.hits", hits=sep.join(hits)))
                        if misses:
                            st.warning(t("one.rules.misses", misses=sep.join(misses)))
                    with cR:
                        st.markdown(f"**{t('common.exit_rules')}**")
                        for r in active_strategy.exit_rules_text:
                            st.markdown(f"- {r}")
                        ex_reasons = strat_eval.get("exit_today_reasons", [])
                        if ex_reasons:
                            sepr = "；" if get_lang() == "zh" else "; "
                            st.error(t("one.rules.exit_today_triggered", reasons=sepr.join(ex_reasons)))
                        else:
                            st.caption(t("common.no_trigger_today"))

                st.markdown(
                    full_recommendation_markdown(
                        snap,
                        daily_df=raw,
                        fast_info=fast,
                        strategy=active_strategy,
                    )
                )
                st.caption(t("one.caption.see_planner"))


if __name__ == "__main__":
    main()
