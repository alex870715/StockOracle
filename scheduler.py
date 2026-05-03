"""
每日管線入口：收盤後執行資料載入 → 分析 → 全體評分 + 短期推薦（台股／美股）。

本地 Cron（伺服器時區請自行對齊美股市場收盤後），範例（每週一至五 17:10 ET 需依機器時區換算）：

    10 22 * * 1-5 cd /path/to/StockOracle && /path/to/venv/bin/python scheduler.py >> logs/cron.log 2>&1

GitHub Actions：見 `.github/workflows/daily_pick.yml`（UTC 排程，可依夏令時間微調）。

環境變數：
    STOCK_ORACLE_SYMBOLS  逗號分隔代號；未設定則使用內建美股+台股清單。
    STOCK_ORACLE_OUT      可選，輸出綜合表 CSV。
    STOCK_ORACLE_OUT_SHORT 可選，輸出短期推薦表 CSV。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 確保與本目錄模組可互相 import（cron / 任意 cwd）
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from daily_pick import run_full_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="StockOracle 每日選股管線")
    p.add_argument(
        "--period",
        default="1y",
        help="yfinance history period（預設 1y，利於均線與指標）",
    )
    p.add_argument(
        "--short-top",
        type=int,
        default=10,
        help="短期推薦最多顯示幾檔（預設 10；0 表示不截斷）",
    )
    p.add_argument(
        "--out",
        default=os.environ.get("STOCK_ORACLE_OUT", ""),
        help="綜合評分表 CSV（也可用 STOCK_ORACLE_OUT）",
    )
    p.add_argument(
        "--out-short",
        default=os.environ.get("STOCK_ORACLE_OUT_SHORT", ""),
        help="短期推薦表 CSV（也可用 STOCK_ORACLE_OUT_SHORT）",
    )
    p.add_argument(
        "--cron-hint",
        action="store_true",
        help="僅印出 Cron / Actions 使用說明後結束",
    )
    args = p.parse_args(argv)

    if args.cron_hint:
        print(__doc__)
        return 0

    all_df, short_df, failed, meta = run_full_report(period=args.period)
    if all_df.empty:
        print("無有效資料：請檢查網路或代號。", file=sys.stderr)
        if failed:
            print(f"失敗清單：{', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"資料截止：美 {meta.get('us_last') or '—'} / 台 {meta.get('tw_last') or '—'}；"
          f"成功 {meta.get('n_ok')} / 全部 {meta.get('n_total')}")
    if failed:
        print(f"失敗：{', '.join(failed)}")
    print()
    print("========== 全體標的：推薦程度（依綜合分數排序）==========")
    print(all_df.to_string(index=False))
    print()

    print("========== 短期推薦（漲幅 ≥ 0.8 ATR + 量比 ≥ 1.5 + 收高）==========")
    if short_df.empty:
        print("（今日無符合條件之短期訊號）")
    else:
        lim = args.short_top if args.short_top > 0 else len(short_df)
        print(short_df.head(lim).to_string(index=False))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        all_df.to_csv(args.out, index=False)
        print(f"Wrote 綜合表 {args.out}", file=sys.stderr)
    if args.out_short and not short_df.empty:
        Path(args.out_short).parent.mkdir(parents=True, exist_ok=True)
        short_df.to_csv(args.out_short, index=False)
        print(f"Wrote 短期表 {args.out_short}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
