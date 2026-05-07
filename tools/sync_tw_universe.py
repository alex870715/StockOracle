"""
從 TWSE 的「ISIN 國際證券辨識號碼」公開頁面抓取上市／上櫃清單，
產生白名單 JSON 給 universe.py 與 symbol_meta.py 使用。

來源（HTML，big5 編碼）：
- 上市：https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
- 上櫃：https://isin.twse.com.tw/isin/C_public.jsp?strMode=4

過濾規則：只保留 區塊「股票」「ETF」「受益證券」（後者主要是 REITs / 信託），
並只收純數字 4–6 碼（含可能的單字尾大寫，例如特別股 1101A 也保留）。

注意：**不必每天同步**。Repo 已內建 `data/universe_tw_*.json`；只在要更新上市／上櫃名單
（新股掛牌、下市等）時執行本腳本或側欄同步即可。

輸出檔：
- data/universe_tw_listed.json
- data/universe_tw_otc.json

執行：
    python tools/sync_tw_universe.py            # 抓上市 + 上櫃
    python tools/sync_tw_universe.py --listed   # 只抓上市
    python tools/sync_tw_universe.py --otc      # 只抓上櫃
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

URL_LISTED = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"  # 上市
URL_OTC = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"  # 上櫃

KEEP_SECTIONS = {"股票", "ETF", "受益證券", "ETN"}

# TWSE 的 SSL 憑證少了 Subject Key Identifier 擴展，OpenSSL 3.x（Linux 雲端常見）
# 嚴格模式會拒絕；本地 macOS LibreSSL 較寬鬆所以沒事。第一次撞牆後設為 True，
# 之後直接 verify=False，避免每個 URL 都重撞一次。
_TWSE_SSL_BROKEN = False


def fetch(url: str) -> str:
    global _TWSE_SSL_BROKEN
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockOracle/1.0)"}
    if _TWSE_SSL_BROKEN:
        r = _fetch_unverified(url, headers)
    else:
        try:
            r = requests.get(url, timeout=60, headers=headers)
        except requests.exceptions.SSLError as e:
            # TWSE 是公開讀取頁面、無敏感資訊，可接受降級到 unverified retry。
            print(f"[警告] TWSE SSL 驗證失敗，降級到 unverified retry: {e}", flush=True)
            _TWSE_SSL_BROKEN = True
            r = _fetch_unverified(url, headers)
    # TWSE 頁面為 big5；用 ms950 對 big5 superset 解較不易壞字
    r.encoding = "ms950"
    r.raise_for_status()
    return r.text


def _fetch_unverified(url: str, headers: dict) -> requests.Response:
    """關掉 SSL 驗證再抓一次；同時壓掉 urllib3 的 InsecureRequestWarning。"""
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    return requests.get(url, timeout=60, headers=headers, verify=False)


_CODE_RE = re.compile(r"^(\d{4,6}[A-Z]?)[\s\u3000]+(.+)$")
# CFICode 第 1 碼：E=Equity、C=Collective Investment（ETF/REIT/受益證券）、
# D=Debt、R=Right（權證）、O=Option、F=Future…
# 我們保留個股 + ETF/受益證券，排除權證 / 期貨 / 選擇權 / 公司債等衍生商品。
_KEEP_CFI_PREFIX = ("E", "C")


def parse(html: str, suffix: str) -> dict[str, str]:
    """回傳 {代號+suffix: 名稱}；用「市場別=上市/上櫃」+「CFICode 以 E 開頭」雙條件過濾。"""
    # TWSE HTML 偶有格式問題：先 lxml、再 html5lib（見 requirements.txt）
    tables = pd.read_html(StringIO(html), flavor=["lxml", "html5lib"])
    if not tables:
        return {}
    df = tables[0]
    if df.shape[1] < 6:
        return {}
    df.columns = list(range(df.shape[1]))
    out: dict[str, str] = {}

    valid_market = {"上市", "上櫃"}

    for _, row in df.iterrows():
        first = str(row.iloc[0]).strip()
        market = str(row.iloc[3]).strip()
        cfi = str(row.iloc[5]).strip()
        if first.lower() == "nan" or not first:
            continue
        if market not in valid_market:
            continue
        if not cfi or cfi[0].upper() not in _KEEP_CFI_PREFIX:
            continue
        m = _CODE_RE.match(first)
        if not m:
            continue
        code, name = m.group(1).strip(), m.group(2).strip()
        out[f"{code}{suffix}"] = name
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="同步台股上市／上櫃白名單")
    p.add_argument("--listed", action="store_true", help="只抓上市")
    p.add_argument("--otc", action="store_true", help="只抓上櫃")
    args = p.parse_args(argv)

    do_listed = args.listed or not args.otc  # 預設抓上市
    do_otc = args.otc or not args.listed  # 預設抓上櫃
    if args.listed and not args.otc:
        do_otc = False
    if args.otc and not args.listed:
        do_listed = False

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if do_listed:
        print("抓取上市清單…", flush=True)
        html = fetch(URL_LISTED)
        m = parse(html, ".TW")
        out = DATA_DIR / "universe_tw_listed.json"
        out.write_text(json.dumps(m, ensure_ascii=False, indent=2))
        print(f"  {out.relative_to(ROOT)}: {len(m)} 檔")

    if do_otc:
        print("抓取上櫃清單…", flush=True)
        html = fetch(URL_OTC)
        m = parse(html, ".TWO")
        out = DATA_DIR / "universe_tw_otc.json"
        out.write_text(json.dumps(m, ensure_ascii=False, indent=2))
        print(f"  {out.relative_to(ROOT)}: {len(m)} 檔")

    return 0


if __name__ == "__main__":
    sys.exit(main())
