"""
StockOracle 雙語系統。

設計：
- `_DICT`：以 `section.key` 命名的字串字典，每個 key 都有 `zh` / `en` 兩種翻譯。
- `t(key, **fmt)`：依「當前語言」回傳字串；缺失 key 會 fallback 到 zh，再 fallback 到 key 本身。
- `set_lang(lang)` / `get_lang()`：透過 Streamlit session_state；非 streamlit 環境則退回模組級變數。
- `tier_label(tier_zh)`：把資料層仍是中文的 tier 名稱（強烈買進…）轉成當前語言顯示。
- `market_label(market_zh)`：把資料層的「台股／美股」轉成 TW / US 顯示。

模組刻意不 import streamlit 在 top-level，避免 unit test 不依賴 streamlit。
"""

from __future__ import annotations

from typing import Iterable

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

_FALLBACK_LANG = "zh"
_module_lang = DEFAULT_LANG  # streamlit 不可用時的退路


def _get_st_session_state():
    try:
        import streamlit as st  # 延遲 import，避免 test 環境失敗
        return st.session_state
    except Exception:
        return None


def get_lang() -> str:
    ss = _get_st_session_state()
    if ss is not None:
        return ss.get("lang", DEFAULT_LANG)
    return _module_lang


def set_lang(lang: str) -> None:
    global _module_lang
    if lang not in LANGS:
        lang = DEFAULT_LANG
    ss = _get_st_session_state()
    if ss is not None:
        ss["lang"] = lang
    _module_lang = lang


def t(key: str, **fmt) -> str:
    return t_lang(key, get_lang(), **fmt)


def t_lang(key: str, lang: str, **fmt) -> str:
    entry = _DICT.get(key)
    if not entry:
        return key
    s = entry.get(lang) or entry.get(_FALLBACK_LANG) or key
    if fmt:
        try:
            s = s.format(**fmt)
        except (KeyError, IndexError):
            pass
    return s


# -------------------------------- 跨檔工具 --------------------------------


_TIER_MAP_EN = {
    "強烈買進": "Strong Buy",
    "買進": "Buy",
    "偏多觀察": "Watch (bullish)",
    "中性": "Neutral",
    "中立": "Neutral",
    "減碼": "Reduce",
    "避開": "Avoid",
}


def tier_label(tier_zh: str | None) -> str:
    """資料層仍存中文 tier 名稱（強烈買進…），UI 端用這個函式轉換。"""
    if tier_zh is None:
        return "—"
    if get_lang() == "en":
        return _TIER_MAP_EN.get(str(tier_zh), str(tier_zh))
    return str(tier_zh)


def tier_options(tiers_zh: Iterable[str]) -> list[tuple[str, str]]:
    """給 multiselect 用：[(value=zh, display=t)...]。"""
    return [(z, tier_label(z)) for z in tiers_zh]


_MARKET_MAP_EN = {"美股": "US", "台股": "TW", "全部": "All"}


def market_label(market_zh: str | None) -> str:
    if market_zh is None:
        return "—"
    if get_lang() == "en":
        return _MARKET_MAP_EN.get(str(market_zh), str(market_zh))
    return str(market_zh)


# -------------------------------- 翻譯字典 --------------------------------


