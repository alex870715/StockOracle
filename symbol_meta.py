"""
代號 ↔ 中／英文名稱對照（以常見台股／美股為主），與顯示格式工具。

DISPLAY_MODES 對應：
- "代號"          → "2330.TW"
- "名稱 代號"     → "台積電 2330.TW"
- "代號 名稱"     → "2330.TW 台積電"
- "名稱"          → "台積電"

若 data/universe_tw_listed.json / universe_tw_otc.json 存在
（由 tools/sync_tw_universe.py 產生），會自動合併到 NAME_MAP，
所以即使你選「全 TW 上市」也能看到名稱。
未收錄的代號會自動退化成「代號」呈現。
"""

from __future__ import annotations

import json
from pathlib import Path

DISPLAY_MODES = ["名稱 代號", "代號 名稱", "代號", "名稱"]

_DATA_DIR = Path(__file__).resolve().parent / "data"

# 台股（含 ETF）— 以 Yahoo Finance 後綴 .TW / .TWO 為 key
_TW_NAMES: dict[str, str] = {
    # 半導體
    "2330.TW": "台積電",
    "2303.TW": "聯電",
    "2454.TW": "聯發科",
    "2337.TW": "旺宏",
    "2344.TW": "華邦電",
    "3034.TW": "聯詠",
    "3661.TW": "世芯-KY",
    "2379.TW": "瑞昱",
    "5347.TWO": "世界先進",
    "6770.TW": "力積電",
    "3008.TW": "大立光",
    "3037.TW": "欣興",
    "8046.TW": "南電",
    "2360.TW": "致茂",
    # 電子 / 系統廠
    "2317.TW": "鴻海",
    "2308.TW": "台達電",
    "3711.TW": "日月光投控",
    "2382.TW": "廣達",
    "2376.TW": "技嘉",
    "2377.TW": "微星",
    "2353.TW": "宏碁",
    "2354.TW": "鴻準",
    "2356.TW": "英業達",
    "2357.TW": "華碩",
    "2474.TW": "可成",
    "3231.TW": "緯創",
    "6669.TW": "緯穎",
    "4938.TW": "和碩",
    "2345.TW": "智邦",
    "3017.TW": "奇鋐",
    "6515.TW": "穎崴",
    # 金融
    "2882.TW": "國泰金",
    "2881.TW": "富邦金",
    "2891.TW": "中信金",
    "2884.TW": "玉山金",
    "5880.TW": "合庫金",
    "2885.TW": "元大金",
    "2883.TW": "開發金",
    "2880.TW": "華南金",
    # 電信 / 傳產 / 鋼鐵 / 塑化 / 食品
    "2412.TW": "中華電",
    "4904.TW": "遠傳",
    "1301.TW": "台塑",
    "1303.TW": "南亞",
    "6505.TW": "台塑化",
    "2002.TW": "中鋼",
    "1216.TW": "統一",
    "9910.TW": "豐泰",
    "9921.TW": "巨大",
    "1707.TW": "葡萄王",
    "6491.TW": "晶碩",
    "9958.TW": "世紀鋼",
    # 航運 / 觀光
    "2603.TW": "長榮海運",
    "2609.TW": "陽明海運",
    "2610.TW": "華航",
    # 熱門台股 ETF
    "0050.TW": "元大台灣50",
    "006208.TW": "富邦台50",
    "0056.TW": "元大高股息",
    "00878.TW": "國泰永續高股息",
    "00713.TW": "元大台灣高息低波",
    "00701.TW": "國泰股利精選30",
    "00692.TW": "富邦公司治理",
    "00881.TW": "國泰台灣5G+",
    "00891.TW": "中信關鍵半導體",
    "00892.TW": "富邦台灣半導體",
    "00893.TW": "國泰智能電動車",
    "00919.TW": "群益台灣精選高息",
    "00929.TW": "復華台灣科技優息",
    "00935.TW": "野村臺灣新科技50",
    "00936.TW": "台新永續高息中小",
    "00939.TW": "統一台灣高息動能",
    "00940.TW": "元大臺灣價值高息",
    "00941.TW": "中信上游半導體",
    "00946.TW": "群益科技高息成長",
    "00947.TW": "中信小資高價30",
    "00961.TW": "元大全球未來通訊",
    # 債券 / 美債 ETF（常被當避險配對）
    "00679B.TW": "元大美債20年",
    "00687B.TW": "國泰20年美債",
    "00772B.TW": "中信高評級公司債",
}

