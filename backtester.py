"""
回測：讀取 data/ 內 OHLCV CSV，執行長期／短期策略並輸出報告。

CSV 格式（必要欄位，大小寫不拘）：
    date, open, high, low, close, volume
    date 可為欄位或索引；需至少約 220 個交易日以上以利 200 日均線穩定。

資料庫：本版僅實作 CSV；若要接 DB，可將查詢結果轉成相同欄位的 DataFrame 後呼叫內部函式。
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis import rsi_wilder


@dataclass
class BacktestResult:
    name: str
    annualized_return: float
    sharpe_ratio: float
    n_trades: int
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: pd.Series | None = None


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    colmap = {c.lower(): c for c in df.columns}
    rename = {}
    for need in ("open", "high", "low", "close", "volume"):
        for k, v in colmap.items():
            if k == need:
                rename[v] = need
                break
    out = df.rename(columns=rename)
    for c in ("open", "high", "low", "close", "volume"):
        if c not in out.columns:
            raise ValueError(f"缺少欄位 {c}，目前欄位：{list(df.columns)}")
    return out


def load_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    df = pd.read_csv(path)
    lower = [str(c).lower() for c in df.columns]
    if "date" in lower:
        c = [x for x in df.columns if str(x).lower() == "date"][0]
        df[c] = pd.to_datetime(df[c])
        df = df.set_index(c)
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        df = df.set_index(df.columns[0])
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return _normalize_ohlcv(df)


def _prep_indicators(close: pd.Series, volume: pd.Series) -> pd.DataFrame:
    d = pd.DataFrame({"close": close.astype(float), "volume": volume.astype(float)})
    d["ma50"] = d["close"].rolling(50, min_periods=50).mean()
    d["ma200"] = d["close"].rolling(200, min_periods=200).mean()
    d["rsi14"] = rsi_wilder(d["close"], 14)
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=20).mean()
    d["ret_1d"] = d["close"].pct_change()
    d["vol_ratio"] = d["volume"] / d["vol_ma20"].replace(0, np.nan)
    return d


def backtest_long_trend(close: pd.Series, volume: pd.Series) -> BacktestResult:
    """
    長期：收盤 > 200MA 且 RSI 向上突破 50 進場；收盤跌破 50MA 出場。
    單一標的、全倉進出，訊號以當日收盤價成交（簡化）。
    """
    d = _prep_indicators(close, volume)
    price = d["close"].values
    rsi = d["rsi14"].values
    ma50 = d["ma50"].values
    ma200 = d["ma200"].values
    n = len(d)
    equity = np.ones(n)
    cash = 1.0
    shares = 0.0
    trades: list[dict[str, Any]] = []
    in_pos = False
    entry_i = -1

    for i in range(1, n):
        if np.isnan(ma200[i]) or np.isnan(rsi[i]) or np.isnan(rsi[i - 1]):
            equity[i] = cash + shares * price[i]
            continue

        if not in_pos:
            cross_up = rsi[i - 1] <= 50.0 < rsi[i]
            if price[i] > ma200[i] and cross_up:
                shares = cash / price[i]
                cash = 0.0
                in_pos = True
                entry_i = i
                trades.append(
                    {
                        "type": "long_term",
                        "action": "BUY",
                        "bar": i,
                        "date": d.index[i],
                        "price": float(price[i]),
                        "reason": "close>MA200 & RSI cross>50",
                    }
                )
        else:
            if price[i] < ma50[i]:
                cash = shares * price[i]
                trades.append(
                    {
                        "type": "long_term",
                        "action": "SELL",
                        "bar": i,
                        "date": d.index[i],
                        "price": float(price[i]),
                        "reason": "close<MA50",
                    }
                )
                shares = 0.0
                in_pos = False

        equity[i] = cash + shares * price[i]

    if in_pos:
        cash = shares * price[-1]
        trades.append(
            {
                "type": "long_term",
                "action": "SELL_EOD",
                "bar": n - 1,
                "date": d.index[-1],
                "price": float(price[-1]),
                "reason": "sample_end",
            }
        )
        shares = 0.0
    equity[-1] = cash + shares * price[-1]

    er = pd.Series(equity, index=d.index)
    rets = er.pct_change().dropna()
    ann, sharpe = _metrics_from_equity(er, rets)
    return BacktestResult(
        name="long_term",
        annualized_return=ann,
        sharpe_ratio=sharpe,
        n_trades=len([t for t in trades if t["action"] == "BUY"]),
        trades=trades,
        equity_curve=er,
    )


def backtest_short_burst(close: pd.Series, high: pd.Series, volume: pd.Series) -> BacktestResult:
    """
    短期：單日量增（volume > 1.2×20日均量）且漲幅 ≥ 3% 進場；
    持有最多 3 個交易日，若盤中高價觸及 +5% 則以該價停利出場；否則第 3 日收盤出場。
    """
    d = _prep_indicators(close, volume)
    price = d["close"].values
    hi = high.astype(float).values
    vol = d["volume"].values
    vol_ma = d["vol_ma20"].values
    ret = d["ret_1d"].values
    n = len(d)
    equity = np.ones(n)
    cash = 1.0
    shares = 0.0
    trades: list[dict[str, Any]] = []
    in_pos = False
    entry_price = 0.0
    hold_left = 0
    entry_bar = 0

    for i in range(1, n):
        if in_pos:
            tp_px = entry_price * 1.05
            # 進場當日收盤成交，不於同一根 K 線檢查停利
            if i > entry_bar and hi[i] >= tp_px:
                exit_px = tp_px
                cash = shares * exit_px
                trades.append(
                    {
                        "type": "short_term",
                        "action": "SELL_TP",
                        "bar": i,
                        "date": d.index[i],
                        "price": float(exit_px),
                        "reason": "+5% take profit",
                    }
                )
                shares = 0.0
                in_pos = False
            else:
                hold_left -= 1
                if hold_left <= 0:
                    exit_px = price[i]
                    cash = shares * exit_px
                    trades.append(
                        {
                            "type": "short_term",
                            "action": "SELL_TIME",
                            "bar": i,
                            "date": d.index[i],
                            "price": float(exit_px),
                            "reason": "3-day hold exit",
                        }
                    )
                    shares = 0.0
                    in_pos = False
        if not in_pos and i < n - 1:
            if (
                not np.isnan(vol_ma[i])
                and vol_ma[i] > 0
                and not np.isnan(ret[i])
                and vol[i] > 1.2 * vol_ma[i]
                and ret[i] >= 0.03
            ):
                entry_price = price[i]
                shares = cash / entry_price
                cash = 0.0
                in_pos = True
                hold_left = 3
                entry_bar = i
                trades.append(
                    {
                        "type": "short_term",
                        "action": "BUY",
                        "bar": i,
                        "date": d.index[i],
                        "price": float(entry_price),
                        "reason": "vol spike & +3%",
                    }
                )

        equity[i] = cash + shares * price[i]

    if in_pos:
        cash = shares * price[-1]
        trades.append(
            {
                "type": "short_term",
                "action": "SELL_EOD",
                "bar": n - 1,
                "date": d.index[-1],
                "price": float(price[-1]),
                "reason": "sample_end",
            }
        )
        shares = 0.0
    equity[-1] = cash + shares * price[-1]

    er = pd.Series(equity, index=d.index)
    rets = er.pct_change().dropna()
    ann, sharpe = _metrics_from_equity(er, rets)
    return BacktestResult(
        name="short_term",
        annualized_return=ann,
        sharpe_ratio=sharpe,
        n_trades=len([t for t in trades if t["action"] == "BUY"]),
        trades=trades,
        equity_curve=er,
    )


def _metrics_from_equity(equity: pd.Series, daily_rets: pd.Series) -> tuple[float, float]:
    eq = equity.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return float("nan"), float("nan")
    total_days = (eq.index[-1] - eq.index[0]).days
    if total_days <= 0:
        total_days = len(eq)
    years = max(total_days / 365.25, 1e-9)
    ann = (float(eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years)) - 1.0
    r = daily_rets.replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < 2 or r.std() == 0 or math.isnan(r.std()):
        sharpe = float("nan")
    else:
        sharpe = float(np.sqrt(252.0) * r.mean() / r.std())
    return ann, sharpe


def print_report(results: list[BacktestResult], *, source: str) -> None:
    print(f"=== 回測來源：{source} ===\n")
    for res in results:
        print(f"--- 策略：{res.name} ---")
        print(f"年化報酬率: {res.annualized_return * 100:.2f}%")
        print(f"夏普比率:   {res.sharpe_ratio:.3f}")
        print(f"進場次數:   {res.n_trades}")
        print("交易清單（節錄）：")
        for t in res.trades[:30]:
            print(f"  {t}")
        if len(res.trades) > 30:
            print(f"  ... 共 {len(res.trades)} 筆，僅顯示前 30 筆")
        print()


def write_demo_csv(path: Path, n: int = 600, seed: int = 42) -> None:
    """產生隨機漫步示範資料，僅供本機測試回測管線。"""
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2018-01-01", periods=n, freq="B")
    r = rng.normal(0.0004, 0.012, n)
    close = 100 * np.exp(np.cumsum(r))
    noise = rng.uniform(0.995, 1.005, n)
    open_ = close * noise
    high = np.maximum(open_, close) * rng.uniform(1.0, 1.02, n)
    low = np.minimum(open_, close) * rng.uniform(0.98, 1.0, n)
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    # 人為製造幾根大量長紅
    for j in [120, 240, 380]:
        if j < n:
            close[j:] *= 1.03
            vol[j] *= 3.0
    df = pd.DataFrame(
        {"date": dt, "open": open_, "high": high, "low": low, "close": close, "volume": vol}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="StockOracle 回測")
    p.add_argument("--csv", help="單一 CSV 路徑")
    p.add_argument("--data-dir", default="data", help="掃描目錄內所有 .csv（預設 data）")
    p.add_argument(
        "--write-demo",
        action="store_true",
        help="寫入 data/demo_backtest.csv 後結束（示範用）",
    )
    args = p.parse_args(argv)

    root = Path(__file__).resolve().parent
    data_dir = root / args.data_dir

    if args.write_demo:
        demo = data_dir / "demo_backtest.csv"
        write_demo_csv(demo)
        print(f"Wrote {demo}")
        return 0

    paths: list[Path] = []
    if args.csv:
        paths.append(Path(args.csv))
    else:
        paths = sorted(data_dir.glob("*.csv"))
        if not paths:
            print("未指定 --csv 且 data/ 無 CSV。可先執行：python backtester.py --write-demo")
            return 1

    for path in paths:
        path = path if path.is_absolute() else root / path
        if not path.exists():
            print(f"找不到檔案：{path}", flush=True)
            continue
        df = load_ohlcv_csv(path)
        long_r = backtest_long_trend(df["close"], df["volume"])
        short_r = backtest_short_burst(df["close"], df["high"], df["volume"])
        print_report([long_r, short_r], source=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
