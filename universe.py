"""
精選股票池：依市場（美股／台股）與規模回傳代號清單，並提供基準指數對照。

規模：
  簡 (~15)            : 權值龍頭
  標準 (~40)          : 權值龍頭 + 主要 ETF / 主流類股
  完整 (~80)          : 上述 + 進階熱門個股 + 多檔 ETF
  全 TW 上市 (~1300)  : 從 data/universe_tw_listed.json 載入
  全 TW 上市+上櫃 (~2300): 含上櫃 .TWO（更多但更慢）

「全 TW」選項需要先執行：
    python tools/sync_tw_universe.py
（同步 TWSE 公開的 ISIN 清單到 data/universe_tw_*.json）
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_LISTED_PATH = _DATA_DIR / "universe_tw_listed.json"
_OTC_PATH = _DATA_DIR / "universe_tw_otc.json"

UNIVERSE_SIZES = [
    "簡 (~15)",
    "標準 (~40)",
    "完整 (~80)",
    "全 TW 上市 (~1300)",
    "全 TW 上市+上櫃 (~2300)",
]


def _load_json_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text()).keys())
    except Exception:
        return []


def has_full_market_data() -> tuple[bool, bool]:
    """回傳 (上市檔案存在, 上櫃檔案存在)，給 UI 顯示提示。"""
    return _LISTED_PATH.exists(), _OTC_PATH.exists()


_US_CORE = [
    # 七巨頭 + 半導體龍頭
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AVGO", "AMD", "TSM", "ASML", "MU",
]

_US_STANDARD_EXTRA = [
    # 軟體 / 雲 / 平台
    "ORCL", "CRM", "ADBE", "NOW", "SNOW", "PLTR",
    # 金融
    "JPM", "GS", "MS", "BAC", "V", "MA",
    # 消費 / 零售
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX",
    # 醫療
    "LLY", "UNH", "JNJ", "PFE",
    # 能源 / 工業
    "XOM", "CVX", "CAT", "BA",
]

_US_FULL_EXTRA = [
    "NFLX", "DIS", "CMCSA", "T", "VZ",
    "INTC", "QCOM", "TXN", "AMAT", "LRCX", "KLAC",
    "PG", "KO", "PEP", "ABNB", "UBER",
    "BLK", "SCHW", "AXP",
    "ABBV", "MRK", "TMO", "ABT",
    "GE", "RTX", "LMT", "DE",
    "BABA", "JD", "PDD",
    "COIN", "SQ", "PYPL",
    # 美股常用 ETF（指數／類股／商品／債）
    "SPY", "QQQ", "IWM", "VOO", "VTI", "ARKK",
    "SOXX", "SMH", "XLF", "XLK", "XLE", "XLV",
    "TLT", "GLD", "SLV", "USO",
]


_TW_CORE = [
    # 半導體龍頭與電子權值
    "2330.TW",  # 台積電
    "2454.TW",  # 聯發科
    "2317.TW",  # 鴻海
    "2308.TW",  # 台達電
    "3711.TW",  # 日月光投控
    "2382.TW",  # 廣達
    # 金融 / 電信權值
    "2882.TW",  # 國泰金
    "2881.TW",  # 富邦金
    "2412.TW",  # 中華電
    # ETF
    "0050.TW",  # 元大台灣 50
    "006208.TW",  # 富邦台灣 50
    "00878.TW",  # 國泰永續高股息
]

_TW_STANDARD_EXTRA = [
    # 半導體 II
    "2303.TW",  # 聯電
    "3034.TW",  # 聯詠
    "3008.TW",  # 大立光
    "6669.TW",  # 緯穎
    "3661.TW",  # 世芯-KY
    "2379.TW",  # 瑞昱
    # AI 伺服器 / 機殼 / 散熱
    "2376.TW",  # 技嘉
    "2357.TW",  # 華碩
    "2356.TW",  # 英業達
    "3017.TW",  # 奇鋐
    "6515.TW",  # 穎崴
    # 傳產 / 鋼鐵 / 塑化
    "1301.TW",  # 台塑
    "1303.TW",  # 南亞
    "2002.TW",  # 中鋼
    "1216.TW",  # 統一
    # 金融 II
    "2891.TW",  # 中信金
    "2884.TW",  # 玉山金
    "5880.TW",  # 合庫金
    # ETF II
    "00929.TW", "00919.TW", "0056.TW",
]

_TW_FULL_EXTRA = [
    # 電子零組件 / NB
    "2474.TW", "2354.TW", "3231.TW", "2353.TW", "2377.TW",
    # 半導體 / IC 設計 II
    "3037.TW", "8046.TW", "2360.TW", "5347.TWO", "2337.TW", "2344.TW",
    "2345.TW", "6770.TW", "2474.TW",
    # 生技 / 電信
    "1707.TW", "4904.TW", "6491.TW",
    # 航運 / 觀光
    "2603.TW", "2609.TW", "2610.TW",
    # 金融 III
    "2885.TW", "2883.TW", "2880.TW",
    # 能源 / 綠能 / 食品
    "9958.TW", "6505.TW", "9910.TW", "9921.TW",
    # 主要台股 ETF（高股息／半導體／科技）
    "00713.TW", "00701.TW", "00692.TW",
    "00881.TW", "00891.TW", "00892.TW", "00893.TW",
    "00919.TW", "00929.TW", "00935.TW", "00936.TW",
    "00939.TW", "00940.TW", "00941.TW", "00946.TW",
    "00947.TW", "00961.TW", "0056.TW",
    # 美債／公司債 ETF
    "00679B.TW", "00687B.TW", "00772B.TW",
]


def benchmark_for_market(market: str) -> str:
    """回傳該市場的基準指數代號（給 yfinance）。"""
    m = (market or "all").strip().lower()
    if m in ("tw", "台股", "taiwan", "tw_stocks"):
        return "^TWII"
    return "^GSPC"


def benchmark_for_symbol(symbol: str) -> str:
    s = (symbol or "").upper()
    if s.endswith(".TW") or s.endswith(".TWO"):
        return "^TWII"
    return "^GSPC"


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def universe(market: str, size: str = "標準 (~40)") -> list[str]:
    """
    market: 'all' | 'us' | 'tw'
    size  : UNIVERSE_SIZES 的其中一項

    「全 TW 上市」/「全 TW 上市+上櫃」會優先載入 data/universe_tw_*.json；
    若檔案缺失則退化為「完整」清單。
    """
    m = (market or "all").strip().lower()
    sz = (size or "標準 (~40)").strip()

    is_full_listed = sz.startswith("全 TW 上市") and "上櫃" not in sz
    is_full_all = sz.startswith("全 TW 上市") and "上櫃" in sz

    if m in ("tw", "台股", "taiwan", "tw_stocks"):
        if is_full_listed or is_full_all:
            listed = _load_json_keys(_LISTED_PATH)
            otc = _load_json_keys(_OTC_PATH) if is_full_all else []
            if listed or otc:
                return _dedup(listed + otc)
            # JSON 不存在 → 退化
        base = list(_TW_CORE)
        if sz.startswith("標準") or sz.startswith("完整"):
            base += _TW_STANDARD_EXTRA
        if sz.startswith("完整"):
            base += _TW_FULL_EXTRA
        return _dedup(base)

    if m in ("us", "美股", "us_stocks"):
        # 美股目前不提供「全市場」（Yahoo 對美股不適合 1k+ 檔次轟炸；可用「完整」即可）
        base = list(_US_CORE)
        if sz.startswith("標準") or sz.startswith("完整") or sz.startswith("全"):
            base += _US_STANDARD_EXTRA
        if sz.startswith("完整") or sz.startswith("全"):
            base += _US_FULL_EXTRA
        return _dedup(base)

    return _dedup(universe("us", size) + universe("tw", size))