_DICT: dict[str, dict[str, str]] = {
    # ====== 通用 ======
    "common.refresh": {"zh": "🔄 重新計算", "en": "🔄 Recalculate"},
    "common.run": {"zh": "▶ 執行", "en": "▶ Run"},
    "common.reset": {"zh": "🗑 重置", "en": "🗑 Reset"},
    "common.market": {"zh": "市場", "en": "Market"},
    "common.us": {"zh": "美股", "en": "US"},
    "common.tw": {"zh": "台股", "en": "TW"},
    "common.all": {"zh": "全部", "en": "All"},
    "common.tier": {"zh": "等級", "en": "Tier"},
    "common.score": {"zh": "綜合分數", "en": "Composite Score"},
    "common.symbol": {"zh": "代號", "en": "Symbol"},
    "common.close": {"zh": "收盤", "en": "Close"},
    "common.shares": {"zh": "股數", "en": "Shares"},
    "common.notional": {"zh": "投入金額", "en": "Notional"},
    "common.weight_pct": {"zh": "權重%", "en": "Weight %"},
    "common.fee": {"zh": "手續費", "en": "Fee"},
    "common.volume_ratio": {"zh": "量比", "en": "Vol Ratio"},
    "common.short_term": {"zh": "短期", "en": "Short-term"},
    "common.short_strength": {"zh": "短期強度", "en": "ST Strength"},
    "common.advanced": {"zh": "進階", "en": "Advanced"},
    "common.no_trigger_today": {"zh": "（今日未觸發任何出場條件）", "en": "(No exit condition triggered today)"},
    "common.entry_rules": {"zh": "進場條件", "en": "Entry rules"},
    "common.exit_rules": {"zh": "出場條件", "en": "Exit rules"},

    # ====== App 標題 / 頂部 ======
    "app.title": {"zh": "StockOracle 選股 v2", "en": "StockOracle Stock Picker v2"},
    "app.page_title": {"zh": "StockOracle 選股 v2", "en": "StockOracle v2"},
    "app.subtitle": {
        "zh": "資料：Yahoo Finance（後復權）。本工具為研究示範，不構成投資建議。",
        "en": "Data: Yahoo Finance (auto-adjusted). For research demo only — not investment advice.",
    },

    # ====== Sidebar ======
    "sidebar.language": {"zh": "🌐 語言 / Language", "en": "🌐 Language"},
    "sidebar.market_section": {"zh": "市場 / 標的", "en": "Market / Universe"},
    "sidebar.market": {"zh": "市場", "en": "Market"},
    "sidebar.size": {"zh": "股票池規模", "en": "Universe size"},
    "sidebar.size_help": {
        "zh": "『全 TW』需先按下方『同步台股全市場清單』。",
        "en": "'TW (all listed)' requires syncing the full TW universe first (button below).",
    },
    "sidebar.full_tw_warn": {
        "zh": "尚未同步全市場名單；下方按一下『同步台股全市場清單』即可。",
        "en": "Full TW universe is not yet synced. Click 'Sync TW universe' below.",
    },
    "sidebar.full_tw_size_listed": {"zh": "上市約 1300 檔", "en": "~1300 listed names"},
    "sidebar.full_tw_size_all": {"zh": "上市+上櫃約 2300 檔", "en": "~2300 listed + OTC names"},
    "sidebar.full_tw_estimate": {
        "zh": "⏱ {est}，首次抓取可能 5–15 分鐘（之後走快取）。",
        "en": "⏱ {est}. First fetch may take 5–15 min, then served from cache.",
    },
    "sidebar.sync_tw": {"zh": "🔄 同步台股全市場清單", "en": "🔄 Sync TW universe"},
    "sidebar.sync_tw_help": {
        "zh": "從 TWSE 公開頁產生／覆寫白名單 JSON；**不需每天按**，僅在要更新上市／上櫃名單時執行即可。",
        "en": "Scrape TWSE to (re)generate JSON. **Not daily**—run when you need an updated listed/OTC roster.",
    },
    "sidebar.sync_status_btn": {"zh": "📋 顯示同步狀態", "en": "📋 Show sync status"},
    "sidebar.sync_running": {"zh": "正從 TWSE 抓取上市／上櫃清單…", "en": "Fetching listed/OTC universe from TWSE…"},
    "sidebar.sync_listed_ok": {"zh": "上市 ✓", "en": "Listed ✓"},
    "sidebar.sync_otc_ok": {"zh": "上櫃 ✓", "en": "OTC ✓"},
    "sidebar.sync_done": {"zh": "同步完成：{which}；NAME_MAP 共 {n} 筆", "en": "Sync complete: {which}; NAME_MAP has {n} entries"},
    "sidebar.sync_failed": {"zh": "同步失敗：{err}", "en": "Sync failed: {err}"},
    "sidebar.sync_status_msg": {
        "zh": "上市檔案：{listed}；上櫃檔案：{otc}",
        "en": "Listed file: {listed}; OTC file: {otc}",
    },
    "sidebar.exists": {"zh": "存在", "en": "exists"},
    "sidebar.missing": {"zh": "缺", "en": "missing"},
    "sidebar.custom_symbols": {"zh": "自訂代號（覆蓋上方）", "en": "Custom symbols (overrides above)"},
    "sidebar.custom_symbols_ph": {
        "zh": "例：AAPL,2330.TW 或 2330（純數字自動補 .TW）",
        "en": "e.g. AAPL,2330.TW or 2330 (numeric → auto add .TW)",
    },
    "sidebar.custom_symbols_help": {
        "zh": "逗號分隔；可省略 $；純數字 4–6 碼會自動加上 .TW。",
        "en": "Comma separated; $ optional; 4–6 digit numerics get .TW appended.",
    },
    "sidebar.history_section": {"zh": "歷史區間", "en": "Historical period"},
    "sidebar.period": {"zh": "資料抓取與圖表期間", "en": "Data & chart period"},
    "sidebar.period_help": {
        "zh": "影響指標可算的長度（MA200 需要 ≥ 200 根 K）；資金 / 風險設定改到「資產規劃」分頁。",
        "en": "Affects how far back indicators can compute (MA200 needs ≥200 bars). Capital / risk settings live in the 'Asset Planner' tab.",
    },
    "sidebar.period.6mo": {"zh": "6 個月", "en": "6 months"},
    "sidebar.period.1y": {"zh": "1 年", "en": "1 year"},
    "sidebar.period.2y": {"zh": "2 年", "en": "2 years"},
    "sidebar.period.5y": {"zh": "5 年", "en": "5 years"},
    "sidebar.strategy_section": {"zh": "策略", "en": "Strategy"},
    "sidebar.strategy_pick": {"zh": "選擇策略", "en": "Pick a strategy"},
    "sidebar.strategy_help": {
        "zh": "不同策略給不同的進出場條件、推薦等級與圖上的進場 / 出場標記。",
        "en": "Each strategy has its own entry/exit rules, recommendation tier, and chart markers.",
    },
    "sidebar.strategy_expander": {"zh": "策略說明", "en": "Strategy details"},
    "sidebar.display_section": {"zh": "顯示", "en": "Display"},
    "sidebar.symbol_display_mode": {"zh": "代號顯示模式", "en": "Symbol display mode"},
    "sidebar.symbol_display_help": {
        "zh": "例：『名稱 代號』→ 旺宏 2337.TW；未收錄名稱者顯示原代號。",
        "en": "e.g. 'Name Symbol' → 旺宏 2337.TW. Uncovered tickers show raw symbol.",
    },
    "sidebar.short_top": {"zh": "短期表格最多列數", "en": "Short-term table max rows"},
    "sidebar.short_top_help": {"zh": "0 = 不截斷", "en": "0 = no truncation"},
    "sidebar.force_refresh": {"zh": "忽略快取（強制重抓）", "en": "Bypass cache (force refetch)"},
    "sidebar.clear_cache_btn": {
        "zh": "🧹 清磁碟快取（修『線消失/訊號消失』）",
        "en": "🧹 Clear disk cache (fix 'lines/signals missing')",
    },
    "sidebar.clear_cache_done": {"zh": "已清除 {n} 個快取檔，下一輪會全部重抓。", "en": "Cleared {n} cache files; next run refetches everything."},

    # ====== 來源 ======
    "source.custom": {"zh": "自訂清單", "en": "Custom list"},

    # ====== 顯示模式 ======
    "display.name_symbol": {"zh": "名稱 代號", "en": "Name Symbol"},
    "display.symbol_only": {"zh": "代號", "en": "Symbol only"},
    "display.name_only": {"zh": "名稱", "en": "Name only"},

    # ====== 頂部 metric ======
    "top.market": {"zh": "市場", "en": "Market"},
    "top.source": {"zh": "代號來源", "en": "Source"},
    "top.range": {"zh": "區間", "en": "Range"},
    "top.last_data": {"zh": "資料截止", "en": "Data through"},
    "top.last_data_value": {"zh": "美 {us} / 台 {tw}", "en": "US {us} / TW {tw}"},
    "top.success_count": {"zh": "成功 / 全部", "en": "OK / Total"},
    "top.failed_list": {"zh": "⚠️ 失敗清單（{n}）", "en": "⚠️ Failed list ({n})"},

    # ====== Tabs ======
    "tabs.rank": {"zh": "📊 排名與篩選", "en": "📊 Rank & Filter"},
    "tabs.one": {"zh": "🔎 個股分析", "en": "🔎 Single Stock"},
    "tabs.short": {"zh": "⚡ 短期推薦", "en": "⚡ Short-term"},
    "tabs.plan": {"zh": "💼 資產規劃", "en": "💼 Asset Planner"},

    # ====== 排名與篩選 ======
    "rank.no_data": {"zh": "沒有成功取得任何標的資料，請更換市場或自訂代號。", "en": "No data fetched. Try a different market or custom symbols."},
    "rank.filter_market": {"zh": "市場", "en": "Market"},
    "rank.filter_tier": {"zh": "等級", "en": "Tier"},
    "rank.filter_min_score": {"zh": "最低綜合分數", "en": "Min composite score"},
    "rank.filter_min_vr": {"zh": "最低量比", "en": "Min vol ratio"},
    "rank.filter_only_short": {"zh": "只看含短期訊號", "en": "Only short-term signals"},
    "rank.caption": {
        "zh": "共 {n} 檔符合條件。點選下方代號可跳到「個股分析」。 ｜「綜合分數」是跨策略共用的多因子評分（趨勢+動能+量+RS），與個股頁的「策略命中度」是不同維度。",
        "en": "{n} match. Click a symbol below to open 'Single Stock'.  |  Composite Score is a multi-factor cross-strategy score (trend+momentum+vol+RS); the 'Strategy hit rate' on the single-stock page is a different metric.",
    },
    "rank.toast_jump": {"zh": "已切換到 {label}", "en": "Switched to {label}"},
    "rank.download_csv": {"zh": "下載目前篩選結果 CSV", "en": "Download filtered CSV"},

    # ====== 短期 tab ======
    "short.empty": {
        "zh": "今日無滿足「漲幅 ≥ 0.8 ATR + 量比 ≥ 1.5 + 收高」的短期訊號。",
        "en": "No short-term signal today (gain ≥ 0.8 ATR, vol ratio ≥ 1.5, close high in range).",
    },
    "short.download_csv": {"zh": "下載短期表 CSV（完整）", "en": "Download short-term CSV (full)"},

    # ====== 個股分析 ======
    "one.no_data": {"zh": "沒有可用標的，請先成功抓資料。", "en": "No symbols available. Please fetch data first."},
    "one.search": {"zh": "搜尋標的（代號或中文名稱皆可，例：2330、台積、TSM、AAPL）", "en": "Search (symbol or name, e.g. 2330, TSM, AAPL)"},
    "one.search_ph": {"zh": "留空則顯示全部", "en": "Empty = show all"},
    "one.search_no_match": {"zh": "找不到符合「{q}」的標的；保留全清單。", "en": "No match for '{q}'; falling back to full list."},
    "one.pick": {"zh": "選擇標的（{shown} / {total} 檔）", "en": "Pick symbol ({shown} / {total})"},
    "one.ma_label": {"zh": "均線（可多選）", "en": "Moving averages (multi)"},
    "one.ma_help": {
        "zh": "MA 期數需至少有對應根數才會畫出（例：MA200 需要 200 根 K）。",
        "en": "Each MA needs that many bars to draw (e.g. MA200 needs 200 bars).",
    },
    "one.show_bb": {"zh": "布林通道", "en": "Bollinger"},
    "one.log_axis": {"zh": "Log 軸", "en": "Log scale"},
    "one.show_macd": {"zh": "MACD", "en": "MACD"},
    "one.show_rsi": {"zh": "RSI", "en": "RSI"},
    "one.show_signals": {"zh": "策略進出場標記", "en": "Strategy markers"},
    "one.no_history": {"zh": "無法取得 {sym} 的歷史資料。", "en": "No history available for {sym}."},
    "one.metric.composite": {"zh": "多因子綜合分", "en": "Composite Score"},
    "one.metric.composite_help": {
        "zh": "跨策略共用的『**綜合面**』評分：趨勢（MA20/50/200 排列）、動能（MACD、RSI）、量能（成交量比）、相對強度（vs 大盤）等加權合成。\n\n與下方『策略命中度』是不同維度——這是**整體技術面**好不好，下面是**所選策略**的進出條件成立度。",
        "en": "Cross-strategy multi-factor score: trend (MA20/50/200 stack), momentum (MACD, RSI), volume, relative strength.\n\nDifferent from 'Strategy hit rate' below — this is overall technical health; below is **selected strategy's** entry-condition fulfillment.",
    },
    "one.metric.tier": {"zh": "綜合等級", "en": "Composite tier"},
    "one.metric.tier_help": {
        "zh": "多因子綜合分對應的等級（強烈買進 / 買進 / 偏多觀察 / 中立 / 減碼）。",
        "en": "Tier mapped from composite score (Strong Buy / Buy / Watch / Neutral / Reduce).",
    },
    "one.metric.today_pct": {"zh": "今日 %", "en": "Today %"},
    "one.metric.dist_52w_high": {"zh": "距 52 週高", "en": "vs 52W high"},
    "one.strategy_metric_label": {"zh": "策略：{label}", "en": "Strategy: {label}"},
    "one.metric.strategy_score": {"zh": "策略命中度（0–10）", "en": "Strategy hit rate (0–10)"},
    "one.metric.strategy_score_help": {
        "zh": "**所選策略**的進場條件命中數（不是綜合分）。\n\n10 分代表所有進場條件全部成立、可以執行；3 分以下代表大部分沒成立、不該動作。",
        "en": "Number of entry conditions met for **selected strategy** (not composite).\n\n10 = all conditions met → actionable; <3 = mostly missed → stand aside.",
    },
    "one.metric.entry_today": {"zh": "今日進場？", "en": "Entry today?"},
    "one.metric.exit_today": {"zh": "今日出場？", "en": "Exit today?"},
    "one.metric.met": {"zh": "✅ 成立", "en": "✅ Met"},
    "one.metric.exit_met": {"zh": "🔻 成立", "en": "🔻 Triggered"},
    "one.metric.dash": {"zh": "—", "en": "—"},
    "one.expander.rules": {"zh": "當前策略條件（{label}）", "en": "Current strategy conditions ({label})"},
    "one.rules.hits": {"zh": "✓ 命中：{hits}", "en": "✓ Met: {hits}"},
    "one.rules.misses": {"zh": "✗ 未命中：{misses}", "en": "✗ Missed: {misses}"},
    "one.rules.exit_today_triggered": {"zh": "⚠️ 今日已觸發：{reasons}", "en": "⚠️ Triggered today: {reasons}"},
    "one.caption.see_planner": {
        "zh": "資金 / 風險 / 部位試算改到「💼 資產規劃」分頁。",
        "en": "Capital / risk / position sizing live in the '💼 Asset Planner' tab.",
    },

    "app.fetch_spin": {
        "zh": "正在抓取 {n} 檔並計算指標（可能稍久）…",
        "en": "Fetching {n} symbols and computing indicators…",
    },
    "app.fetch_phase_dl": {"zh": "下載報價", "en": "Download"},
    "app.fetch_phase_calc": {"zh": "計算指標", "en": "Indicators"},
    "app.fetch_status": {
        "zh": "**{cur}/{total}** · {phase} · {sym}",
        "en": "**{cur}/{total}** · {phase} · {sym}",
    },
    "app.report_from_cache": {
        "zh": "已從本機載入上次排名（略過整包下載）。按頂部「重新計算」可強制連網更新。",
        "en": "Loaded last rankings from disk (skipped bulk download). Use top **Refresh** to refetch.",
    },

    # ====== Chart（個股圖 · K 區間／視窗）======
    "chart.quick_hint": {"zh": "📈 日／週／月 K 僅視覺週期；評分仍以側欄日線為準。", "en": "📈 Daily/weekly/monthly are visual; scoring uses sidebar daily."},
    "chart.mode.daily": {"zh": "日線", "en": "Daily"},
    "chart.mode.intra": {"zh": "盤中（分／時 K）", "en": "Intraday (min/h)"},
    "chart.expander.title": {"zh": "ℹ️ 圖表說明與資料限制", "en": "ℹ️ Chart notes & data limits"},
    "chart.expander.intro": {
        "zh": "下方評分／策略仍以**日線（與主榜相同 period）**計算；圖表可切換**日／週／月 K**僅為視覺參考。週／月區間時指標依該 K 線重算。進出場標記僅在**日 K**顯示。資料來源 Yahoo，可能有缺根。",
        "en": "**Scores/strategy remain daily** (sidebar period); **D/W/M bars are visual.** Indicators are recomputed per bar interval. Strategy entry/exit marks only on **daily** bars. Yahoo — gaps possible.",
    },
    "chart.iv_label": {"zh": "K線區間", "en": "Bar interval"},
    "chart.bar_interval_label": {"zh": "圖：K 線週期", "en": "Chart: bar interval"},
    "chart.period_label": {"zh": "抓取資料範圍（Yahoo period）", "en": "Fetch range (Yahoo period)"},
    "chart.xview_label": {"zh": "圖上一開始顯示多久（仍可捲輪縮放、工具列🔍框選縮放、雙擊還原）", "en": "Initial window (scroll to zoom; toolbar 🔍 box-zoom; double-click resets)"},

    "chart.interval.1m": {"zh": "1 分鐘 K", "en": "1m"},
    "chart.interval.2m": {"zh": "2 分鐘 K", "en": "2m"},
    "chart.interval.5m": {"zh": "5 分鐘 K", "en": "5m"},
    "chart.interval.15m": {"zh": "15 分鐘 K", "en": "15m"},
    "chart.interval.30m": {"zh": "30 分鐘 K", "en": "30m"},
    "chart.interval.60m": {"zh": "60 分鐘 K", "en": "60m"},
    "chart.interval.90m": {"zh": "90 分鐘 K", "en": "90m"},
    "chart.interval.1h": {"zh": "1 小時 K", "en": "1h"},
    "chart.interval.1d": {"zh": "日", "en": "Daily"},
    "chart.interval.1wk": {"zh": "週", "en": "Weekly"},
    "chart.interval.1mo": {"zh": "月", "en": "Monthly"},

    "chart.period.5d": {"zh": "5 個交易日區間", "en": "~5 sessions"},
    "chart.period.1d": {"zh": "1 個交易日", "en": "~1 session"},
    "chart.period.1mo": {"zh": "約 1 個月", "en": "~1 month"},
    "chart.period.3mo": {"zh": "約 3 個月", "en": "~3 months"},
    "chart.period.6mo": {"zh": "約 6 個月", "en": "~6 months"},
    "chart.period.ytd": {"zh": "今年以來（YTD）", "en": "YTD"},
    "chart.period.1y": {"zh": "約 1 年", "en": "~1 year"},
    "chart.period.2y": {"zh": "約 2 年", "en": "~2 years"},
    "chart.period.5y": {"zh": "約 5 年", "en": "~5 years"},
    "chart.period.10y": {"zh": "約 10 年", "en": "~10 years"},
    "chart.period.max": {"zh": "可查最長區間（Max）", "en": "Max"},
    "chart.bars_combo": {"zh": "{interval}｜資料 {period}", "en": "{interval} · Fetch {period}"},

    "chart.xview.all": {"zh": "顯示已抓取的範圍（全部）", "en": "Full fetched range"},
    "chart.xview.last_4h": {"zh": "最近約 4 小時", "en": "~4 hours"},
    "chart.xview.last_8h": {"zh": "最近約 8 小時", "en": "~8 hours"},
    "chart.xview.last_24h": {"zh": "最近約 24 小時／1 日", "en": "~24h / 1 day"},
    "chart.xview.last_3d": {"zh": "最近約 3 日", "en": "~3 days"},
    "chart.xview.last_5d": {"zh": "最近約 5 日／1 交易週", "en": "~5 days"},
    "chart.xview.last_2w": {"zh": "最近約 2 週", "en": "~2 weeks"},
    "chart.xview.last_1mo": {"zh": "最近約 1 個月", "en": "~1 month"},
    "chart.xview.last_3mo": {"zh": "最近約 3 個月", "en": "~3 months"},
    "chart.xview.last_6mo": {"zh": "最近約 6 個月", "en": "~6 months"},
    "chart.xview.ytd": {"zh": "今年至今（依最後資料日回溯）", "en": "YTD (from last bar)"},
    "chart.xview.last_1y": {"zh": "最近約 1 年", "en": "~1 year"},
    "chart.xview.last_2y": {"zh": "最近約 2 年", "en": "~2 years"},
    "chart.xview.last_5y": {"zh": "最近約 5 年", "en": "~5 years"},
    "chart.xview.last_10y": {"zh": "最近約 10 年", "en": "~10 years"},

    # ====== Asset Planner ======
    "plan.no_candidates": {"zh": "沒有候選資料；先到「排名與篩選」確認有跑出標的。", "en": "No candidates. Check 'Rank & Filter' first."},
    "plan.section_a": {"zh": "### A. 目標設定", "en": "### A. Goal"},
    "plan.cur_cap": {"zh": "目前資金", "en": "Current capital"},
    "plan.tgt_cap": {"zh": "目標資金", "en": "Target capital"},
    "plan.horizon": {"zh": "期間（年）", "en": "Horizon (years)"},
    "plan.market_base": {"zh": "基準市場", "en": "Benchmark market"},
    "plan.required_cagr": {"zh": "所需年化 CAGR", "en": "Required CAGR"},
    "plan.section_b": {"zh": "### B. 配置建議", "en": "### B. Allocation"},
    "plan.risk_profile": {"zh": "風險偏好", "en": "Risk profile"},
    "plan.profile_caption": {
        "zh": "**{label}**：現金緩衝 {cb:.0f}% / 最多 {n} 檔 / 單檔 ≤ {cap:.0f}% / 單筆風險 ≤ {rt:.2f}% / ATR 停損 {sm:.1f}× / {tp:.1f}R 停利。",
        "en": "**{label}**: cash buffer {cb:.0f}% / max {n} positions / per-pos ≤ {cap:.0f}% / risk-per-trade ≤ {rt:.2f}% / ATR stop {sm:.1f}× / take-profit {tp:.1f}R.",
    },
    "plan.pick_mode": {"zh": "選股方式", "en": "Pick mode"},
    "plan.pick_auto": {"zh": "自動 Top N", "en": "Auto Top N"},
    "plan.pick_manual": {"zh": "手動勾選", "en": "Manual"},
    "plan.top_n": {"zh": "Top N", "en": "Top N"},
    "plan.manual_pick": {"zh": "勾選想配置的標的（已存的選擇換偏好時不會被清掉）", "en": "Pick stocks (selections persist when you change profile)"},
    "plan.clear_picks_btn": {"zh": "🗑 清空選股", "en": "🗑 Clear picks"},
    "plan.clear_picks_help": {"zh": "一鍵清掉手動勾選清單", "en": "Clear all manual picks"},
    "plan.over_cap_warn": {
        "zh": "已選 **{cnt} 檔**，超出「{label}」風險偏好的上限 **{cap} 檔**。系統只會用前 {cap} 檔做配置；要改可以：(1) 升風險偏好、(2) 自己 ✕ 掉幾檔、或 (3) 按右邊「🗑 清空選股」重來。",
        "en": "Picked **{cnt}**, exceeds '{label}' cap of **{cap}**. Only the top {cap} are used. Either (1) raise risk profile, (2) deselect some, or (3) click '🗑 Clear picks'.",
    },
    "plan.picked_count": {"zh": "已選 {cnt} / {cap} 檔（上限由風險偏好決定）。", "en": "Picked {cnt} / {cap} (cap from risk profile)."},
    "plan.allow_fractional": {"zh": "台股零股交易", "en": "TW fractional shares"},
    "plan.allow_fractional_help": {
        "zh": "ON → 台股可買 1 股；OFF → 必須買整張（1 張 = 1000 股，小資金常被擋光）。",
        "en": "ON → 1-share trades allowed; OFF → must buy 1 lot = 1000 shares (small accounts often blocked).",
    },
    "plan.empty_alloc_warn": {"zh": "配置出空清單；請改變風險偏好、勾更多標的、或開啟零股。", "en": "Allocation came out empty. Try a different risk profile, pick more stocks, or enable fractional."},
    "plan.col.display": {"zh": "顯示", "en": "Display"},
    "plan.col.weight": {"zh": "權重%", "en": "Weight %"},
    "plan.col.shares": {"zh": "股數", "en": "Shares"},
    "plan.col.notional": {"zh": "投入金額", "en": "Notional"},
    "plan.col.stop": {"zh": "停損價", "en": "Stop price"},
    "plan.col.tp": {"zh": "停利價", "en": "TP price"},
    "plan.col.atr_pct": {"zh": "ATR%", "en": "ATR %"},
    "plan.col.risk": {"zh": "單筆風險$", "en": "Risk $"},
    "plan.col.score": {"zh": "策略分數", "en": "Strategy score"},
    "plan.metric.invested": {"zh": "實際投入", "en": "Invested"},
    "plan.metric.cash": {"zh": "現金", "en": "Cash"},
    "plan.metric.total_risk": {"zh": "總風險預算", "en": "Total risk"},
    "plan.metric.total_risk_help": {"zh": "假設所有持股都觸停損的總損失上限。", "en": "Max loss if every position hits stop."},
    "plan.metric.n_pos": {"zh": "持股數", "en": "Positions"},
    "plan.section_c": {"zh": "### C. 再平衡規則", "en": "### C. Rebalance rules"},
    "plan.rb_stock": {"zh": "**🎯 個股層級**", "en": "**🎯 Per-stock**"},
    "plan.rb_stock_atr": {"zh": "跌破 ATR 停損 → 立即出場", "en": "Hit ATR stop → exit immediately"},
    "plan.rb_stock_tp": {"zh": "漲到 {r:.1f}R 停利 → 立即出場", "en": "Hit {r:.1f}R take-profit → exit immediately"},
    "plan.rb_port": {"zh": "**📦 組合層級**", "en": "**📦 Portfolio**"},
    "plan.rb_port_dd": {"zh": "帳戶較高點回撤 ≥", "en": "Drawdown from high ≥"},
    "plan.rb_port_dd_pct": {"zh": "回撤 %", "en": "Drawdown %"},
    "plan.rb_port_dd_help": {"zh": "觸發 → 全部持股砍半轉現金", "en": "Trigger → cut all positions in half, hold cash"},
    "plan.rb_port_tp": {"zh": "帳戶較起點漲 ≥", "en": "Total gain ≥"},
    "plan.rb_port_tp_pct": {"zh": "漲幅 %", "en": "Gain %"},
    "plan.rb_port_tp_help": {"zh": "觸發 → 把所有持股砍 1/3 落袋", "en": "Trigger → trim 1/3 of every position"},
    "plan.rb_time": {"zh": "**⏱ 時間層級**", "en": "**⏱ Time-based**"},
    "plan.rb_time_on": {"zh": "例行重排 N 天一次", "en": "Routine rebalance every N days"},
    "plan.rb_time_n": {"zh": "天數", "en": "Days"},
    "plan.rb_time_n_help": {
        "zh": "到期就用當日收盤照原本權重重排",
        "en": "On schedule, rebalance to target weights at that day's close",
    },
    "plan.section_d": {"zh": "### D. 交易成本與回測假設", "en": "### D. Transaction costs & assumptions"},
    "plan.fee_bps": {"zh": "手續費 (bps)", "en": "Fee (bps)"},
    "plan.fee_bps_help": {
        "zh": "買 / 賣各收一次。台股全額 14.25 bps，多數券商打 5 折 ≈ 7 bps；美股零佣金 = 0。",
        "en": "Charged on buy & sell. TW full rate 14.25 bps (most brokers ~7 bps after discount); US is typically zero.",
    },
    "plan.tax_bps_tw": {"zh": "賣方證交稅 (bps)", "en": "Sell tax (bps, TW)"},
    "plan.tax_bps_us": {"zh": "賣方規費 (bps)", "en": "Sell fee (bps, US)"},
    "plan.tax_bps_help": {
        "zh": "台股賣方一律 30 bps（ETF 10 bps 暫不細分）；美股 SEC 費 ≈ 0.3 bps。",
        "en": "TW sellers: flat 30 bps (ETFs 10 bps, not split here). US SEC fee ≈ 0.3 bps.",
    },
    "plan.slip_bps": {"zh": "滑價 (bps)", "en": "Slippage (bps)"},
    "plan.slip_bps_help": {
        "zh": "進場吃較貴、出場吃較便宜。流動性差就調高。",
        "en": "Buys execute slightly higher, sells slightly lower. Bump up if liquidity is poor.",
    },
    "plan.use_wf": {"zh": "Walk-forward 換股", "en": "Walk-forward rebalance"},
    "plan.use_wf_help": {
        "zh": "✅ 推薦：每次再平衡時用『當天』的策略分數重新挑 Top N（避免 look-ahead bias）。\n關掉 → 持股不換、只調權重。",
        "en": "✅ Recommended: at each rebalance, re-pick Top N using *that day's* strategy score (no look-ahead).\nOff → keep the same stocks; only adjust weights.",
    },
    "plan.adjust_caption": {
        "zh": "ℹ️ 收盤價已使用 yfinance `auto_adjust=True`：所有歷史 OHLC 已自動還原**現金股利、配股、分割**，回測 NAV 直接代表**含股息的總報酬**，不需要再額外加股息（雙重計算會 over-count）。",
        "en": "ℹ️ Prices use yfinance `auto_adjust=True`: historical OHLC is back-adjusted for **cash dividends, splits, and stock dividends**, so the NAV is the **total return including dividends**. Don't add dividends separately (would double-count).",
    },
    "plan.pool_size": {"zh": "候選池大小（walk-forward 換股）", "en": "Candidate pool size (walk-forward)"},
    "plan.pool_size_help": {
        "zh": "每次再平衡時可在這幾檔裡挑當天 Top N。越大越貼近真實，但要抓更多歷史會慢。",
        "en": "At each rebalance, picks the day's Top N from this many candidates. Larger is more realistic but slower.",
    },
    "plan.pool_size_caption": {
        "zh": "候選池 = 排名表前 {n} 檔（會合併你目前 plan 內的標的，去重後送進回測）。",
        "en": "Pool = top {n} from ranking table (merged with current plan symbols, deduped).",
    },
    "plan.section_e": {"zh": "### E. 快速回測（套用以上配置 + 規則 + 成本）", "en": "### E. Backtest (apply allocation + rules + costs)"},
    "plan.run_btn": {"zh": "▶ 執行回測", "en": "▶ Run backtest"},
    "plan.spinner_run": {"zh": "撈 {n} 檔歷史並跑 walk-forward 回測中…", "en": "Fetching history for {n} symbols and running walk-forward backtest…"},
    "plan.stale_warn": {
        "zh": "⚠️ 你已修改了 A~D 區的參數，目前顯示的是**舊**回測結果。若要套用新設定請按上方「▶ 執行回測」重新跑。",
        "en": "⚠️ Parameters in A–D changed; the result below is **stale**. Click '▶ Run backtest' to re-run with new settings.",
    },
    "plan.bt.end_nav": {"zh": "結束 NAV", "en": "Final NAV"},
    "plan.bt.cagr": {"zh": "年化報酬 (CAGR)", "en": "CAGR"},
    "plan.bt.cagr_help": {"zh": "已扣手續費 / 證交稅 / 滑價，以及含股息的總報酬。", "en": "Net of fees/tax/slippage and includes dividends (total return)."},
    "plan.bt.sharpe": {"zh": "Sharpe", "en": "Sharpe"},
    "plan.bt.mdd": {"zh": "最大回撤 (MDD)", "en": "Max drawdown (MDD)"},
    "plan.bt.trades_winrate": {"zh": "交易 / 勝率", "en": "Trades / Win rate"},
    "plan.bt.fees": {"zh": "累計交易成本", "en": "Total fees"},
    "plan.bt.fees_help": {"zh": "手續費 + 證交稅 + 滑價合計。", "en": "Sum of fees + tax + slippage."},
    "plan.bt.nav_label": {"zh": "本配置 NAV", "en": "Plan NAV"},
    "plan.bt.bench_label": {"zh": "基準 {label}（同期間）", "en": "Benchmark {label} (same period)"},
    "plan.bt.start_annot": {"zh": "起始 {money}", "en": "Start {money}"},
    "plan.bt.yaxis": {"zh": "帳戶 NAV ($)", "en": "NAV ($)"},
    "plan.bt.event_log": {
        "zh": "📋 事件記錄（{n} 筆，包含個股停損/停利、組合再平衡）",
        "en": "📋 Event log ({n} entries: stops, take-profits, rebalances)",
    },
    "plan.bt.period_caption": {
        "zh": "回測期間：{start} 到 {end}（共 {n} 個交易日）",
        "en": "Backtest window: {start} → {end} ({n} trading days)",
    },
    "plan.bt.empty_warn": {
        "zh": "回測沒有產出 NAV — 通常是候選之間時間軸交集太短。可換掉新上市的標的後重試。",
        "en": "Backtest produced no NAV — usually because the date intersection is too short. Try removing recently-listed symbols.",
    },

    # ====== 設定 expander（在 sidebar 底部）======
    "settings.section": {"zh": "⚙️ 設定", "en": "⚙️ Settings"},
    "settings.section_help": {
        "zh": "語言、顯示樣式、快取等偏好設定。",
        "en": "Language, display preferences, cache control.",
    },

    # ====== 自訂策略 ======
    "custom.section": {"zh": "✏️ 自訂策略", "en": "✏️ Custom strategy"},
    "custom.section_caption": {
        "zh": "用下拉條件組出你的策略；ALL 命中才視為今日進場/出場。下方可切換為文字表達式。",
        "en": "Combine dropdown conditions; ALL must be true for today entry/exit. Switch to expression mode below for advanced.",
    },
    "custom.enable": {"zh": "啟用自訂策略", "en": "Enable custom strategy"},
    "custom.enable_help": {
        "zh": "啟用後會在『策略』下拉中多一檔「我的策略」。內建 5 套策略仍可選。",
        "en": "When enabled, adds a 'My Strategy' option to the strategy picker. Built-in 5 strategies still available.",
    },
    "custom.name": {"zh": "策略名稱", "en": "Strategy name"},
    "custom.mode": {"zh": "編輯模式", "en": "Edit mode"},
    "custom.mode_form": {"zh": "表單組合（推薦）", "en": "Form (recommended)"},
    "custom.mode_expr": {"zh": "文字表達式（進階）", "en": "Expression (advanced)"},
    "custom.entry_block": {"zh": "進場條件（全部成立才進）", "en": "Entry conditions (all must be true)"},
    "custom.exit_block": {"zh": "出場條件（任一成立即出）", "en": "Exit conditions (any triggers exit)"},
    "custom.metric": {"zh": "指標", "en": "Metric"},
    "custom.op": {"zh": "比較", "en": "Compare"},
    "custom.value": {"zh": "比較值", "en": "Value"},
    "custom.against": {"zh": "比較對象", "en": "Compare with"},
    "custom.against_value": {"zh": "數值", "en": "Number"},
    "custom.against_metric": {"zh": "另一個指標", "en": "Another metric"},
    "custom.add_entry": {"zh": "➕ 加進場條件", "en": "➕ Add entry"},
    "custom.add_exit": {"zh": "➕ 加出場條件", "en": "➕ Add exit"},
    "custom.remove": {"zh": "✕", "en": "✕"},
    "custom.max_hold": {"zh": "最大持有天數（0 = 不限）", "en": "Max hold days (0 = unlimited)"},
    "custom.expr_entry": {"zh": "進場表達式", "en": "Entry expression"},
    "custom.expr_exit": {"zh": "出場表達式", "en": "Exit expression"},
    "custom.expr_help": {
        "zh": "可用：close, open, high, low, volume, ma5/10/20/50/60/120/200, rsi14, atr14, atr14_pct, vol_60d_ann, mdd_60d, dist_to_52w_high_pct, volume_ratio, macd_hist, ret_1d, day_close_loc。\n運算子：> < >= <= == != and or not 與括號。\n例：close > ma20 and rsi14 < 70 and volume_ratio > 1.5",
        "en": "Available: close, open, high, low, volume, ma5/10/20/50/60/120/200, rsi14, atr14, atr14_pct, vol_60d_ann, mdd_60d, dist_to_52w_high_pct, volume_ratio, macd_hist, ret_1d, day_close_loc.\nOperators: > < >= <= == != and or not + parentheses.\nExample: close > ma20 and rsi14 < 70 and volume_ratio > 1.5",
    },
    "custom.expr_invalid": {"zh": "表達式無效：{err}", "en": "Invalid expression: {err}"},
    "custom.no_conds": {"zh": "尚未設定任何條件，自訂策略不會被列入策略選單。", "en": "No conditions yet — custom strategy is not added to the picker."},
    "custom.preview_label": {"zh": "我的策略", "en": "My Strategy"},
    "custom.op.gt": {"zh": ">", "en": ">"},
    "custom.op.gte": {"zh": "≥", "en": "≥"},
    "custom.op.lt": {"zh": "<", "en": "<"},
    "custom.op.lte": {"zh": "≤", "en": "≤"},
    "custom.op.eq": {"zh": "=", "en": "="},
    "custom.op.cross_above": {"zh": "向上穿越", "en": "Cross above"},
    "custom.op.cross_below": {"zh": "向下跌破", "en": "Cross below"},

    # ====== 持股健檢 ======
    "tabs.holdings": {"zh": "🏥 持股健檢", "en": "🏥 Portfolio Health"},
    "hold.section_title": {"zh": "🩺 持股健檢", "en": "🩺 Portfolio Health Check"},
    "hold.intro": {
        "zh": "不一定要用 CSV：在同一區塊內編輯表格與「剩餘現金」，再按區塊底「▶ 執行健檢」一次送出計算；或上傳 CSV 後按「確認載入」。資料僅存在 session，可匯出備份。",
        "en": "Edit the holdings grid + cash in the same block below, then **Run health check** once — or CSV import via 'Import'. Session-only — export as backup.",
    },
    "hold.editor_caption": {
        "zh": "表格與「剩餘現金」在下面同一區：**改完資料後請按區塊底「▶ 執行健檢」一次**即可套用並計算，不必為了送出再編一次。按「新增空白列」或表格下方 ➕ 加列手動填入；台股可輸入 2330（會自動加 .TW）。",
        "en": "Editor + cash are in one form: **submit with '▶ Run health check' at the bottom** after edits — no duplicate entry. Use 'Add blank row' or grid ➕. TW ticker 2330 auto-appends .TW.",
    },
    "hold.import_confirm": {"zh": "📥 確認載入 CSV", "en": "📥 Import CSV"},
    "hold.import_help_button": {
        "zh": "選好 CSV 後按此載入表格（可避免重複自動匯入）。",
        "en": "Select a CSV then click — avoids repeated auto-import on every rerun.",
    },
    "hold.add_blank_row": {"zh": "➕ 新增空白列（手動建檔）", "en": "➕ Add blank row"},
    "hold.add_blank_hint": {
        "zh": "適合不使用 Excel：一列一標的填完再跑健檢。",
        "en": "For manual entry — one symbol per row, then Run health check.",
    },
    "hold.col.symbol": {"zh": "代號", "en": "Symbol"},
    "hold.col.shares": {"zh": "股數", "en": "Shares"},
    "hold.col.avg_cost": {"zh": "均價", "en": "Avg cost"},
    "hold.col.note": {"zh": "備註", "en": "Note"},
    "hold.pos.cash": {"zh": "現股", "en": "Cash"},
    "hold.pos.margin": {"zh": "融資", "en": "Margin"},
    "hold.col.position": {"zh": "部位類型", "en": "Position type"},
    "hold.col.margin_finance_pct": {"zh": "融資借款占進場％", "en": "Financed % of cost"},
    "hold.col.margin_interest_pct": {"zh": "融資年利率％", "en": "Margin rate % APY"},
    "hold.col.estimated_margin_principal": {"zh": "推估欠款（本）", "en": "Est. margin principal"},
    "hold.col.margin_equity": {"zh": "融資權益比％（估）", "en": "Margin equity % (est)"},
    "hold.caption.margin_proxy": {
        "zh": "「融資權益比％」＝（市值 − **推估欠款**）÷ 市值 × 100（單檔近似值，≠ 券商整戶維持率）。推估欠款 ＝ 股數 × 均價 × **融資借款占進場％**；未填成數時內預設 **60**（六成融資口語）。",
        "en": "Estimated equity % = (mkt − estimated loan) / mkt × 100 (per-symbol proxy, ≠ broker margin). Estimated loan ≈ shares×avg_cost×(finance %); blank → assumes **60%** financed.",
    },
    "hold.caption.position_codes": {
        "zh": "部位類型：`cash`＝現股、`margin`＝融資（表格與 CSV 皆用英文代碼）。下方可再看融資估算說明。",
        "en": "Position column: `cash` vs `margin` (same codes in CSV). See margin notes below.",
    },
    "hold.caption.margin_finance_pct_help": {
        "zh": "僅 `margin` 有效：欠款本金 ≈ **股數×均價×此％**。常見六成融請填 **60**；留白則自動用 **60**。",
        "en": "`margin` only: principal ≈ **shares×avg×%/100**. Classic 60/40 margin → enter **60**; blank → **60** default.",
    },
    "hold.caption.margin_interest_pct_help": {
        "zh": "選填：券商對帳之**名目年化利率％**（只顯示參考，不會改算權益比）。現股請留白。",
        "en": "Optional: nominal **APR %** from broker (display only — does not change equity math).",
    },
    "hold.caption.margin_inputs_intro": {
        "zh": "融資列請填：**融資借款占進場％**（非金額）與可選的年利率％；欠款由程式用股數／均價推算。現股請留空。",
        "en": "For margin legs: finance **% of entry cost**, optional **APR %**; leave blank cash rows.",
    },
    "hold.cash_label": {"zh": "剩餘現金", "en": "Cash on hand"},
    "hold.cash_help": {"zh": "未投入的現金；用來算「總資金」。", "en": "Uninvested cash; used to compute total capital."},
    "hold.run_btn": {"zh": "▶ 執行健檢", "en": "▶ Run health check"},
    "hold.no_rows": {"zh": "尚未輸入任何持股；新增至少一列再按「執行健檢」。", "en": "No holdings entered yet. Add at least one row, then click 'Run health check'."},
    "hold.fetching": {"zh": "正在抓 {n} 檔即時價並計算指標…", "en": "Fetching live data for {n} symbols and computing indicators…"},
    "hold.fetch_failed": {"zh": "抓不到 {sym} 的資料，請確認代號（台股需 .TW / .TWO）", "en": "Could not fetch {sym}. Verify symbol (TW needs .TW / .TWO)."},
    "hold.metric.total_capital": {"zh": "淨資產（約）", "en": "Net worth (approx.)"},
    "hold.metric.total_capital_help": {
        "zh": "現金 ＋ 各檔自有約當額。**融資**：自有約當 ≈ **市值 − 推算欠款**（欠款依進場與融資％估算）。與下方「成本合計」不同。",
        "en": "Cash + per-line sleeves. Margin: approx **MV − estimated loan**. Distinct from **total cost** below.",
    },
    "hold.metric.market_value": {"zh": "持股市值", "en": "Holdings value"},
    "hold.metric.cost_total": {"zh": "成本合計", "en": "Total cost"},
    "hold.metric.cost_total_help": {
        "zh": "**現股**：各檔 股數×均價 加總。**融資**：只加總進場『自備款』— 進場總價 × (100−借款占進場％)／100；未填％時預設借款六成、自有四成。",
        "en": "**Cash**: sum of shares×avg cost. **Margin**: sum of **your equity at entry** = cost×(100−finance%)/100; default finance 60% if blank.",
    },
    "hold.metric.cash": {"zh": "現金", "en": "Cash"},
    "hold.metric.unrealized": {"zh": "未實現損益", "en": "Unrealized P&L"},
    "hold.metric.unrealized_pct": {"zh": "未實現損益%", "en": "Unrealized %"},
    "hold.metric.holdings_count": {"zh": "持股數", "en": "Holdings"},
    "hold.metric.health_avg": {"zh": "健檢平均分", "en": "Avg health score"},
    "hold.metric.deploy_pct": {"zh": "持倉佔淨資產％", "en": "Equity sleeves / net %"},
    "hold.metric.deploy_pct_help": {
        "zh": "（市值基礎）自有約當合計 ÷ 淨資產；融資檔＝市值−推算欠款。",
        "en": "MTM sleeves ÷ net worth; margin ≈ MV−loan.",
    },
    "hold.col.market": {"zh": "市場", "en": "Market"},
    "hold.col.cur_price": {"zh": "現價", "en": "Current"},
    "hold.col.market_value": {"zh": "市值", "en": "Mkt value"},
    "hold.col.cost": {"zh": "成本", "en": "Cost"},
    "hold.col.pnl": {"zh": "損益$", "en": "P&L $"},
    "hold.col.pnl_pct": {"zh": "損益%", "en": "P&L %"},
    "hold.col.weight": {"zh": "佔比%", "en": "Weight %"},
    "hold.col.health": {"zh": "體檢分", "en": "Health"},
    "hold.col.tier": {"zh": "綜合等級", "en": "Tier"},
    "hold.col.advice": {"zh": "建議", "en": "Advice"},
    "hold.col.outlook": {"zh": "策略展望", "en": "Outlook"},
    "hold.detail_title": {"zh": "📋 個別持股展望（按策略命中度）", "en": "📋 Per-holding outlook (by strategy hit rate)"},
    "hold.outlook_caption": {
        "zh": "對每檔持股跑你內建的 5 套策略，看哪些『現在』還是進場條件成立 → 仍看好；轉為出場條件成立 → 警示。",
        "en": "Runs all 5 built-in strategies on each holding: still meeting entry conditions → still bullish; meeting exit conditions → warning.",
    },
    "hold.outlook.bullish": {"zh": "仍看好", "en": "Bullish"},
    "hold.outlook.warn": {"zh": "出場警示", "en": "Exit warning"},
    "hold.outlook.neutral": {"zh": "中性", "en": "Neutral"},
    "hold.advice.strong_take_profit": {"zh": "📈 大幅獲利，可分批停利", "en": "📈 Big gain — consider scaling out"},
    "hold.advice.take_profit_trend_weak": {"zh": "⚠️ 獲利且趨勢轉弱，建議獲利了結", "en": "⚠️ In gain but trend weakening — consider take profit"},
    "hold.advice.hold_strong": {"zh": "✅ 趨勢健康，可續抱", "en": "✅ Trend healthy — hold"},
    "hold.advice.hold_neutral": {"zh": "🟡 中性，持續觀察", "en": "🟡 Neutral — keep watching"},
    "hold.advice.cut_loss": {"zh": "🔻 跌破關鍵均線/停損位，考慮停損", "en": "🔻 Below key MA/stop — consider cutting"},
    "hold.advice.add_on_strength": {"zh": "💪 強勢回測支撐，可考慮加碼", "en": "💪 Strong pullback to support — consider adding"},
    "hold.advice.reduce_overweight": {"zh": "⚖️ 單檔權重過高（>30%），建議分散", "en": "⚖️ Position over 30% — consider diversifying"},
    "hold.advice.margin_equity_low": {
        "zh": "⚠️ 估算融資權益比率偏低（加價／跌勢易造成追繳），宜控管曝險",
        "en": "⚠️ Estimated margin equity is low — review exposure vs margin cushion",
    },
    "hold.advice.no_data": {"zh": "—（無資料）", "en": "— (no data)"},
    "hold.verdict.title": {"zh": "組合綜合評價", "en": "Portfolio verdict"},
    "hold.verdict.composite": {"zh": "綜合分", "en": "Score"},
    "hold.verdict.tier.strong": {"zh": "穩健體質", "en": "Solid shape"},
    "hold.verdict.tier.balance": {"zh": "大致均衡", "en": "Mostly balanced"},
    "hold.verdict.tier.watch": {"zh": "須多加留意", "en": "Worth watching"},
    "hold.verdict.tier.risk": {"zh": "風險偏高", "en": "Higher risk"},
    "hold.verdict.disclaimer": {
        "zh": "評分為規則彙總示範，非投資建議；請以自身風承受度與券商規則為準。",
        "en": "Rule-based illustration only — not advice; rely on your risk tolerance & broker rules.",
    },
    "hold.verdict.bullet.avg_health_pp": {
        "zh": "加權平均個股健檢分約 **{h:.0f}/100**。",
        "en": "Weighted average health score ≈ **{h:.0f}/100**.",
    },
    "hold.verdict.bullet.deploy_high": {
        "zh": "資金運用率高（約 **{d:.0f}%**），現金緩衝少，請留意回撤與再平衡需求。",
        "en": "Capital deployment is high (**{d:.0f}%**) — thin cash cushion; watch drawdown/rebalancing.",
    },
    "hold.verdict.bullet.deploy_low": {
        "zh": "資金運用率低（約 **{d:.0f}%**），部位偏輕或可考慮依計畫進場。",
        "en": "Low deployment (**{d:.0f}%**) — lightly invested vs plan?",
    },
    "hold.verdict.bullet.concentrated": {
        "zh": "單檔權重偏集中（最重約 **{w:.1f}%**），請留意集中度風險。",
        "en": "Concentration elevated (max weight **{w:.1f}%**).",
    },
    "hold.verdict.bullet.has_margin": {"zh": "持有融資部位：請搭配上述權益比與停損／追繳風險自評。", "en": "Margin legs present — review equity proxies & stop-outs."},
    "hold.verdict.bullet.margin_tight": {"zh": "部分融資標的估算權益比率偏低（最低約 **{m:.0f}%**）。", "en": "Some margin lines show tight equity (~**{m:.0f}%** min)."},
    "hold.import_csv": {"zh": "📥 匯入 CSV", "en": "📥 Import CSV"},
    "hold.import_help": {
        "zh": "必填：symbol, shares, avg_cost；可選：note, position、margin_finance_pct、margin_interest_pct。**舊欄 margin_loan**（金額）仍可匯入，會自動換算成％。其他多餘欄略過。",
        "en": "Required: symbol, shares, avg_cost; optional: note, position, margin_finance_pct (financed share of entry cost %), margin_interest_pct (% APR). **Legacy margin_loan** is converted to %. Extra columns ignored.",
    },
    "hold.import_ok": {"zh": "已匯入 {n} 檔持股", "en": "Imported {n} holdings"},
    "hold.import_fail": {"zh": "匯入失敗：{err}", "en": "Import failed: {err}"},
    "hold.export_csv": {"zh": "📤 下載目前持股 CSV", "en": "📤 Download holdings CSV"},
    "hold.clear_btn": {"zh": "🗑 清空持股", "en": "🗑 Clear holdings"},
    "hold.clear_confirm": {"zh": "已清空所有持股", "en": "All holdings cleared"},

    # ====== Risk profile ======
    "risk.conservative": {"zh": "保守（低波動、抗回撤）", "en": "Conservative (low vol, drawdown averse)"},
    "risk.balanced": {"zh": "平衡（中等波動、長線複利）", "en": "Balanced (moderate vol, long-term compounding)"},
    "risk.aggressive": {"zh": "積極（中波段、追漲）", "en": "Aggressive (swing, chase trends)"},
    "risk.conservative.note": {
        "zh": "現金緩衝大、單檔上限低，停損嚴；適合 6–12 個月以內、不能再賠的資金。",
        "en": "Big cash buffer, low per-position cap, tight stops. Suits ≤6–12 month horizons, capital you can't afford to lose more of.",
    },
    "risk.balanced.note": {
        "zh": "現金緩衝適中、5–8 檔分散；適合 1–3 年累積、目標 8–15% CAGR 的資金。",
        "en": "Moderate cash buffer, 5–8 names. Suits 1–3 year horizons targeting 8–15% CAGR.",
    },
    "risk.aggressive.note": {
        "zh": "現金緩衝小、單檔權重高，停損寬；適合接受年內 ‑20% 回撤的資金。",
        "en": "Small cash buffer, concentrated, looser stops. Suits capital comfortable with intra-year -20% drawdown.",
    },

    # ====== Feasibility ======
    "feas.fail.target_le_current": {"zh": "目標金額不大於現有資金，立即達成。", "en": "Target ≤ current capital — already met."},
    "feas.note.tw_high": {"zh": "需求 CAGR 約 {r:.1f}% > 台股長期歷史平均 ~{mean:.0f}%，要靠選股 alpha 才有機會。", "en": "Required CAGR ~{r:.1f}% > TW long-run mean ~{mean:.0f}%; requires stock-picking alpha."},
    "feas.note.tw_mid": {"zh": "需求 CAGR 約 {r:.1f}%，介於台股歷史平均 ~{mean:.0f}% 上下，可達成但需紀律。", "en": "Required CAGR ~{r:.1f}% near TW historical mean ~{mean:.0f}% — feasible with discipline."},
    "feas.note.tw_low": {"zh": "需求 CAGR 約 {r:.1f}% 在台股歷史平均之下，配合風險控管即可。", "en": "Required CAGR ~{r:.1f}% < TW historical mean — feasible with risk discipline."},
    "feas.note.us_high": {"zh": "需求 CAGR 約 {r:.1f}% > 美股歷史平均 ~{mean:.0f}%，需要選對成長股或加槓桿，風險高。", "en": "Required CAGR ~{r:.1f}% > US historical mean ~{mean:.0f}% — needs growth picks or leverage; high risk."},
    "feas.note.us_mid": {"zh": "需求 CAGR 約 {r:.1f}% 接近美股歷史平均，主流大盤即可達成。", "en": "Required CAGR ~{r:.1f}% near US historical mean — feasible with broad-market exposure."},
    "feas.note.us_low": {"zh": "需求 CAGR 約 {r:.1f}% 低於美股歷史平均，較容易達成。", "en": "Required CAGR ~{r:.1f}% below US historical mean — easy to achieve."},
    "feas.verdict.too_aggressive": {"zh": "目標過於進取", "en": "Goal too aggressive"},
    "feas.verdict.feasible_disciplined": {"zh": "可行但需紀律", "en": "Feasible with discipline"},
    "feas.verdict.feasible_easy": {"zh": "目標相對容易達成", "en": "Goal relatively easy"},
    "feas.verdict.met": {"zh": "目標已達成", "en": "Goal already met"},
}
