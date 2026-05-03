"""
v2 選股管線：給每檔產出含趨勢、動能、相對強弱、波動率、回撤、量能、ATR 短期等欄位的快照，
並輸出綜合排名與短期推薦。

主要對外函式：
- run_full_report(symbols, period, market_for_bench=None, progress_cb=None)
    回傳 (all_df, short_df, failed_symbols, meta)
- build_symbol_snapshot(symbol, df, *, enriched=None, bench_close=None) -> dict | None
"""

from __future__ import annotations

import os
from typing import Callable, Iterable

import pandas as pd

from analysis import add_indicators
from data_loader import fetch_history, fetch_watchlist, last_index_for
from universe import benchmark_for_market, benchmark_for_symbol


def _default_us_symbols() -> list[str]:
    return ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "TSM"]


def _default_tw_symbols() -> list[str]:
    return ["2330.TW", "2454.TW", "2317.TW", "2308.TW", "2882.TW", "0050.TW"]


def _default_symbols() -> list[str]:
    raw = os.environ.get("STOCK_ORACLE_SYMBOLS")
    if raw and raw.strip():
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return _default_us_symbols() + _default_tw_symbols()


def symbols_for_market(market: str) -> list[str]:
    m = (market or "all").strip().lower()
    if m in ("us", "美股", "us_stocks"):
        return list(_default_us_symbols())
    if m in ("tw", "台股", "taiwan", "tw_stocks"):
        return list(_default_tw_symbols())
    return _default_us_symbols() + _default_tw_symbols()


def infer_market(symbol: str) -> str:
    s = (symbol or "").upper()
    if s.endswith(".TW") or s.endswith(".TWO"):
        return "台股"
    return "美股"


def _f(x) -> float | None:
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


def score_v2(snap: dict) -> tuple[float, dict]:
    """
    新版綜合評分：
      趨勢階梯 (max ~4)
      動能 (max 1.5)
      相對強弱 vs 基準 (max 2 / min -1)
      量能 (max 1.5)
      波動率懲罰 (max -1)
      回撤懲罰 (max -1.5)
    回傳 (score, breakdown)
    """
    s = 0.0
    bd: dict[str, float] = {}

    close = _f(snap.get("close"))
    ma20 = _f(snap.get("ma20"))
    ma50 = _f(snap.get("ma50"))
    ma200 = _f(snap.get("ma200"))

    trend = 0.0
    if close is not None and ma20 is not None and close > ma20:
        trend += 1.0
    if close is not None and ma50 is not None and close > ma50:
        trend += 1.0
    if ma20 is not None and ma50 is not None and ma20 > ma50:
        trend += 0.75
    if ma50 is not None and ma200 is not None and ma50 > ma200:
        trend += 1.25
    bd["趨勢階梯"] = trend
    s += trend

    momentum = 0.0
    rsi = _f(snap.get("rsi14"))
    if rsi is not None:
        if 45 <= rsi <= 65:
            momentum += 1.5
        elif 40 <= rsi <= 70:
            momentum += 1.0
        elif 70 < rsi <= 78:
            momentum += 0.25
    bd["動能(RSI)"] = momentum
    s += momentum

    rs = 0.0
    rs60 = _f(snap.get("rs_60d_pct"))
    if rs60 is not None:
        rs = max(min(rs60 * 0.1, 2.0), -1.0)
    bd["相對強弱"] = rs
    s += rs

    vol_score = 0.0
    vr = _f(snap.get("volume_ratio"))
    if vr is not None and vr >= 1.0:
        vol_score = min((vr - 1.0) * 1.0, 1.5)
    bd["量能"] = vol_score
    s += vol_score

    vol_pen = 0.0
    vol60 = _f(snap.get("vol_60d_ann"))
    if vol60 is not None:
        if vol60 > 0.6:
            vol_pen = -1.0
        elif vol60 > 0.45:
            vol_pen = -0.5
    bd["波動率"] = vol_pen
    s += vol_pen

    dd_pen = 0.0
    mdd = _f(snap.get("mdd_60d"))
    if mdd is not None:
        if mdd < -0.25:
            dd_pen = -1.5
        elif mdd < -0.18:
            dd_pen = -0.75
    bd["回撤"] = dd_pen
    s += dd_pen

    return s, bd


def tier_from_score(score: float) -> str:
    if score >= 7.0:
        return "強烈買進"
    if score >= 5.0:
        return "買進"
    if score >= 3.0:
        return "偏多觀察"
    if score >= 1.0:
        return "中性"
    return "避開"


def short_term_v2(snap: dict) -> tuple[bool, float]:
    """
    短期戰術（量增價漲突破）：
      - 漲幅 / ATR% >= 0.8（用 ATR 取代固定 3%，控制不同股波動）
      - 量比 >= 1.5
      - 收盤位於當日區間上 60%（避免上影線）
    回傳 (是否成立, 強度分數)
    """
    ret = _f(snap.get("ret_1d"))
    vr = _f(snap.get("volume_ratio"))
    atrp = _f(snap.get("atr14_pct"))
    loc = _f(snap.get("day_close_loc"))
    if ret is None or vr is None or atrp is None or atrp <= 0:
        return False, 0.0
    breakout = (ret * 100.0) / atrp >= 0.8
    location_ok = loc is None or loc >= 0.6
    if not (breakout and vr >= 1.5 and location_ok):
        return False, 0.0
    strength = (ret * 100.0) * min(vr, 3.0)
    return True, float(strength)