# 美股（含 ETF）
_US_NAMES: dict[str, str] = {
    # 七巨頭 / 半導體
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta",
    "TSLA": "Tesla",
    "AVGO": "Broadcom",
    "AMD": "AMD",
    "TSM": "TSMC ADR",
    "ASML": "ASML",
    "MU": "Micron",
    "INTC": "Intel",
    "QCOM": "Qualcomm",
    "TXN": "Texas Instruments",
    "AMAT": "Applied Materials",
    "LRCX": "Lam Research",
    "KLAC": "KLA",
    # 軟體 / 雲端
    "ORCL": "Oracle",
    "CRM": "Salesforce",
    "ADBE": "Adobe",
    "NOW": "ServiceNow",
    "SNOW": "Snowflake",
    "PLTR": "Palantir",
    "NFLX": "Netflix",
    "DIS": "Disney",
    "CMCSA": "Comcast",
    "T": "AT&T",
    "VZ": "Verizon",
    # 金融
    "JPM": "JPMorgan",
    "GS": "Goldman Sachs",
    "MS": "Morgan Stanley",
    "BAC": "Bank of America",
    "V": "Visa",
    "MA": "Mastercard",
    "BLK": "BlackRock",
    "SCHW": "Charles Schwab",
    "AXP": "American Express",
    # 消費
    "WMT": "Walmart",
    "COST": "Costco",
    "HD": "Home Depot",
    "MCD": "McDonald's",
    "NKE": "Nike",
    "SBUX": "Starbucks",
    "PG": "P&G",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "ABNB": "Airbnb",
    "UBER": "Uber",
    # 醫療
    "LLY": "Eli Lilly",
    "UNH": "UnitedHealth",
    "JNJ": "Johnson & Johnson",
    "PFE": "Pfizer",
    "ABBV": "AbbVie",
    "MRK": "Merck",
    "TMO": "Thermo Fisher",
    "ABT": "Abbott",
    # 工業 / 能源 / 軍工
    "XOM": "ExxonMobil",
    "CVX": "Chevron",
    "CAT": "Caterpillar",
    "BA": "Boeing",
    "GE": "GE",
    "RTX": "RTX",
    "LMT": "Lockheed Martin",
    "DE": "Deere",
    # 中概 / 科技 / 加密
    "BABA": "阿里巴巴",
    "JD": "京東",
    "PDD": "拼多多",
    "COIN": "Coinbase",
    "SQ": "Block",
    "PYPL": "PayPal",
    # 美股常用 ETF
    "SPY": "SPDR S&P 500",
    "QQQ": "Invesco QQQ",
    "IWM": "Russell 2000 ETF",
    "VOO": "Vanguard S&P 500",
    "VTI": "Vanguard 全市場",
    "ARKK": "ARK Innovation",
    "SOXX": "iShares 半導體",
    "SMH": "VanEck 半導體",
    "XLF": "金融類股 ETF",
    "XLK": "科技類股 ETF",
    "XLE": "能源類股 ETF",
    "XLV": "醫療類股 ETF",
    "TLT": "20Y 美債 ETF",
    "GLD": "SPDR 黃金",
    "SLV": "白銀 ETF",
    "USO": "原油 ETF",
    # 指數本身
    "^GSPC": "S&P 500 指數",
    "^TWII": "台灣加權指數",
    "^IXIC": "Nasdaq 指數",
    "^DJI": "道瓊指數",
    "^VIX": "VIX 波動率",
}

def _load_json_map(filename: str) -> dict[str, str]:
    p = _DATA_DIR / filename
    if not p.exists():
        return {}
    try:
        return {str(k): str(v) for k, v in json.loads(p.read_text()).items()}
    except Exception:
        return {}


# 順序：先載 JSON（全市場名稱），再用內建精選蓋過去；確保我們手動寫的別名優先。
NAME_MAP: dict[str, str] = {
    **_load_json_map("universe_tw_listed.json"),
    **_load_json_map("universe_tw_otc.json"),
    **_TW_NAMES,
    **_US_NAMES,
}


def reload_names() -> int:
    """重新從 JSON 載入；UI 在執行同步後可呼叫此函式刷新。回傳載入後總筆數。"""
    global NAME_MAP
    NAME_MAP = {
        **_load_json_map("universe_tw_listed.json"),
        **_load_json_map("universe_tw_otc.json"),
        **_TW_NAMES,
        **_US_NAMES,
    }
    return len(NAME_MAP)


def display_name(symbol: str) -> str:
    """回傳對應中／英文名稱；查不到回空字串。"""
    if not symbol:
        return ""
    return NAME_MAP.get(symbol.strip().upper(), "")


def format_symbol(symbol: str, mode: str = "名稱 代號") -> str:
    sym = (symbol or "").strip()
    if not sym:
        return ""
    name = display_name(sym)
    if not name:
        return sym  # 未收錄退化為代號
    if mode == "代號":
        return sym
    if mode == "名稱":
        return name
    if mode == "代號 名稱":
        return f"{sym} {name}"
    return f"{name} {sym}"


def format_symbols(symbols: list[str], mode: str = "名稱 代號") -> list[str]:
    return [format_symbol(s, mode) for s in symbols]
