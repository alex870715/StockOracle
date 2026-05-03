"""
策略模組（v1）：

每個策略需提供：
  - key / label / description
  - evaluate(snap, enriched) -> dict：
        判定當日「現在能不能進」「現在該不該出」、給出 0–10 的策略分數、
        對應推薦等級、命中與未命中規則、進出場規則文字（給 UI / 推薦理由用）。
  - historical_signals(enriched) -> dict[str, list[pd.Timestamp]]：
        針對整段歷史標記 entry / exit 點，給 charts.py 畫進場（黃三角）／出場（紅三角）。

設計準則：
  * 規則都用 add_indicators() 產出的欄位，不重新算 → 與 daily_pick 一致。
  * 每個策略的「分數」= 命中規則數 / 規則總數 × 10；<3 視為「中性」、≥3 「偏多觀察」、
    ≥5 「買進」、≥7 「強烈買進」、=0 「避開」。
  * historical_signals 用向量化條件，避免 1300+ 標的時逐列計算。

新增策略只要把類別塞進 STRATEGY_REGISTRY 即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from i18n import get_lang


def _pick_lang(d_or_s, lang: str | None = None):
    """
    取雙語欄位：
      - 字串 → 直接回傳
      - dict {"zh": ..., "en": ...} → 依當前語言回傳，缺則 fallback zh
      - list → 對每個元素遞迴
    """
    if d_or_s is None:
        return None
    cur = lang or get_lang()
    if isinstance(d_or_s, str):
        return d_or_s
    if isinstance(d_or_s, dict):
        return d_or_s.get(cur) or d_or_s.get("zh") or next(iter(d_or_s.values()), "")
    if isinstance(d_or_s, list):
        return [_pick_lang(x, cur) for x in d_or_s]
    return d_or_s


_TIMEFRAME_I18N = {
    "短期": {"zh": "短期", "en": "Short-term"},
    "中期": {"zh": "中期", "en": "Mid-term"},
    "中長期": {"zh": "中長期", "en": "Long-term"},
}


_RISK_LABEL_I18N = {
    "低": {"zh": "低", "en": "Low"},
    "中": {"zh": "中", "en": "Mid"},
    "高": {"zh": "高", "en": "High"},
}


_TIER_I18N = {
    "強烈買進": {"zh": "強烈買進", "en": "Strong Buy"},
    "買進": {"zh": "買進", "en": "Buy"},
    "偏多觀察": {"zh": "偏多觀察", "en": "Watch (bullish)"},
    "中性": {"zh": "中性", "en": "Neutral"},
    "中立": {"zh": "中立", "en": "Neutral"},
    "減碼": {"zh": "減碼", "en": "Reduce"},
    "避開": {"zh": "避開", "en": "Avoid"},
}


# ---- 共用工具 ----


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


def _tier_from_score(s: float) -> str:
    """資料層仍回中文 tier key；UI 端用 i18n.tier_label() 轉成當前語言。"""
    if s >= 7.0:
        return "強烈買進"
    if s >= 5.0:
        return "買進"
    if s >= 3.0:
        return "偏多觀察"
    if s >= 1.0:
        return "中性"
    return "避開"


def _tier_localized(s: float) -> str:
    """直接給 UI 顯示用的當前語言 tier。"""
    return _pick_lang(_TIER_I18N[_tier_from_score(s)])


def _series(d: pd.DataFrame, col: str) -> pd.Series:
    if col not in d.columns:
        return pd.Series(np.nan, index=d.index, dtype="float64")
    s = pd.to_numeric(d[col], errors="coerce")
    return s


def _safe_bool(s: pd.Series) -> pd.Series:
    """把可能含 NaN 的條件轉成乾淨 bool（避免 pandas 的 downcast FutureWarning）。"""
    if s is None:
        return pd.Series(dtype=bool)
    return s.where(s.notna(), False).astype(bool)


def _bool_to_dates(mask: pd.Series) -> list[pd.Timestamp]:
    if mask is None:
        return []
    m = _safe_bool(mask)
    return list(m.index[m])


def _prev_true(s: pd.Series) -> pd.Series:
    """前一日是否為 True；用 shift(fill_value=False) 避免引入 NaN。"""
    return _safe_bool(s).shift(1, fill_value=False)


def _rising_edge(s: pd.Series) -> pd.Series:
    """
    只取『從 False 變 True』那一天的 mask（rising edge）。
    避免「條件成立後連續 N 天都被點滿」造成圖上一堆出場箭頭。
    """
    m = _safe_bool(s)
    return m & (~_prev_true(m))


def _pair_entries_exits(
    entry_dates: list[pd.Timestamp],
    exit_dates: list[pd.Timestamp],
    *,
    max_hold_days: int | None = None,
) -> tuple[list[pd.Timestamp], list[pd.Timestamp]]:
    """
    把進出場日做 1-to-1 配對：每一個 entry 只配第一個比它晚的 exit；
    沒配到 exit 的 entry 仍保留（畫面會看到沒被收掉的進場），
    沒配到 entry 的 exit 一律丟掉（沒持倉的出場毫無意義）。

    若給 max_hold_days，超過天數還沒出場 → 自動補一個「時間到」出場。
    """
    entries = sorted(set(pd.to_datetime(d) for d in (entry_dates or [])))
    exits_pool = sorted(set(pd.to_datetime(d) for d in (exit_dates or [])))

    paired_entries: list[pd.Timestamp] = []
    paired_exits: list[pd.Timestamp] = []
    j = 0  # exits 指標
    for e in entries:
        # 找第一個 > entry 的 exit
        while j < len(exits_pool) and exits_pool[j] <= e:
            j += 1
        chosen_exit = None
        if j < len(exits_pool):
            chosen_exit = exits_pool[j]
            # 限制最大持有天數：超過就用「entry + N 個交易日」當預設出場
            if max_hold_days is not None:
                deadline = e + pd.tseries.offsets.BDay(int(max_hold_days))
                if chosen_exit > deadline:
                    chosen_exit = deadline
                    # 注意：j 不前進，因為這個 chosen_exit 是模擬的，不消耗真實 exit
                else:
                    j += 1
            else:
                j += 1
        elif max_hold_days is not None:
            chosen_exit = e + pd.tseries.offsets.BDay(int(max_hold_days))
        paired_entries.append(e)
        if chosen_exit is not None:
            paired_exits.append(chosen_exit)
    return paired_entries, paired_exits


def _clean_signals(
    entry_mask: pd.Series,
    exit_mask: pd.Series,
    *,
    max_hold_days: int | None = None,
) -> dict[str, list[pd.Timestamp]]:
    """
    把任意 raw 條件 mask 過一次「rising edge + 1-to-1 配對」處理。
    所有策略的 historical_signals 統一走這個 pipeline，避免出場箭頭氾濫。
    """
    entry_edge = _rising_edge(entry_mask)
    exit_edge = _rising_edge(exit_mask)
    entries = _bool_to_dates(entry_edge)
    exits_raw = _bool_to_dates(exit_edge)
    e_pair, x_pair = _pair_entries_exits(entries, exits_raw, max_hold_days=max_hold_days)
    return {"entries": e_pair, "exits": x_pair}


def _evaluate_rules(rules: list[tuple[str, bool | None]]) -> tuple[float, list[str], list[str]]:
    """
    rules: [(描述, 命中 True / 未命中 False / 無法判定 None), ...]
    回傳 (score 0~10, 命中描述, 未命中描述)
    """
    valid = [(d, ok) for d, ok in rules if ok is not None]
    hits = [d for d, ok in valid if ok]
    misses = [d for d, ok in valid if not ok]
    if not valid:
        return 0.0, [], []
    score = (len(hits) / len(valid)) * 10.0
    return score, hits, misses


# ---- Protocol ----


class Strategy(Protocol):
    key: str
    label: str
    description: str
    timeframe: str  # "短期" | "中期" | "中長期"
    risk_label: str  # "低" | "中" | "高"

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]: ...
    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]: ...


# ---- 策略基底 ----


@dataclass
class _Base:
    """
    每個策略內部把 label / description / entry_rules / exit_rules 都存成 dict {zh, en}（或 list of dict）。
    對外仍然提供原本的 .label / .description / .entry_rules_text / .exit_rules_text，
    這四個 attribute 動態依當前語言回傳，向後相容所有既有呼叫者。
    """
    key: str = ""
    _label_i18n: dict[str, str] = field(default_factory=dict)
    _description_i18n: dict[str, str] = field(default_factory=dict)
    _entry_rules_i18n: list[dict[str, str]] = field(default_factory=list)
    _exit_rules_i18n: list[dict[str, str]] = field(default_factory=list)
    _timeframe_zh: str = "中期"
    _risk_label_zh: str = "中"

    @property
    def label(self) -> str:
        return _pick_lang(self._label_i18n) or ""

    @property
    def description(self) -> str:
        return _pick_lang(self._description_i18n) or ""

    @property
    def entry_rules_text(self) -> list[str]:
        return [_pick_lang(r) for r in self._entry_rules_i18n]

    @property
    def exit_rules_text(self) -> list[str]:
        return [_pick_lang(r) for r in self._exit_rules_i18n]

    @property
    def timeframe(self) -> str:
        return _pick_lang(_TIMEFRAME_I18N.get(self._timeframe_zh, self._timeframe_zh))

    @property
    def risk_label(self) -> str:
        return _pick_lang(_RISK_LABEL_I18N.get(self._risk_label_zh, self._risk_label_zh))


# ---- 1) 短期激進做多（量增 + ATR 突破） ----


class ShortAggressive(_Base):
    def __init__(self) -> None:
        super().__init__(
            key="short_aggressive",
            _label_i18n={
                "zh": "短期激進做多（量爆+ATR 突破）",
                "en": "Short Aggressive (volume burst + ATR breakout)",
            },
            _description_i18n={
                "zh": "抓單日量價齊揚的拍打：日漲幅 ≥ 0.8×ATR、量比 ≥ 1.5、收於當日上 60%。出場以 5 日均線 / 1.5×ATR 停損為主，最多持有 7 天。",
                "en": "Catch single-day volume+price breakouts: gain ≥ 0.8×ATR, vol ratio ≥ 1.5, close in upper 60%. Exit via 5-day MA / 1.5×ATR stop, max 7-day hold.",
            },
            _timeframe_zh="短期",
            _risk_label_zh="高",
            _entry_rules_i18n=[
                {"zh": "日漲幅 ≥ 0.8 × ATR(14)（用 ATR 取代固定 %、自動適配波動率）",
                 "en": "Daily gain ≥ 0.8 × ATR(14) (ATR-based, adapts to volatility)"},
                {"zh": "成交量比 ≥ 1.5", "en": "Volume ratio ≥ 1.5"},
                {"zh": "收盤位於當日區間上 60%（避免上影線）",
                 "en": "Close in upper 60% of daily range (avoid upper wick)"},
                {"zh": "進階：MA20 在 MA50 之上時加分（順勢進）",
                 "en": "Bonus: MA20 > MA50 (trade with trend)"},
            ],
            _exit_rules_i18n=[
                {"zh": "收盤跌破 5 日均線", "en": "Close breaks below 5-day MA"},
                {"zh": "單日跌幅 ≥ 1.5 × ATR（明顯分布）", "en": "Daily drop ≥ 1.5 × ATR (clear distribution)"},
                {"zh": "持有滿 7 個交易日無突破前高 → 視為失敗，出場",
                 "en": "Hold 7 trading days without making new high → considered failed, exit"},
                {"zh": "硬停損：跌破進場日低點", "en": "Hard stop: break entry-day low"},
            ],
        )

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]:
        ret = _f(snap.get("ret_1d"))
        vr = _f(snap.get("volume_ratio"))
        atrp = _f(snap.get("atr14_pct"))
        loc = _f(snap.get("day_close_loc"))
        ma20 = _f(snap.get("ma20"))
        ma50 = _f(snap.get("ma50"))

        atr_breakout = (
            None if (ret is None or atrp is None or atrp <= 0) else (ret * 100.0 / atrp) >= 0.8
        )
        vol_ok = None if vr is None else vr >= 1.5
        loc_ok = None if loc is None else loc >= 0.6
        trend_ok = None if (ma20 is None or ma50 is None) else ma20 > ma50

        rules = [
            (_pick_lang({"zh": "漲幅 ≥ 0.8×ATR", "en": "Gain ≥ 0.8×ATR"}), atr_breakout),
            (_pick_lang({"zh": "量比 ≥ 1.5", "en": "Vol ratio ≥ 1.5"}), vol_ok),
            (_pick_lang({"zh": "收於當日上 60%", "en": "Close in upper 60%"}), loc_ok),
            (_pick_lang({"zh": "MA20 > MA50（順勢加分）", "en": "MA20 > MA50 (trend bonus)"}), trend_ok),
        ]
        score, hits, misses = _evaluate_rules(rules)

        close = _f(snap.get("close"))
        ma5 = None
        if "close" in enriched.columns:
            cls = pd.to_numeric(enriched["close"], errors="coerce")
            if cls.dropna().shape[0] >= 5:
                ma5 = float(cls.rolling(5).mean().iloc[-1])
        atr14 = _f(snap.get("atr14"))
        exit_today = False
        exit_reasons: list[str] = []
        if close is not None and ma5 is not None and close < ma5:
            exit_today = True
            exit_reasons.append(_pick_lang({
                "zh": f"收盤 {close:.2f} 已跌破 5 日均 {ma5:.2f}",
                "en": f"Close {close:.2f} broke below 5-day MA {ma5:.2f}",
            }))
        if ret is not None and atr14 is not None and close is not None and ret * close <= -1.5 * atr14:
            exit_today = True
            exit_reasons.append(_pick_lang({"zh": "單日跌幅已達 1.5×ATR", "en": "Daily drop reached 1.5×ATR"}))

        entry_today = bool(atr_breakout and vol_ok and loc_ok)

        return {
            "entry_today": entry_today,
            "exit_today": exit_today,
            "score": score,
            "recommendation": _tier_from_score(score),
            "rule_hits": hits,
            "rule_misses": misses,
            "exit_today_reasons": exit_reasons,
            "entry_rules_text": self.entry_rules_text,
            "exit_rules_text": self.exit_rules_text,
        }

    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
        d = enriched
        if not {"ret_1d", "volume_ratio", "atr14_pct", "day_close_loc", "close", "open"}.issubset(d.columns):
            return {"entries": [], "exits": []}
        ret = _series(d, "ret_1d")
        vr = _series(d, "volume_ratio")
        atrp = _series(d, "atr14_pct")
        loc = _series(d, "day_close_loc")
        cls = _series(d, "close")
        atr14 = _series(d, "atr14")

        entry = (ret * 100.0 / atrp.replace(0, np.nan) >= 0.8) & (vr >= 1.5) & (loc >= 0.6)

        ma5 = cls.rolling(5).mean()
        cross_down_5 = (cls < ma5) & (cls.shift(1) >= ma5.shift(1))
        big_dump = (ret * cls) <= (-1.5 * atr14)
        exit_ = cross_down_5 | big_dump

        # 短線策略：最多持有 7 個交易日，超時自動補出場
        return _clean_signals(entry, exit_, max_hold_days=7)


# ---- 2) 動能突破（20 日新高 + 量能） ----


class MomentumBreakout(_Base):
    def __init__(self) -> None:
        super().__init__(
            key="momentum_breakout",
            _label_i18n={
                "zh": "動能突破（20 日新高+量能）",
                "en": "Momentum Breakout (20-day high + volume)",
            },
            _description_i18n={
                "zh": "抓站上「前 20 日最高」的真突破，配合放量與 RSI 在多頭區間。出場用 10 日新低或 RSI 跌破 50。",
                "en": "Catch true breakouts above the 20-day high with volume and RSI in bullish zone. Exit on 10-day low break or RSI drop below 50.",
            },
            _timeframe_zh="中期",
            _risk_label_zh="中",
            _entry_rules_i18n=[
                {"zh": "收盤 ≥ 前 20 日最高（不含當日）",
                 "en": "Close ≥ prior 20-day high (excl. today)"},
                {"zh": "成交量比 ≥ 1.3", "en": "Volume ratio ≥ 1.3"},
                {"zh": "RSI(14) 落在 50–75 區間", "en": "RSI(14) between 50–75"},
                {"zh": "MA50 > MA200（中期多頭結構）",
                 "en": "MA50 > MA200 (mid-term uptrend structure)"},
            ],
            _exit_rules_i18n=[
                {"zh": "收盤跌破 10 日最低", "en": "Close breaks below 10-day low"},
                {"zh": "RSI(14) 跌破 50", "en": "RSI(14) drops below 50"},
                {"zh": "MACD 柱由正轉負", "en": "MACD histogram flips positive → negative"},
            ],
        )

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]:
        d = enriched
        cls = _series(d, "close")
        prev_20_high = cls.shift(1).rolling(20).max()
        breakout = bool(cls.iloc[-1] >= prev_20_high.iloc[-1]) if cls.dropna().shape[0] >= 21 else None

        vr = _f(snap.get("volume_ratio"))
        vol_ok = None if vr is None else vr >= 1.3
        rsi = _f(snap.get("rsi14"))
        rsi_ok = None if rsi is None else 50.0 <= rsi <= 75.0
        ma50 = _f(snap.get("ma50"))
        ma200 = _f(snap.get("ma200"))
        trend_ok = None if (ma50 is None or ma200 is None) else ma50 > ma200

        rules = [
            (_pick_lang({"zh": "收盤 ≥ 前 20 日最高", "en": "Close ≥ prior 20-day high"}), breakout),
            (_pick_lang({"zh": "量比 ≥ 1.3", "en": "Vol ratio ≥ 1.3"}), vol_ok),
            (_pick_lang({"zh": "RSI(14) 50–75", "en": "RSI(14) 50–75"}), rsi_ok),
            (_pick_lang({"zh": "MA50 > MA200", "en": "MA50 > MA200"}), trend_ok),
        ]
        score, hits, misses = _evaluate_rules(rules)
        entry_today = bool(breakout and vol_ok and rsi_ok)

        prev_10_low = cls.shift(1).rolling(10).min()
        last_close = _f(snap.get("close"))
        exit_today = False
        reasons: list[str] = []
        if last_close is not None and not pd.isna(prev_10_low.iloc[-1]) and last_close < float(prev_10_low.iloc[-1]):
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"收盤 {last_close:.2f} 跌破 10 日最低 {float(prev_10_low.iloc[-1]):.2f}",
                "en": f"Close {last_close:.2f} broke below 10-day low {float(prev_10_low.iloc[-1]):.2f}",
            }))
        if rsi is not None and rsi < 50.0:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"RSI {rsi:.1f} 已跌破 50",
                "en": f"RSI {rsi:.1f} dropped below 50",
            }))
        mh = _series(d, "macd_hist")
        if mh.dropna().shape[0] >= 2 and mh.iloc[-1] < 0 and mh.iloc[-2] >= 0:
            exit_today = True
            reasons.append(_pick_lang({"zh": "MACD 柱由正轉負", "en": "MACD histogram flipped positive → negative"}))

        return {
            "entry_today": entry_today,
            "exit_today": exit_today,
            "score": score,
            "recommendation": _tier_from_score(score),
            "rule_hits": hits,
            "rule_misses": misses,
            "exit_today_reasons": reasons,
            "entry_rules_text": self.entry_rules_text,
            "exit_rules_text": self.exit_rules_text,
        }

    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
        d = enriched
        if not {"close", "volume_ratio", "rsi14"}.issubset(d.columns):
            return {"entries": [], "exits": []}
        cls = _series(d, "close")
        vr = _series(d, "volume_ratio")
        rsi = _series(d, "rsi14")
        ma50 = _series(d, "ma50")
        ma200 = _series(d, "ma200")
        prev20h = cls.shift(1).rolling(20).max()
        prev10l = cls.shift(1).rolling(10).min()

        entry = (cls >= prev20h) & (vr >= 1.3) & (rsi.between(50, 75)) & (ma50 > ma200)

        mh = _series(d, "macd_hist")
        macd_flip_down = (mh < 0) & (mh.shift(1) >= 0)
        exit_ = (cls < prev10l) | (rsi < 50) | macd_flip_down

        # 中期策略：最多持有 60 個交易日（趨勢沒走完不該硬下車）
        return _clean_signals(entry, exit_, max_hold_days=60)


# ---- 3) 中長期趨勢（月線之上） ----


class LongTrend(_Base):
    def __init__(self) -> None:
        super().__init__(
            key="long_trend",
            _label_i18n={
                "zh": "中長期趨勢（站上月線+RS 為正）",
                "en": "Long Trend (above MA200 + positive RS)",
            },
            _description_i18n={
                "zh": "走慢一點：close 站上 MA200、MA50 > MA200、相對強弱 60D 為正、RSI 不過熱。出場用跌破 MA50 或 RS 轉負。",
                "en": "Slower setup: close > MA200, MA50 > MA200, 60D relative strength positive, RSI not overheated. Exit on close < MA50 or RS turning negative.",
            },
            _timeframe_zh="中長期",
            _risk_label_zh="低",
            _entry_rules_i18n=[
                {"zh": "收盤 > MA200", "en": "Close > MA200"},
                {"zh": "MA50 > MA200（黃金交叉之後或維持）",
                 "en": "MA50 > MA200 (after or holding golden cross)"},
                {"zh": "RSI(14) 在 45–70（不過冷也不過熱）",
                 "en": "RSI(14) between 45–70 (neither cold nor overheated)"},
                {"zh": "60 日相對強弱 vs 大盤 ≥ 0%（沒輸給大盤）",
                 "en": "60-day relative strength vs index ≥ 0% (not lagging)"},
            ],
            _exit_rules_i18n=[
                {"zh": "收盤跌破 MA50", "en": "Close breaks below MA50"},
                {"zh": "MA50 與 MA200 死亡交叉", "en": "MA50 / MA200 death cross"},
                {"zh": "60 日相對強弱跌至 ‑5%（明顯落後大盤）",
                 "en": "60-day RS drops to ‑5% (clearly lagging index)"},
            ],
        )

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]:
        close = _f(snap.get("close"))
        ma50 = _f(snap.get("ma50"))
        ma200 = _f(snap.get("ma200"))
        rsi = _f(snap.get("rsi14"))
        rs60 = _f(snap.get("rs_60d_pct"))

        above_200 = None if (close is None or ma200 is None) else close > ma200
        ma_stack = None if (ma50 is None or ma200 is None) else ma50 > ma200
        rsi_ok = None if rsi is None else 45.0 <= rsi <= 70.0
        rs_ok = None if rs60 is None else rs60 >= 0.0

        rules = [
            (_pick_lang({"zh": "收盤 > MA200", "en": "Close > MA200"}), above_200),
            (_pick_lang({"zh": "MA50 > MA200", "en": "MA50 > MA200"}), ma_stack),
            (_pick_lang({"zh": "RSI(14) 45–70", "en": "RSI(14) 45–70"}), rsi_ok),
            (_pick_lang({"zh": "60D RS ≥ 0%", "en": "60D RS ≥ 0%"}), rs_ok),
        ]
        score, hits, misses = _evaluate_rules(rules)
        entry_today = bool(above_200 and ma_stack and rsi_ok and rs_ok)

        exit_today = False
        reasons: list[str] = []
        if close is not None and ma50 is not None and close < ma50:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"收盤 {close:.2f} 已跌破 MA50 {ma50:.2f}",
                "en": f"Close {close:.2f} broke below MA50 {ma50:.2f}",
            }))
        if ma50 is not None and ma200 is not None and ma50 < ma200:
            exit_today = True
            reasons.append(_pick_lang({"zh": "MA50 < MA200（死亡交叉）", "en": "MA50 < MA200 (death cross)"}))
        if rs60 is not None and rs60 < -5.0:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"60D RS {rs60:+.2f}% 落後大盤過多",
                "en": f"60D RS {rs60:+.2f}% lagging index too far",
            }))

        return {
            "entry_today": entry_today,
            "exit_today": exit_today,
            "score": score,
            "recommendation": _tier_from_score(score),
            "rule_hits": hits,
            "rule_misses": misses,
            "exit_today_reasons": reasons,
            "entry_rules_text": self.entry_rules_text,
            "exit_rules_text": self.exit_rules_text,
        }

    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
        d = enriched
        if not {"close", "ma50", "ma200", "rsi14"}.issubset(d.columns):
            return {"entries": [], "exits": []}
        cls = _series(d, "close")
        ma50 = _series(d, "ma50")
        ma200 = _series(d, "ma200")
        rsi = _series(d, "rsi14")
        rs60 = _series(d, "rs_60d_pct") if "rs_60d_pct" in d.columns else pd.Series(0.0, index=d.index)

        entry = (cls > ma200) & (ma50 > ma200) & (rsi.between(45, 70)) & (rs60 >= 0.0)

        # 注意：底下三個都是「狀態」，rising-edge 要在 _clean_signals 裡統一做
        exit_ = (cls < ma50) | (ma50 < ma200) | (rs60 < -5.0)

        # 中長期策略：不限最大持有天數（讓趨勢自己走完）
        return _clean_signals(entry, exit_, max_hold_days=None)


# ---- 4) 反轉接刀（RSI 超賣 + 觸下軌） ----


class MeanReversion(_Base):
    def __init__(self) -> None:
        super().__init__(
            key="mean_reversion",
            _label_i18n={
                "zh": "反轉接刀（RSI 超賣+布林下軌）",
                "en": "Mean Reversion (RSI oversold + Bollinger lower)",
            },
            _description_i18n={
                "zh": "高風險高報酬：RSI(14) < 30、價格觸或穿布林下軌、當日紅 K 收復前低。出場用 RSI 回到 60 或觸 MA20。",
                "en": "High risk / high reward: RSI(14) < 30, price touches/breaks Bollinger lower, daily red→green candle. Exit on RSI back ≥ 60 or close touching MA20.",
            },
            _timeframe_zh="短期",
            _risk_label_zh="高",
            _entry_rules_i18n=[
                {"zh": "RSI(14) < 30（深度超賣）", "en": "RSI(14) < 30 (deeply oversold)"},
                {"zh": "布林位置 ≤ 0.10（碰到下軌）", "en": "Bollinger position ≤ 0.10 (touching lower band)"},
                {"zh": "當日 K 為紅 K（close > open，止跌跡象）",
                 "en": "Bullish candle (close > open, sign of stabilization)"},
                {"zh": "距 52 週低點 ≤ 10%（真的在底部區）",
                 "en": "Within 10% of 52-week low (truly bottom zone)"},
            ],
            _exit_rules_i18n=[
                {"zh": "RSI(14) 站上 60", "en": "RSI(14) back above 60"},
                {"zh": "收盤觸及 MA20", "en": "Close touches MA20"},
                {"zh": "硬停損：較進場日收盤再跌 8%", "en": "Hard stop: 8% below entry-day close"},
            ],
        )

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]:
        rsi = _f(snap.get("rsi14"))
        bb_pct = None
        if "bb_pct" in enriched.columns:
            bb_pct = _f(enriched["bb_pct"].iloc[-1])
        d = enriched
        op_last = _f(d["open"].iloc[-1]) if "open" in d.columns else None
        cls_last = _f(snap.get("close"))
        red = None if (op_last is None or cls_last is None) else cls_last > op_last
        dl = _f(snap.get("dist_to_52w_low_pct"))

        rsi_ok = None if rsi is None else rsi < 30.0
        bb_ok = None if bb_pct is None else bb_pct <= 0.10
        dl_ok = None if dl is None else dl <= 10.0

        rules = [
            (_pick_lang({"zh": "RSI(14) < 30", "en": "RSI(14) < 30"}), rsi_ok),
            (_pick_lang({"zh": "布林位置 ≤ 0.10", "en": "Bollinger pos ≤ 0.10"}), bb_ok),
            (_pick_lang({"zh": "當日紅 K", "en": "Bullish daily candle"}), red),
            (_pick_lang({"zh": "距 52W 低 ≤ 10%", "en": "Within 10% of 52W low"}), dl_ok),
        ]
        score, hits, misses = _evaluate_rules(rules)
        entry_today = bool(rsi_ok and bb_ok and red)

        exit_today = False
        reasons: list[str] = []
        if rsi is not None and rsi >= 60.0:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"RSI {rsi:.1f} 已站上 60",
                "en": f"RSI {rsi:.1f} back above 60",
            }))
        ma20 = _f(snap.get("ma20"))
        if cls_last is not None and ma20 is not None and cls_last >= ma20:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"已觸及 MA20 {ma20:.2f}",
                "en": f"Reached MA20 {ma20:.2f}",
            }))

        return {
            "entry_today": entry_today,
            "exit_today": exit_today,
            "score": score,
            "recommendation": _tier_from_score(score),
            "rule_hits": hits,
            "rule_misses": misses,
            "exit_today_reasons": reasons,
            "entry_rules_text": self.entry_rules_text,
            "exit_rules_text": self.exit_rules_text,
        }

    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
        d = enriched
        if not {"rsi14", "bb_pct", "open", "close"}.issubset(d.columns):
            return {"entries": [], "exits": []}
        rsi = _series(d, "rsi14")
        bb = _series(d, "bb_pct")
        op = _series(d, "open")
        cls = _series(d, "close")
        ma20 = _series(d, "ma20")

        entry = (rsi < 30.0) & (bb <= 0.10) & (cls > op)
        exit_ = (cls >= ma20) | (rsi >= 60.0)

        # 反轉接刀：超過 20 個交易日還沒回來就硬下車
        return _clean_signals(entry, exit_, max_hold_days=20)


# ---- 5) 防守型 ETF / 殖利率 ----


class DefensiveETF(_Base):
    def __init__(self) -> None:
        super().__init__(
            key="defensive_etf",
            _label_i18n={
                "zh": "防守型（低波動 + 月線之上）",
                "en": "Defensive (low vol + above MA200)",
            },
            _description_i18n={
                "zh": "挑波動低、回撤小、又站上 MA200 的「睡得著」標的。適合主流大盤 ETF、配息型 ETF、防禦型大型股。",
                "en": "Pick low-vol, low-drawdown names above MA200 — sleep-well-at-night stuff. Fits broad-market ETFs, dividend ETFs, defensive large caps.",
            },
            _timeframe_zh="中長期",
            _risk_label_zh="低",
            _entry_rules_i18n=[
                {"zh": "60 日年化波動率 < 25%", "en": "60-day annualized vol < 25%"},
                {"zh": "近 60 日最大回撤 > ‑10%", "en": "60-day max drawdown > ‑10%"},
                {"zh": "收盤 > MA200", "en": "Close > MA200"},
                {"zh": "ATR(14) / 收盤 < 2.5%（日內噪音不大）",
                 "en": "ATR(14) / close < 2.5% (low intraday noise)"},
            ],
            _exit_rules_i18n=[
                {"zh": "60 日年化波動率 > 35%", "en": "60-day annualized vol > 35%"},
                {"zh": "近 60 日最大回撤 < ‑15%", "en": "60-day max drawdown < ‑15%"},
                {"zh": "收盤跌破 MA200", "en": "Close breaks below MA200"},
            ],
        )

    def evaluate(self, snap: dict, enriched: pd.DataFrame) -> dict[str, Any]:
        vol60 = _f(snap.get("vol_60d_ann"))
        mdd = _f(snap.get("mdd_60d"))
        close = _f(snap.get("close"))
        ma200 = _f(snap.get("ma200"))
        atrp = _f(snap.get("atr14_pct"))

        v_ok = None if vol60 is None else vol60 < 0.25
        d_ok = None if mdd is None else mdd > -0.10
        m_ok = None if (close is None or ma200 is None) else close > ma200
        a_ok = None if atrp is None else atrp < 2.5

        rules = [
            (_pick_lang({"zh": "年化波動率 < 25%", "en": "Annualized vol < 25%"}), v_ok),
            (_pick_lang({"zh": "60 日 MDD > ‑10%", "en": "60-day MDD > ‑10%"}), d_ok),
            (_pick_lang({"zh": "收盤 > MA200", "en": "Close > MA200"}), m_ok),
            (_pick_lang({"zh": "ATR%/收盤 < 2.5%", "en": "ATR%/close < 2.5%"}), a_ok),
        ]
        score, hits, misses = _evaluate_rules(rules)
        entry_today = bool(v_ok and d_ok and m_ok and a_ok)

        exit_today = False
        reasons: list[str] = []
        if vol60 is not None and vol60 > 0.35:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"年化波動率 {vol60 * 100:.1f}% > 35%",
                "en": f"Annualized vol {vol60 * 100:.1f}% > 35%",
            }))
        if mdd is not None and mdd < -0.15:
            exit_today = True
            reasons.append(_pick_lang({
                "zh": f"60 日 MDD {mdd * 100:.1f}% < ‑15%",
                "en": f"60-day MDD {mdd * 100:.1f}% < ‑15%",
            }))
        if close is not None and ma200 is not None and close < ma200:
            exit_today = True
            reasons.append(_pick_lang({"zh": "收盤跌破 MA200", "en": "Close broke below MA200"}))

        return {
            "entry_today": entry_today,
            "exit_today": exit_today,
            "score": score,
            "recommendation": _tier_from_score(score),
            "rule_hits": hits,
            "rule_misses": misses,
            "exit_today_reasons": reasons,
            "entry_rules_text": self.entry_rules_text,
            "exit_rules_text": self.exit_rules_text,
        }

    def historical_signals(self, enriched: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
        d = enriched
        if not {"close", "ma200"}.issubset(d.columns):
            return {"entries": [], "exits": []}
        vol60 = _series(d, "vol_60d_ann")
        mdd = _series(d, "mdd_60d")
        cls = _series(d, "close")
        ma200 = _series(d, "ma200")
        atrp = _series(d, "atr14_pct")

        entry = (vol60 < 0.25) & (mdd > -0.10) & (cls > ma200) & (atrp < 2.5)
        exit_ = (cls < ma200) | (vol60 > 0.35) | (mdd < -0.15)

        return _clean_signals(entry, exit_, max_hold_days=None)


# ---- 註冊表 ----


_REGISTRY: dict[str, Strategy] = {
    s.key: s
    for s in [
        ShortAggressive(),
        MomentumBreakout(),
        LongTrend(),
        MeanReversion(),
        DefensiveETF(),
    ]
}


def list_strategies() -> list[Strategy]:
    return list(_REGISTRY.values())


def get_strategy(key: str) -> Strategy:
    if key not in _REGISTRY:
        raise KeyError(f"未知策略：{key}；可用：{list(_REGISTRY.keys())}")
    return _REGISTRY[key]


def strategy_choices() -> list[tuple[str, str]]:
    """給 UI 用的 (key, label) 列表。會依當前語言動態生成。"""
    if get_lang() == "en":
        return [(s.key, f"{s.label}  ·  {s.timeframe} / Risk: {s.risk_label}") for s in list_strategies()]
    return [(s.key, f"{s.label}　·　{s.timeframe}／風險{s.risk_label}") for s in list_strategies()]


DEFAULT_STRATEGY_KEY = "long_trend"
