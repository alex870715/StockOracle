"""
Yahoo Finance 資料載入：磁碟快取 + 重試 + 並行抓取 + 失敗清單 + 進度回呼。

設計：
- 任何呼叫 yfinance 的函式都用 `_with_retry`；對暫時性錯誤指數退避。
- 個股／基準 OHLCV 走 parquet 磁碟快取（key 含 symbol / period / interval / auto_adjust）。
- 並行抓取使用 ThreadPoolExecutor；可給進度回呼 (i, total, symbol, ok)。
- 自訂代號正規化：去 $、純數字台股自動補 .TW。

注意：磁碟快取預設 TTL 1 小時；可用 `set_cache_dir` 切換目錄。
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import yfinance as yf

# cache 版本後綴：邏輯有變動時 +1，可一次失效掉舊的髒檔（例如曾寫入過短的歷史）。
_CACHE_VERSION = "v2"
_CACHE_DIR = Path(__file__).resolve().parent / ".cache" / f"yf_{_CACHE_VERSION}"
_DEFAULT_TTL_SECS = 60 * 60  # 1 小時

# 各 period 的「合理最少根數」估計：低於這個數量視為片段資料、不採用 / 不寫入。
# 已對新上市標的留 1/3 的緩衝，避免誤殺真的是新股。
_MIN_ROWS_FOR_PERIOD: dict[str, int] = {
    "5d": 3,
    "1mo": 12,
    "3mo": 35,
    "6mo": 80,
    "ytd": 30,
    "1y": 160,
    "2y": 320,
    "5y": 800,
    "10y": 1600,
    "max": 0,  # 不檢查
}


def _min_rows_for_period(period: str) -> int:
    return _MIN_ROWS_FOR_PERIOD.get((period or "").lower(), 0)


# Yahoo 區間：分鐘／小時級 K 線必須保留索引時刻（不可 normalize 成純日期）
_INTRADAY_INTERVALS = frozenset({"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"})


def is_intraday_interval(interval: str) -> bool:
    return (interval or "").strip().lower() in _INTRADAY_INTERVALS


def _effective_min_rows(period: str, interval: str) -> int:
    """分 K 資料長度落差大；略過最短根數門檻，避免快取／重試誤判。"""
    return 0 if is_intraday_interval(interval) else _min_rows_for_period(period)


# 抑制 yfinance 自身的錯誤訊息（例如某些代號的 'NoneType' object is not subscriptable）。
# 我們本來就會把抓不到的代號收進「失敗清單」回傳給 UI / CLI。
for _name in ("yfinance", "yfinance.utils", "yfinance.scrapers", "yfinance.scrapers.history"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


def set_cache_dir(p: str | Path) -> None:
    global _CACHE_DIR
    _CACHE_DIR = Path(p)


def normalize_yahoo_symbol(symbol: str) -> str:
    """整理 Yahoo Finance 代號；純數字 4–6 碼自動補 .TW（純台股慣例）。"""
    s = (symbol or "").strip().upper().lstrip("$").strip()
    if not s:
        return s
    if s.endswith((".TW", ".TWO")):
        return s
    if re.fullmatch(r"\d{4,6}", s):
        return f"{s}.TW"
    return s


def _cache_paths(symbol: str, period: str, interval: str, auto_adjust: bool) -> tuple[Path, Path]:
    safe = symbol.replace("/", "_").replace("^", "INDEX_")
    base = _CACHE_DIR / f"{safe}__{period}__{interval}__{int(auto_adjust)}"
    return base.with_suffix(".parquet"), base.with_suffix(".meta.json")


def _read_cache(
    symbol: str, period: str, interval: str, auto_adjust: bool, ttl_secs: int
) -> pd.DataFrame | None:
    data_p, meta_p = _cache_paths(symbol, period, interval, auto_adjust)
    if not (data_p.exists() and meta_p.exists()):
        return None
    try:
        meta = json.loads(meta_p.read_text())
        age = time.time() - float(meta.get("fetched_at", 0))
        if age > ttl_secs:
            return None
        df = pd.read_parquet(data_p)
        if df.empty:
            return None
        # 若資料明顯比 period 預期還短（之前 yfinance 偶發只回片段），
        # 視為髒快取觸發重抓；這也是「MA200 / 短期訊號突然不見」最常見的源頭。
        min_rows = _effective_min_rows(period, interval)
        if min_rows and len(df) < min_rows:
            return None
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        return None


def _write_cache(
    symbol: str, period: str, interval: str, auto_adjust: bool, df: pd.DataFrame
) -> None:
    # 拒絕寫入明顯片段化的結果，避免下一次讀到不完整資料。
    min_rows = _effective_min_rows(period, interval)
    if min_rows and len(df) < min_rows:
        return
    data_p, meta_p = _cache_paths(symbol, period, interval, auto_adjust)
    try:
        data_p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(data_p)
        meta_p.write_text(
            json.dumps(
                {"fetched_at": time.time(), "symbol": symbol, "n_rows": int(len(df))},
                ensure_ascii=False,
            )
        )
    except Exception:
        # 寫入失敗不致命，下次重抓即可
        pass


def clear_cache(older_than_secs: float | None = None) -> int:
    """清掉磁碟快取；不傳參數=全清。回傳刪除的檔案數。"""
    if not _CACHE_DIR.exists():
        return 0
    now = time.time()
    cnt = 0
    for p in _CACHE_DIR.iterdir():
        if not p.is_file():
            continue
        if older_than_secs is not None:
            try:
                if now - p.stat().st_mtime < older_than_secs:
                    continue
            except Exception:
                pass
        try:
            p.unlink()
            cnt += 1
        except Exception:
            pass
    return cnt


def _with_retry(fn: Callable, *, attempts: int = 3, base_sleep: float = 0.7):
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # yfinance 偶發 JSON / 網路錯
            last_exc = e
            time.sleep(base_sleep * (2**i))
    if last_exc:
        raise last_exc
    return None


def _normalize_columns(df: pd.DataFrame, *, strip_calendar_day_only: bool = True) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()

    # 1) 攤平 MultiIndex（yfinance 有時對單一代號也會回 MultiIndex）。
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for c in df.columns:
            if isinstance(c, tuple):
                # 取「OHLCV 名稱」這一層；通常在第 0 層，少數情況在第 1 層
                top = c[0] if isinstance(c[0], str) else (c[1] if len(c) > 1 else c[0])
                if not isinstance(top, str):
                    top = str(top)
                flat.append(top)
            else:
                flat.append(c)
        df.columns = flat

    # 2) 中英欄名統一為小寫
    rename = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
    }
    df = df.rename(columns=rename)

    # 3) 去掉重複欄位（保留第一份）：避免 df["close"] 變成 DataFrame
    df = df.loc[:, ~df.columns.duplicated()]

    # 4) 只留我們會用到的欄位（且必須存在於 df）
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    if not keep:
        return pd.DataFrame()
    df = df[keep].copy()

    # 5) 強制數值化、清掉整列 NaN
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])

    # 6) 統一 DatetimeIndex 為 naive（部分代號如 3711.TW 會帶 Asia/Taipei tz，
    #    其他不帶；混在一起做 intersection / reindex 會交集為 0）。
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        try:
            idx = idx.tz_convert(None)
        except (TypeError, AttributeError):
            idx = idx.tz_localize(None)
    if strip_calendar_day_only:
        df.index = idx.normalize()  # 日線：對齊到日
    else:
        df.index = idx  # 分／小時 K：保留時刻以便繪圖
    return df


def fetch_history(
    symbol: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    auto_adjust: bool = True,
    use_cache: bool = True,
    ttl_secs: int = _DEFAULT_TTL_SECS,
) -> pd.DataFrame:
    """
    抓取單一標的 OHLCV；含磁碟快取與重試。
    auto_adjust=True：以後復權價計算（與 yfinance 預設一致）。
    """
    sym = normalize_yahoo_symbol(symbol)
    if not sym:
        return pd.DataFrame()

    strip_cal = not is_intraday_interval(interval)

    if use_cache:
        cached = _read_cache(sym, period, interval, auto_adjust, ttl_secs)
        if cached is not None:
            cached["symbol"] = sym
            return cached

    def _do_download() -> pd.DataFrame:
        df = yf.download(
            sym,
            period=period,
            interval=interval,
            auto_adjust=auto_adjust,
            progress=False,
            threads=False,
            group_by="column",
        )
        return _normalize_columns(df, strip_calendar_day_only=strip_cal)

    def _do_ticker() -> pd.DataFrame:
        # 部分台股 ETF（例如 00919.TW）走 download() 容易丟 internal None；
        # 改用 Ticker.history() 通常較穩。
        df = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=auto_adjust)
        return _normalize_columns(df, strip_calendar_day_only=strip_cal)

    df = pd.DataFrame()
    try:
        out = _with_retry(_do_download, attempts=2)
        if isinstance(out, pd.DataFrame):
            df = out
    except Exception:
        df = pd.DataFrame()

    # 若結果為空、或明顯比 period 預期短（片段資料），改用 Ticker.history() 再試。
    min_rows = _effective_min_rows(period, interval)
    if df.empty or (min_rows and len(df) < min_rows):
        try:
            out2 = _with_retry(_do_ticker, attempts=2)
            if isinstance(out2, pd.DataFrame) and not out2.empty:
                # 取兩次結果中比較長的那份。
                if df.empty or len(out2) > len(df):
                    df = out2
        except Exception:
            pass

    if not df.empty and use_cache:
        _write_cache(sym, period, interval, auto_adjust, df)

    if not df.empty:
        df = df.copy()
        df["symbol"] = sym
    return df


def fetch_watchlist(
    symbols: Iterable[str],
    *,
    period: str = "1y",
    interval: str = "1d",
    auto_adjust: bool = True,
    use_cache: bool = True,
    ttl_secs: int = _DEFAULT_TTL_SECS,
    max_workers: int = 8,
    progress_cb: Callable[[int, int, str, bool], None] | None = None,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """
    並行抓取多檔；回傳 (成功 dict, 失敗代號清單)。
    progress_cb(i, total, symbol, ok) 在每檔完成後被呼叫。
    """
    syms_norm = [normalize_yahoo_symbol(s) for s in symbols if s and str(s).strip()]
    syms_norm = [s for s in syms_norm if s]
    total = len(syms_norm)
    out: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    if total == 0:
        return out, failed

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, total))) as ex:
        futs = {
            ex.submit(
                fetch_history,
                s,
                period=period,
                interval=interval,
                auto_adjust=auto_adjust,
                use_cache=use_cache,
                ttl_secs=ttl_secs,
            ): s
            for s in syms_norm
        }
        for i, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            ok = False
            try:
                df = fut.result()
                if df is not None and not df.empty:
                    out[sym] = df
                    ok = True
                else:
                    failed.append(sym)
            except Exception:
                failed.append(sym)
            if progress_cb:
                try:
                    progress_cb(i, total, sym, ok)
                except Exception:
                    pass

    return out, failed


def fetch_fast_info(symbol: str) -> dict:
    """
    取部分基本面 / 報價快取（fast_info），失敗回空 dict。
    包含：last_price、market_cap、currency、ten_day_avg_volume、year_high/low 等。
    """
    sym = normalize_yahoo_symbol(symbol)
    if not sym:
        return {}

    def _do() -> dict:
        t = yf.Ticker(sym)
        fi = getattr(t, "fast_info", None) or {}
        out = {}
        for k in (
            "last_price",
            "market_cap",
            "currency",
            "ten_day_average_volume",
            "three_month_average_volume",
            "year_high",
            "year_low",
            "fifty_day_average",
            "two_hundred_day_average",
        ):
            try:
                v = fi[k] if hasattr(fi, "__getitem__") else getattr(fi, k, None)
            except Exception:
                v = None
            if v is not None:
                out[k] = v
        return out

    try:
        return _with_retry(_do, attempts=2) or {}
    except Exception:
        return {}


def last_index_for(market: str, ttl_secs: int = _DEFAULT_TTL_SECS) -> pd.Timestamp | None:
    """回傳該市場基準指數最後一根日 K 的時間（用來顯示資料時效）。"""
    bench = "^TWII" if (market or "").lower().startswith(("tw", "台")) else "^GSPC"
    df = fetch_history(bench, period="1mo", interval="1d", use_cache=True, ttl_secs=ttl_secs)
    if df.empty:
        return None
    return pd.to_datetime(df.index[-1])
