"""技術指標：均線、RSI、量能、ATR、波動率、回撤、布林通道、MACD、相對強弱。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi_wilder(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": hist})


def bollinger(close: pd.Series, length: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(length, min_periods=length).mean()
    std = close.rolling(length, min_periods=length).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower})


def rolling_max_drawdown(close: pd.Series, window: int) -> pd.Series:
    """以 window 內的最高點為基準，計算近 window 期 max drawdown（負值或 0）。"""
    roll_max = close.rolling(window, min_periods=2).max()
    dd = close / roll_max - 1.0
    return dd.rolling(window, min_periods=2).min()


def annualized_volatility(close: pd.Series, window: int = 60) -> pd.Series:
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window, min_periods=window).std() * np.sqrt(252.0)


def relative_strength_pct(close: pd.Series, bench_close: pd.Series, lookback: int = 60) -> pd.Series:
    """單檔相對基準的 lookback 期超額報酬（百分比；正值代表跑贏基準）。"""
    a = close.pct_change(lookback, fill_method=None)
    b = bench_close.reindex(close.index).ffill().pct_change(lookback, fill_method=None)
    return (a - b) * 100.0


def _as_series(x, index: pd.Index, name: str) -> pd.Series:
    """確保拿到的是一維 Series；遇到重複欄名造成的 DataFrame 取第一欄。"""
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(np.nan, index=index, name=name, dtype="float64")
        x = x.iloc[:, 0]
    s = pd.to_numeric(x, errors="coerce")
    if not isinstance(s, pd.Series):
        s = pd.Series(s, index=index, name=name)
    return s.astype("float64")


def add_indicators(
    df: pd.DataFrame,
    *,
    bench_close: pd.Series | None = None,
    include_full: bool = True,
) -> pd.DataFrame:
    """
    在含 OHLCV 的 DataFrame 上新增完整技術欄位：
      ma20 / ma50 / ma200, rsi14, ret_1d, day_close_loc,
      volume_ma20, volume_ratio, volume_zscore,
      atr14, atr14_pct, vol_60d_ann,
      mdd_60d, mdd_252d, dist_to_52w_high_pct, dist_to_52w_low_pct,
      bb_mid/upper/lower, bb_pct, macd / macd_signal / macd_hist,
      若提供 bench_close：rs_60d_pct, rs_120d_pct, rel_strength_score
    """
    out = df.copy()
    # 防呆：去掉重複欄位（避免 out["close"] 拿到 DataFrame）
    out = out.loc[:, ~out.columns.duplicated()]
    if "close" not in out.columns:
        raise KeyError("DataFrame 需包含欄位 'close'")
    if "high" not in out.columns:
        out["high"] = out["close"]
    if "low" not in out.columns:
        out["low"] = out["close"]

    close = _as_series(out["close"], out.index, "close")
    high = _as_series(out["high"], out.index, "high")
    low = _as_series(out["low"], out.index, "low")
    op = _as_series(out["open"], out.index, "open") if "open" in out.columns else close

    out["ma20"] = close.rolling(20, min_periods=20).mean()
    out["rsi14"] = rsi_wilder(close, 14)
    out["ret_1d"] = close / close.shift(1) - 1.0
    rng = (high - low).replace(0, np.nan)
    out["day_close_loc"] = ((close - low) / rng).clip(0.0, 1.0)
    _ = op  # 保留欄位完整；UI 不一定使用 open 但會在圖表用到

    vol = (
        _as_series(out["volume"], out.index, "volume")
        if "volume" in out.columns
        else pd.Series(np.nan, index=out.index, dtype="float64")
    )
    out["volume_ma20"] = vol.rolling(20, min_periods=20).mean()
    out["volume_ratio"] = vol / out["volume_ma20"].replace(0, np.nan)
    vol_std = vol.rolling(20, min_periods=20).std()
    out["volume_zscore"] = (vol - out["volume_ma20"]) / vol_std.replace(0, np.nan)

    if not include_full:
        return out

    out["ma50"] = close.rolling(50, min_periods=50).mean()
    out["ma200"] = close.rolling(200, min_periods=200).mean()

    out["atr14"] = atr(high, low, close, 14)
    out["atr14_pct"] = out["atr14"] / close.replace(0, np.nan) * 100.0

    out["vol_60d_ann"] = annualized_volatility(close, 60)

    out["mdd_60d"] = rolling_max_drawdown(close, 60)
    out["mdd_252d"] = rolling_max_drawdown(close, 252)

    high_252 = close.rolling(252, min_periods=20).max()
    low_252 = close.rolling(252, min_periods=20).min()
    out["high_52w"] = high_252
    out["low_52w"] = low_252
    out["dist_to_52w_high_pct"] = (close / high_252 - 1.0) * 100.0
    out["dist_to_52w_low_pct"] = (close / low_252.replace(0, np.nan) - 1.0) * 100.0

    bb = bollinger(close, 20, 2.0)
    out = out.join(bb)
    width = (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["bb_pct"] = (close - out["bb_lower"]) / width

    m = macd(close, 12, 26, 9)
    out = out.join(m)

    if bench_close is not None and not bench_close.empty:
        out["rs_60d_pct"] = relative_strength_pct(close, bench_close, 60)
        out["rs_120d_pct"] = relative_strength_pct(close, bench_close, 120)
        out["rel_strength_score"] = (
            out["rs_60d_pct"].fillna(0) * 0.6 + out["rs_120d_pct"].fillna(0) * 0.4
        )

    return out


def latest_swing_low(low: pd.Series, lookback: int = 20) -> float | None:
    """取近 lookback 期最低點（簡易 swing low），給停損參考。"""
    s = pd.to_numeric(low, errors="coerce").dropna().tail(lookback)
    if s.empty:
        return None
    return float(s.min())