def build_symbol_snapshot(
    symbol: str,
    df: pd.DataFrame,
    *,
    enriched: pd.DataFrame | None = None,
    bench_close: pd.Series | None = None,
) -> dict | None:
    enriched = (
        enriched if enriched is not None else add_indicators(df, bench_close=bench_close, include_full=True)
    )
    if enriched.empty or len(enriched) < 25:
        return None
    last = enriched.iloc[-1]

    snap: dict = {
        "market": infer_market(symbol),
        "symbol": symbol,
        "close": _f(last.get("close")),
        "ret_1d": _f(last.get("ret_1d")),
        "ma20": _f(last.get("ma20")),
        "ma50": _f(last.get("ma50")),
        "ma200": _f(last.get("ma200")),
        "rsi14": _f(last.get("rsi14")),
        "macd_hist": _f(last.get("macd_hist")),
        "volume_ratio": _f(last.get("volume_ratio")),
        "volume_zscore": _f(last.get("volume_zscore")),
        "atr14": _f(last.get("atr14")),
        "atr14_pct": _f(last.get("atr14_pct")),
        "vol_60d_ann": _f(last.get("vol_60d_ann")),
        "mdd_60d": _f(last.get("mdd_60d")),
        "mdd_252d": _f(last.get("mdd_252d")),
        "high_52w": _f(last.get("high_52w")),
        "low_52w": _f(last.get("low_52w")),
        "dist_to_52w_high_pct": _f(last.get("dist_to_52w_high_pct")),
        "dist_to_52w_low_pct": _f(last.get("dist_to_52w_low_pct")),
        "rs_60d_pct": _f(last.get("rs_60d_pct")),
        "rs_120d_pct": _f(last.get("rs_120d_pct")),
        "day_close_loc": _f(last.get("day_close_loc")),
        "as_of": pd.to_datetime(enriched.index[-1]).strftime("%Y-%m-%d"),
    }
    sc, breakdown = score_v2(snap)
    snap["score"] = sc
    snap["score_breakdown"] = breakdown
    snap["recommendation"] = tier_from_score(sc)

    st_sig, st_strength = short_term_v2(snap)
    snap["short_term_signal"] = st_sig
    snap["short_term_score"] = st_strength

    return snap


def _columns_order() -> list[str]:
    return [
        "market",
        "symbol",
        "as_of",
        "close",
        "ret_1d",
        "ma20",
        "ma50",
        "ma200",
        "rsi14",
        "macd_hist",
        "volume_ratio",
        "atr14_pct",
        "vol_60d_ann",
        "mdd_60d",
        "dist_to_52w_high_pct",
        "rs_60d_pct",
        "rs_120d_pct",
        "score",
        "recommendation",
        "short_term_signal",
        "short_term_score",
    ]


def run_full_report(
    symbols: Iterable[str] | None = None,
    *,
    period: str = "1y",
    market_for_bench: str | None = None,
    progress_cb: Callable[[int, int, str, bool], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict]:
    """
    並行抓取 → 計算指標 → 建立快照 → 排序輸出。
    回傳 (all_df, short_df, failed_symbols, meta)
    meta 含：n_total、n_ok、bench_used、bench_last_date 等。
    """
    syms = list(symbols) if symbols is not None else _default_symbols()

    bench_us = "^GSPC"
    bench_tw = "^TWII"
    bench_us_df = fetch_history(bench_us, period=period, interval="1d")
    bench_tw_df = fetch_history(bench_tw, period=period, interval="1d")
    bench_us_close = bench_us_df["close"] if not bench_us_df.empty else pd.Series(dtype=float)
    bench_tw_close = bench_tw_df["close"] if not bench_tw_df.empty else pd.Series(dtype=float)

    frames, failed = fetch_watchlist(syms, period=period, progress_cb=progress_cb)

    rows: list[dict] = []
    for sym, df in frames.items():
        bench_close = (
            bench_tw_close
            if benchmark_for_symbol(sym) == "^TWII"
            else bench_us_close
        )
        snap = build_symbol_snapshot(sym, df, bench_close=bench_close if not bench_close.empty else None)
        if snap:
            rows.append(snap)

    if not rows:
        return pd.DataFrame(), pd.DataFrame(), failed, {
            "n_total": len(syms),
            "n_ok": 0,
            "bench_us": bench_us,
            "bench_tw": bench_tw,
        }

    all_df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    short_df = (
        all_df[all_df["short_term_signal"]]
        .sort_values("short_term_score", ascending=False)
        .reset_index(drop=True)
        .copy()
    )

    cols = [c for c in _columns_order() if c in all_df.columns]
    all_df = all_df[cols + [c for c in all_df.columns if c not in cols and c != "score_breakdown"]]
    short_df = short_df[[c for c in cols if c in short_df.columns]] if not short_df.empty else short_df

    meta = {
        "n_total": len(syms),
        "n_ok": int(len(all_df)),
        "bench_us": bench_us,
        "bench_tw": bench_tw,
        "bench_us_last": (str(bench_us_df.index[-1].date()) if not bench_us_df.empty else None),
        "bench_tw_last": (str(bench_tw_df.index[-1].date()) if not bench_tw_df.empty else None),
        "us_last": str(last_index_for("us").date()) if last_index_for("us") is not None else None,
        "tw_last": str(last_index_for("tw").date()) if last_index_for("tw") is not None else None,
    }
    return all_df, short_df, failed, meta


# ----- 相容舊 CLI -----


def pick_top(symbol_frames: dict[str, pd.DataFrame], *, top_n: int = 5) -> pd.DataFrame:
    rows: list[dict] = []
    for sym, df in symbol_frames.items():
        snap = build_symbol_snapshot(sym, df)
        if snap:
            rows.append(snap)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def run_daily_pick(
    symbols: Iterable[str] | None = None,
    *,
    top_n: int = 5,
    period: str = "1y",
) -> pd.DataFrame:
    syms = list(symbols) if symbols is not None else _default_symbols()
    frames, _ = fetch_watchlist(syms, period=period)
    return pick_top(frames, top_n=top_n)
