"""
推薦解讀：把 snapshot 翻成「投資人會看的論述」，含多週期視角、52 週位置、
ATR 停損／部位試算、基本面、明確失效條件。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from analysis import add_indicators, latest_swing_low
from i18n import get_lang, tier_label


def T(zh: str, en: str) -> str:
    """In-line bilingual helper：根據當前語言回傳。"""
    return en if get_lang() == "en" else zh


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


def weekly_view(daily_df: pd.DataFrame) -> dict[str, Any]:
    """把日 K 重採樣成週 K，回傳趨勢階梯小結。"""
    if daily_df is None or daily_df.empty:
        return {"available": False}
    cols = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    df = daily_df[list(cols.keys())].resample("W-FRI").agg(cols).dropna()
    if len(df) < 30:
        return {"available": False}
    enr = add_indicators(df, include_full=True)
    last = enr.iloc[-1]
    return {
        "available": True,
        "close": _f(last.get("close")),
        "ma20": _f(last.get("ma20")),
        "ma50": _f(last.get("ma50")),
        "above_ma20": _f(last.get("close")) is not None
        and _f(last.get("ma20")) is not None
        and _f(last.get("close")) > _f(last.get("ma20")),
        "above_ma50": _f(last.get("close")) is not None
        and _f(last.get("ma50")) is not None
        and _f(last.get("close")) > _f(last.get("ma50")),
        "rsi14": _f(last.get("rsi14")),
        "macd_hist": _f(last.get("macd_hist")),
    }


def trade_plan(snapshot: dict, daily_df: pd.DataFrame | None, *, account_size: float, risk_pct: float) -> dict[str, Any]:
    """
    依 ATR 與最近 swing low 給出建議停損／停利／部位（教學示範用，非投資建議）。
    risk_pct: 每筆風險佔總資金的百分比（例如 1.0 = 1%）。
    """
    close = _f(snapshot.get("close"))
    atr14 = _f(snapshot.get("atr14"))
    plan: dict[str, Any] = {"available": False}
    if close is None or close <= 0 or atr14 is None or atr14 <= 0:
        return plan

    swing_low = None
    if daily_df is not None and "low" in daily_df.columns:
        swing_low = latest_swing_low(daily_df["low"], lookback=20)

    stop_atr = close - 2.0 * atr14
    stop = max(s for s in (stop_atr, swing_low) if s is not None) if swing_low is not None else stop_atr
    stop = max(stop, 0.0)

    risk_per_share = max(close - stop, 1e-9)
    take_profit_3r = close + 3.0 * risk_per_share
    risk_dollar = account_size * (risk_pct / 100.0)
    raw_shares = risk_dollar / risk_per_share

    market = snapshot.get("market", "")
    if market == "台股":
        unit = 1000
        suggested_shares = max(int(raw_shares // unit) * unit, 0)
    else:
        suggested_shares = max(int(raw_shares), 0)

    notional = suggested_shares * close
    actual_risk = suggested_shares * risk_per_share

    plan.update(
        {
            "available": True,
            "stop_price": float(stop),
            "stop_pct": float((stop / close - 1.0) * 100.0),
            "take_profit_3r": float(take_profit_3r),
            "tp_pct": float((take_profit_3r / close - 1.0) * 100.0),
            "atr_pct": float(atr14 / close * 100.0),
            "swing_low_20d": float(swing_low) if swing_low is not None else None,
            "risk_dollar_budget": float(risk_dollar),
            "suggested_shares": int(suggested_shares),
            "notional_value": float(notional),
            "actual_risk_dollar": float(actual_risk),
            "lot_unit_note": "台股以 1000 股為一張" if market == "台股" else "美股以股為單位",
        }
    )
    return plan


def fundamentals_summary(fast_info: dict) -> dict[str, Any]:
    """整理 yfinance fast_info 之必要欄位；缺值就略過。"""
    if not fast_info:
        return {"available": False}

    def g(k: str) -> Any:
        try:
            return fast_info.get(k)
        except Exception:
            return None

    out: dict[str, Any] = {"available": True}
    if g("market_cap"):
        try:
            mc = float(g("market_cap"))
            out["market_cap_b"] = mc / 1e9
        except Exception:
            pass
    if g("currency"):
        out["currency"] = g("currency")
    if g("year_high"):
        out["year_high"] = _f(g("year_high"))
    if g("year_low"):
        out["year_low"] = _f(g("year_low"))
    if g("ten_day_average_volume"):
        out["avg_vol_10d"] = _f(g("ten_day_average_volume"))
    if g("three_month_average_volume"):
        out["avg_vol_3m"] = _f(g("three_month_average_volume"))
    if g("fifty_day_average"):
        out["ma50_fast"] = _f(g("fifty_day_average"))
    if g("two_hundred_day_average"):
        out["ma200_fast"] = _f(g("two_hundred_day_average"))
    return out


def explanation_bullets(snapshot: dict, weekly: dict[str, Any]) -> list[str]:
    sym = str(snapshot.get("symbol", ""))
    mkt_zh = str(snapshot.get("market", ""))
    mkt = T(mkt_zh, {"美股": "US", "台股": "TW"}.get(mkt_zh, mkt_zh))
    score = _f(snapshot.get("score"))
    tier_zh = str(snapshot.get("recommendation", ""))
    tier = tier_label(tier_zh) if tier_zh else ""
    bd = snapshot.get("score_breakdown") or {}
    sep = T("、", ", ")

    bullets: list[str] = []
    if score is not None and tier:
        bd_str = sep.join(f"{k} {v:+.2f}" for k, v in bd.items()) if bd else ""
        if get_lang() == "en":
            bullets.append(
                f"**{sym}** ({mkt}) Composite **{score:.2f}** → tier **{tier}**."
                + (f" (breakdown: {bd_str})" if bd_str else "")
            )
        else:
            bullets.append(
                f"**{sym}**（{mkt}）綜合分數 **{score:.2f}** → 等級「**{tier}**」。"
                + (f"（拆解：{bd_str}）" if bd_str else "")
            )

    close = _f(snapshot.get("close"))
    ma20 = _f(snapshot.get("ma20"))
    ma50 = _f(snapshot.get("ma50"))
    ma200 = _f(snapshot.get("ma200"))
    ladder_msgs: list[str] = []
    if close is not None and ma20 is not None:
        ladder_msgs.append(("MA20✔" if close > ma20 else "MA20✘") + f"({ma20:.2f})")
    if close is not None and ma50 is not None:
        ladder_msgs.append(("MA50✔" if close > ma50 else "MA50✘") + f"({ma50:.2f})")
    if close is not None and ma200 is not None:
        ladder_msgs.append(("MA200✔" if close > ma200 else "MA200✘") + f"({ma200:.2f})")
    if ladder_msgs:
        bullets.append(T("日線趨勢階梯：", "Daily trend ladder: ") + sep.join(ladder_msgs))

    if weekly.get("available"):
        wm = []
        if weekly.get("above_ma20"):
            wm.append(T("週線在 MA20 之上", "weekly above MA20"))
        else:
            wm.append(T("週線在 MA20 之下", "weekly below MA20"))
        if weekly.get("above_ma50"):
            wm.append(T("且高於 MA50", "and above MA50"))
        rsi_w = _f(weekly.get("rsi14"))
        mh_w = _f(weekly.get("macd_hist"))
        extras = []
        if rsi_w is not None:
            extras.append(T(f"週 RSI {rsi_w:.1f}", f"weekly RSI {rsi_w:.1f}"))
        if mh_w is not None:
            extras.append(T(f"週 MACD 柱 {mh_w:+.3f}", f"weekly MACD-hist {mh_w:+.3f}"))
        bullets.append(T("週線視角：", "Weekly view: ") + T("，", ", ").join(wm + extras))

    rsi = _f(snapshot.get("rsi14"))
    mh = _f(snapshot.get("macd_hist"))
    mh_msg = ""
    if mh is not None:
        mh_msg = T(
            f"，MACD 柱 **{mh:+.3f}**（>0 偏多）",
            f", MACD-hist **{mh:+.3f}** (>0 = bullish)",
        )
    if rsi is not None:
        bullets.append(T(f"動能：日 RSI(14) **{rsi:.1f}**", f"Momentum: daily RSI(14) **{rsi:.1f}**") + mh_msg)

    rs60 = _f(snapshot.get("rs_60d_pct"))
    rs120 = _f(snapshot.get("rs_120d_pct"))
    if rs60 is not None or rs120 is not None:
        parts = []
        if rs60 is not None:
            parts.append(T(f"60 日 **{rs60:+.2f}%**", f"60D **{rs60:+.2f}%**"))
        if rs120 is not None:
            parts.append(T(f"120 日 **{rs120:+.2f}%**", f"120D **{rs120:+.2f}%**"))
        bullets.append(
            T("相對強弱（vs 對應大盤）：", "Relative strength (vs benchmark): ")
            + T("／", " / ").join(parts)
            + T("（正值＝跑贏大盤）。", " (positive = outperforming).")
        )

    vr = _f(snapshot.get("volume_ratio"))
    vz = _f(snapshot.get("volume_zscore"))
    if vr is not None:
        if vr >= 1.5:
            bullets.append(T(
                f"量能：當日量為 20 日均量的 **{vr:.2f} 倍**（明顯放量）。",
                f"Volume: today's volume is **{vr:.2f}×** the 20D average (clear surge).",
            ))
        elif vr >= 1.0:
            bullets.append(T(
                f"量能：當日量為 20 日均量的 **{vr:.2f} 倍**（小幅放大）。",
                f"Volume: today's volume is **{vr:.2f}×** the 20D average (mild expansion).",
            ))
        else:
            bullets.append(T(
                f"量能：當日量為 20 日均量的 **{vr:.2f} 倍**（縮量）。",
                f"Volume: today's volume is **{vr:.2f}×** the 20D average (contracted).",
            ))
    if vz is not None and vz > 1.0:
        bullets.append(T(
            f"量能 z-score **{vz:.2f}**，屬近 20 日明顯異常放量。",
            f"Volume z-score **{vz:.2f}** — clear 20-day anomaly.",
        ))

    vol60 = _f(snapshot.get("vol_60d_ann"))
    if vol60 is not None:
        bullets.append(T(
            f"波動率：60 日年化 **{vol60 * 100:.1f}%**（>45% 在評分中扣分）。",
            f"Volatility: 60D annualized **{vol60 * 100:.1f}%** (>45% deducts in scoring).",
        ))

    mdd = _f(snapshot.get("mdd_60d"))
    if mdd is not None:
        bullets.append(T(
            f"回撤：近 60 日最大回撤 **{mdd * 100:.1f}%**。",
            f"Drawdown: 60-day MDD **{mdd * 100:.1f}%**.",
        ))

    dh = _f(snapshot.get("dist_to_52w_high_pct"))
    dl = _f(snapshot.get("dist_to_52w_low_pct"))
    if dh is not None and dl is not None:
        bullets.append(T(
            f"52 週位置：距高點 **{dh:+.1f}%**、距低點 **{dl:+.1f}%**。",
            f"52-week position: **{dh:+.1f}%** from high, **{dl:+.1f}%** from low.",
        ))

    if snapshot.get("short_term_signal"):
        ret1 = _f(snapshot.get("ret_1d"))
        atrp = _f(snapshot.get("atr14_pct"))
        loc = _f(snapshot.get("day_close_loc"))
        bullets.append(T(
            f"**短期戰術訊號**：日漲 **{(ret1 or 0) * 100:.2f}%**（≈ {((ret1 or 0) * 100) / (atrp or 1):.2f} ATR）、量比 **{(_f(snapshot.get('volume_ratio')) or 0):.2f}**、收於當日區間 **{(loc or 0) * 100:.0f}%** 位置。",
            f"**Short-term tactical**: daily gain **{(ret1 or 0) * 100:.2f}%** (≈ {((ret1 or 0) * 100) / (atrp or 1):.2f} ATR), vol ratio **{(_f(snapshot.get('volume_ratio')) or 0):.2f}**, close at **{(loc or 0) * 100:.0f}%** of daily range.",
        ))
    else:
        bullets.append(T(
            "短期戰術：今日未滿足「漲幅 ≥ 0.8 ATR + 量比 ≥ 1.5 + 收高」三條件。",
            "Short-term tactical: today doesn't meet 'gain ≥ 0.8 ATR + vol ratio ≥ 1.5 + close high'.",
        ))

    return bullets


def trade_plan_markdown(plan: dict[str, Any]) -> str:
    if not plan.get("available"):
        return T(
            "_資料不足，無法給出 ATR 停損／部位試算。_",
            "_Insufficient data for ATR stop / sizing._",
        )
    if get_lang() == "en":
        lines = [
            "### Position sizing (educational, not advice)",
            f"- Suggested stop: **{plan['stop_price']:.2f}** (~{plan['stop_pct']:+.2f}%; max of 2×ATR and 20-day swing low)",
            f"- 3R take-profit: **{plan['take_profit_3r']:.2f}** (~{plan['tp_pct']:+.2f}%)",
            f"- 14-day ATR / close ≈ **{plan['atr_pct']:.2f}%**",
            f"- Risk budget: **${plan['risk_dollar_budget']:,.0f}** → suggested **{plan['suggested_shares']:,} shares**"
            f" (notional ≈ {plan['notional_value']:,.0f}, actual risk ≈ {plan['actual_risk_dollar']:,.0f})",
            f"- Note: {plan['lot_unit_note']}.",
        ]
    else:
        lines = [
            "### 操作試算（教學示範，非投資建議）",
            f"- 建議停損：**{plan['stop_price']:.2f}**（約 {plan['stop_pct']:+.2f}%；以 max(2×ATR, 20 日 swing low) 取較保守者）",
            f"- 3R 停利目標：**{plan['take_profit_3r']:.2f}**（約 {plan['tp_pct']:+.2f}%）",
            f"- 14 日 ATR / 收盤 ≈ **{plan['atr_pct']:.2f}%**",
            f"- 風險預算：**${plan['risk_dollar_budget']:,.0f}** → 建議部位 **{plan['suggested_shares']:,} 股**"
            f"（總市值 ≈ {plan['notional_value']:,.0f}，實際單筆風險 ≈ {plan['actual_risk_dollar']:,.0f}）",
            f"- 註：{plan['lot_unit_note']}。",
        ]
    return "\n".join(lines)


def invalidation_markdown(snapshot: dict) -> str:
    close = _f(snapshot.get("close"))
    ma50 = _f(snapshot.get("ma50"))
    items = [T(
        "### 訊號失效條件（通用，任一觸發即重新評估）",
        "### Invalidation rules (any trigger → re-evaluate)",
    )]
    if close is not None and ma50 is not None:
        items.append(T(
            f"- 日 K 收破 **MA50（{ma50:.2f}）**：原始多頭結構轉弱。",
            f"- Daily close below **MA50 ({ma50:.2f})**: bullish structure weakens.",
        ))
    items.append(T(
        "- 日 K 出現 **單根跌幅 ≥ 1.5 ATR** 且伴隨 **量比 ≥ 1.5** → 視為主要分布訊號。",
        "- Single-day drop **≥ 1.5 ATR** with **vol ratio ≥ 1.5** → treat as major distribution.",
    ))
    items.append(T(
        "- **相對強弱（vs 大盤）連續 5 個交易日為負** 且新低：相對動能消失。",
        "- Relative strength **negative for 5 consecutive days** and at new lows: momentum gone.",
    ))
    items.append(T(
        "- 短期戰術部位：**進場 3 日內未達 +1R** 或 **跌破當日低點** 即停損。",
        "- Short-term tactical: **failure to reach +1R within 3 days** or **break of entry-day low** → stop.",
    ))
    return "\n".join(items)


def strategy_status_markdown(snapshot: dict, enriched: pd.DataFrame, strategy) -> str:
    """組出『當前選用策略』的進出場條件 + 今日狀態。"""
    if strategy is None:
        return ""
    ev = strategy.evaluate(snapshot, enriched)
    score = ev.get('score', 0)
    rec_zh = ev.get('recommendation', '-')
    rec = tier_label(rec_zh) if rec_zh else "-"
    if get_lang() == "en":
        parts: list[str] = [
            f"### Current strategy: {strategy.label}",
            f"_{strategy.description}_",
            "",
            f"- Strategy score: **{score:.2f} / 10** → tier **{rec}**",
            f"- Entry today: **{'✅ Yes' if ev.get('entry_today') else 'No'}**",
            f"- Exit today: **{'🔻 Yes' if ev.get('exit_today') else 'No'}**",
            "",
            "**Entry rules**",
        ]
    else:
        parts = [
            f"### 當前策略：{strategy.label}",
            f"_{strategy.description}_",
            "",
            f"- 策略分數：**{score:.2f} / 10** → 等級 **{rec}**",
            f"- 今日進場是否成立：**{'✅ 是' if ev.get('entry_today') else '否'}**",
            f"- 今日出場是否成立：**{'🔻 是' if ev.get('exit_today') else '否'}**",
            "",
            "**進場條件**",
        ]
    for r in strategy.entry_rules_text:
        parts.append(f"- {r}")
    parts.append("")
    parts.append(T("**出場條件**", "**Exit rules**"))
    for r in strategy.exit_rules_text:
        parts.append(f"- {r}")

    hits = ev.get("rule_hits", [])
    misses = ev.get("rule_misses", [])
    sep = T("、", ", ")
    if hits or misses:
        parts.append("")
        if hits:
            parts.append(T("> ✓ **目前命中**：", "> ✓ **Currently met**: ") + sep.join(hits))
        if misses:
            parts.append(T("> ✗ **目前未命中**：", "> ✗ **Currently missed**: ") + sep.join(misses))

    ex_reasons = ev.get("exit_today_reasons", [])
    if ex_reasons:
        parts.append("")
        parts.append(T("> ⚠️ **今日已觸發出場條件**：", "> ⚠️ **Exit triggered today**: ") + T("；", "; ").join(ex_reasons))
    return "\n".join(parts)


def fundamentals_markdown(fund: dict[str, Any]) -> str:
    if not fund or not fund.get("available"):
        return ""
    lines = [T("### 報價／市值快照", "### Quote / Market-cap snapshot")]
    if "market_cap_b" in fund:
        cur = fund.get("currency") or ""
        lines.append(T(
            f"- 市值：約 **{fund['market_cap_b']:,.1f} B {cur}**",
            f"- Market cap: ~**{fund['market_cap_b']:,.1f} B {cur}**",
        ))
    if "year_high" in fund and "year_low" in fund:
        lines.append(T(
            f"- 52 週高 / 低：**{fund['year_high']:.2f} / {fund['year_low']:.2f}**",
            f"- 52W high / low: **{fund['year_high']:.2f} / {fund['year_low']:.2f}**",
        ))
    if "avg_vol_10d" in fund or "avg_vol_3m" in fund:
        v10 = fund.get("avg_vol_10d")
        v3m = fund.get("avg_vol_3m")
        s = []
        if v10:
            s.append(T(f"10 日均量 {v10:,.0f}", f"10D avg vol {v10:,.0f}"))
        if v3m:
            s.append(T(f"3 月均量 {v3m:,.0f}", f"3M avg vol {v3m:,.0f}"))
        if s:
            lines.append(T("- 流動性：", "- Liquidity: ") + T("、", ", ").join(s))
    return "\n".join(lines)


def strategy_framework_markdown() -> str:
    if get_lang() == "en":
        return (
            "### Scoring framework (v2)\n\n"
            "1. **Trend ladder**: close > MA20, close > MA50, MA20 > MA50, MA50 > MA200.\n"
            "2. **Momentum**: RSI(14) prefers 45–65; MACD histogram for direction.\n"
            "3. **Relative strength**: 60D / 120D excess return vs benchmark (^GSPC for US, ^TWII for TW).\n"
            "4. **Volume**: today's volume / 20D average; also volume z-score.\n"
            "5. **Volatility penalty**: 60D annualized > 45% starts deducting.\n"
            "6. **Drawdown penalty**: 60D MDD < ‑18% starts deducting.\n"
            "7. **Short-term tactical**: gain ≥ 0.8×ATR(14) + vol ratio ≥ 1.5 + close in upper 60% of range.\n\n"
            "> Rules are for research demo only — **not investment advice**. Verify before any trade."
        )
    return (
        "### 本工具策略框架（v2）\n\n"
        "1. **趨勢階梯**：close > MA20、close > MA50、MA20 > MA50、MA50 > MA200。\n"
        "2. **動能**：RSI(14) 偏好 45–65；MACD 柱輔助方向。\n"
        "3. **相對強弱**：與對應大盤（美股 ^GSPC、台股 ^TWII）比 60／120 日超額報酬。\n"
        "4. **量能**：當日量 / 20 日均量；額外觀察 z-score。\n"
        "5. **波動率扣分**：60 日年化 > 45% 開始扣分。\n"
        "6. **回撤扣分**：60 日最大回撤 < ‑18% 開始扣分。\n"
        "7. **短期戰術**：漲幅 ≥ 0.8×ATR(14) + 量比 ≥ 1.5 + 收於日內區間上 60%。\n\n"
        "> 規則為研究示範，**不構成投資建議**；任何進出場前請自行驗證。"
    )


def full_recommendation_markdown(
    snapshot: dict,
    *,
    daily_df: pd.DataFrame | None = None,
    fast_info: dict | None = None,
    strategy=None,
) -> str:
    """個股頁的完整推薦：技術解讀 + 策略狀態 + 基本面（不含部位試算，那塊改到資產規劃頁）。"""
    weekly = weekly_view(daily_df) if daily_df is not None else {"available": False}
    fund = fundamentals_summary(fast_info or {})

    parts: list[str] = [T("### 推薦解讀（自動生成）", "### Auto-generated analysis"), ""]
    for b in explanation_bullets(snapshot, weekly):
        parts.append(f"- {b}")

    if strategy is not None and daily_df is not None:
        from analysis import add_indicators
        enr = add_indicators(daily_df, include_full=True)
        parts += ["", strategy_status_markdown(snapshot, enr, strategy)]
    else:
        parts += ["", invalidation_markdown(snapshot)]

    fund_md = fundamentals_markdown(fund)
    if fund_md:
        parts += ["", fund_md]
    parts += ["", "---", strategy_framework_markdown()]
    return "\n".join(parts)
