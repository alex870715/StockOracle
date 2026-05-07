"""
將 run_full_report 結果暫存於磁碟，減少每次開啟 Streamlit 都整包重抓。

環境變數：
- STOCK_ORACLE_REPORT_TTL_HOURS  報告有效時間（小時），預設 24；設 0 表示停用持久化。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parent
_CACHE_DIR = _ROOT / ".cache" / "reports"


def _signature_id(symbols: tuple[str, ...], period: str) -> str:
    raw = json.dumps({"p": period, "s": list(symbols)}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _paths(sig_id: str) -> tuple[Path, Path, Path, Path]:
    base = _CACHE_DIR / sig_id
    return (
        base.with_suffix(".all.parquet"),
        base.with_suffix(".short.parquet"),
        base.with_suffix(".failed.json"),
        base.with_suffix(".meta.json"),
    )


def report_ttl_hours() -> float | None:
    raw = os.environ.get("STOCK_ORACLE_REPORT_TTL_HOURS", "24").strip()
    try:
        h = float(raw)
    except ValueError:
        h = 24.0
    if h <= 0:
        return None
    return h


def save_report(
    symbols: tuple[str, ...],
    period: str,
    *,
    all_df: pd.DataFrame,
    short_df: pd.DataFrame,
    failed: list[str],
    meta: dict[str, Any],
) -> None:
    ttl = report_ttl_hours()
    if ttl is None:
        return
    sig = _signature_id(symbols, period)
    p_all, p_short, p_fail, p_meta = _paths(sig)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        all_df.to_parquet(p_all)
        if not short_df.empty:
            short_df.to_parquet(p_short)
        else:
            short_df.to_parquet(p_short)
        p_fail.write_text(json.dumps(failed, ensure_ascii=False), encoding="utf-8")
        blob = {
            "saved_at": time.time(),
            "period": period,
            "n_symbols": len(symbols),
            "meta": meta,
        }
        p_meta.write_text(json.dumps(blob, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def load_report(
    symbols: tuple[str, ...],
    period: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict[str, Any]] | None:
    ttl = report_ttl_hours()
    if ttl is None:
        return None
    sig = _signature_id(symbols, period)
    p_all, p_short, p_fail, p_meta = _paths(sig)
    if not (p_all.exists() and p_meta.exists()):
        return None
    try:
        meta_side = json.loads(p_meta.read_text(encoding="utf-8"))
        age_h = (time.time() - float(meta_side.get("saved_at", 0))) / 3600.0
        if age_h > ttl:
            return None
        all_df = pd.read_parquet(p_all)
        short_df = pd.read_parquet(p_short) if p_short.exists() else pd.DataFrame()
        failed_raw = json.loads(p_fail.read_text(encoding="utf-8")) if p_fail.exists() else []
        failed = list(failed_raw) if isinstance(failed_raw, list) else []
        meta = meta_side.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        return all_df, short_df, failed, meta
    except Exception:
        return None


def clear_saved_reports() -> int:
    """刪除 .cache/reports 內檔案；回傳刪除數。"""
    if not _CACHE_DIR.is_dir():
        return 0
    n = 0
    for p in _CACHE_DIR.iterdir():
        if p.is_file():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n
