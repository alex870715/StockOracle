"""
Plotly 走勢圖（深色主題）：
- 主圖：K 線 + 任意 MA（由 ma_periods 列表動態畫）+ 布林通道（可選）+ 進場 / 出場標記 + 基準對照（可選）
- 副圖一：成交量（量柱配合 K 線顏色）
- 副圖二：MACD（柱 + DIF + 訊號線）
- 副圖三：RSI(14)（含 30/50/70 參考）
- 時間軸範圍按鈕（1M/3M/6M/YTD/1Y/Max）+ log 切換 + 跨子圖十字游標

注意：基準對照走「主圖右側軸（secondary_y）」，必須由 make_subplots 的 specs 預先聲明，
不能事後 update_layout(yaxis2=...) — 否則會與成交量副圖的 yaxis2 撞名導致畫面崩壞。
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


_DEFAULT_MA_PERIODS = (20, 50, 200)
# 預先排好的調色盤；不夠時迴圈使用
_MA_COLORS = (
    "#ffb74d",  # 橘
    "#4fc3f7",  # 淺藍
    "#b39ddb",  # 紫（MA200 預設用這個 + dash）
    "#81c784",  # 綠
    "#f06292",  # 粉
    "#ffd54f",  # 黃
    "#80cbc4",  # 青
    "#a1887f",  # 棕
)


def _safe_signal_marks(d: pd.DataFrame) -> tuple[list, list]:
    """fallback：以舊的 short_term_v2 條件對歷史 K 標記進場點（沒外部 signals 時用）。"""
    if not {"ret_1d", "volume_ratio", "atr14_pct", "day_close_loc", "high"}.issubset(d.columns):
        return [], []
    ret = d["ret_1d"]
    vr = d["volume_ratio"]
    atrp = d["atr14_pct"]
    loc = d["day_close_loc"]
    cond = (ret * 100.0 / atrp.replace(0, np.nan) >= 0.8) & (vr >= 1.5) & (loc >= 0.6)
    cond = cond.fillna(False)
    xs = list(d.index[cond])
    ys = list(d["high"][cond] * 1.01)
    return xs, ys


def _marker_y(d: pd.DataFrame, dates: list, *, source: str) -> list:
    """把 entry / exit 日期對應到圖上的 y 位置：進場用 high*1.02、出場用 low*0.98。"""
    if not dates:
        return []
    s = pd.Series(d.index, index=d.index)
    aligned = [pd.Timestamp(x) for x in dates if pd.Timestamp(x) in s.index]
    if not aligned:
        return []
    if source == "entry":
        return [float(d.loc[x, "high"]) * 1.02 for x in aligned]
    return [float(d.loc[x, "low"]) * 0.98 for x in aligned]


def _normalize_to_first(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return s
    return s / float(s.iloc[0]) * 100.0


def _color_for_ma(period: int, idx: int) -> tuple[str, str]:
    """回傳 (color, dash)；MA200 強制用紫色 + 點線，其他依索引輪流。"""
    if period >= 200:
        return ("#b39ddb", "dot")
    if period >= 100:
        return ("#90a4ae", "dash")
    return (_MA_COLORS[idx % len(_MA_COLORS)], "solid")


def build_ohlcv_figure(
    df: pd.DataFrame,
    symbol: str,
    *,
    ma_periods: Iterable[int] | None = None,
    show_bb: bool = False,
    show_signals: bool = True,
    show_macd: bool = True,
    show_rsi: bool = True,
    log_scale: bool = False,
    bench_close: pd.Series | None = None,
    bench_name: str = "",
    height: int | None = None,
    entry_dates: list | None = None,
    exit_dates: list | None = None,
    strategy_label: str | None = None,
) -> go.Figure:
    need = {"open", "high", "low", "close", "volume"}
    if not need.issubset(set(df.columns)):
        fig = go.Figure()
        fig.update_layout(title=f"{symbol}：資料欄位不足", template="plotly_dark")
        return fig

    d = df.copy().dropna(subset=["open", "high", "low", "close"])
    if d.empty:
        fig = go.Figure()
        fig.update_layout(title=f"{symbol} 無有效 K 線", template="plotly_dark")
        return fig

    periods = sorted(set(int(p) for p in (ma_periods if ma_periods is not None else _DEFAULT_MA_PERIODS) if int(p) > 0))

    # 排版
    rows = 1 + (1 if "volume" in d.columns else 0) + (1 if show_macd else 0) + (1 if show_rsi else 0)
    row_heights = [0.46]
    title_main = f"{symbol} 日線 · OHLC"
    if strategy_label:
        title_main += f"　·　策略：{strategy_label}"
    titles = [title_main]
    panel_index = {"price": 1}
    cur = 1
    if "volume" in d.columns:
        cur += 1
        panel_index["vol"] = cur
        row_heights.append(0.16)
        titles.append("成交量")
    if show_macd:
        cur += 1
        panel_index["macd"] = cur
        row_heights.append(0.20)
        titles.append("MACD (12,26,9)")
    if show_rsi:
        cur += 1
        panel_index["rsi"] = cur
        row_heights.append(0.18)
        titles.append("RSI(14)")
    h = height or (320 + 160 * (rows - 1))

    # specs：第 1 列宣告 secondary_y，給「基準標準化」用（避免和副圖的 yaxis2 撞名）
    specs = [[{"secondary_y": True}]]
    for _ in range(rows - 1):
        specs.append([{"secondary_y": False}])

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=row_heights,
        subplot_titles=tuple(titles),
        specs=specs,
    )

    # ---- 主圖：K 線 ----
    fig.add_trace(
        go.Candlestick(
            x=d.index,
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            name="K 線",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
            showlegend=False,
        ),
        row=1, col=1, secondary_y=False,
    )

    # ---- MA：依 ma_periods 動態畫；分析模組沒提供時就現算 ----
    cls = pd.to_numeric(d["close"], errors="coerce")
    for i, p in enumerate(periods):
        col = f"ma{p}"
        if col in d.columns and d[col].notna().any():
            ser = pd.to_numeric(d[col], errors="coerce")
        elif cls.dropna().shape[0] >= p:
            ser = cls.rolling(p, min_periods=p).mean()
        else:
            continue
        if not ser.notna().any():
            continue
        color, dash = _color_for_ma(p, i)
        width = 1.6 if p >= 200 else (1.4 if p >= 50 else 1.5)
        fig.add_trace(
            go.Scatter(
                x=d.index, y=ser, name=f"MA{p}",
                line=dict(color=color, width=width, dash=dash),
            ),
            row=1, col=1, secondary_y=False,
        )

    # ---- 布林通道 ----
    if show_bb and {"bb_upper", "bb_lower"}.issubset(d.columns):
        fig.add_trace(
            go.Scatter(x=d.index, y=d["bb_upper"], name="BB Upper",
                       line=dict(color="rgba(176,190,197,0.55)", width=1)),
            row=1, col=1, secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(x=d.index, y=d["bb_lower"], name="BB Lower",
                       line=dict(color="rgba(176,190,197,0.55)", width=1),
                       fill="tonexty", fillcolor="rgba(176,190,197,0.08)"),
            row=1, col=1, secondary_y=False,
        )

    # ---- 進場 / 出場標記 ----
    if show_signals:
        if entry_dates is not None:
            ent_xs = list(entry_dates)
            ent_ys = _marker_y(d, ent_xs, source="entry")
        else:
            ent_xs, ent_ys = _safe_signal_marks(d)
        if ent_xs:
            fig.add_trace(
                go.Scatter(
                    x=ent_xs, y=ent_ys, mode="markers", name="進場訊號",
                    marker=dict(symbol="triangle-up", size=12, color="#ffd54f",
                                line=dict(color="#1a1c24", width=1)),
                    hovertemplate="進場<br>%{x|%Y-%m-%d}<extra></extra>",
                ),
                row=1, col=1, secondary_y=False,
            )

        if exit_dates:
            ext_xs = list(exit_dates)
            ext_ys = _marker_y(d, ext_xs, source="exit")
            if ext_xs:
                fig.add_trace(
                    go.Scatter(
                        x=ext_xs, y=ext_ys, mode="markers", name="出場訊號",
                        marker=dict(symbol="triangle-down", size=12, color="#ef5350",
                                    line=dict(color="#1a1c24", width=1)),
                        hovertemplate="出場<br>%{x|%Y-%m-%d}<extra></extra>",
                    ),
                    row=1, col=1, secondary_y=False,
                )

    # ---- 基準對照（兩條標準化線；放在主圖右側軸，不影響價格軸縮放）----
    if bench_close is not None and not bench_close.empty:
        bn = _normalize_to_first(bench_close.reindex(d.index).ffill())
        if not bn.empty:
            cn = _normalize_to_first(d["close"])
            fig.add_trace(
                go.Scatter(
                    x=cn.index, y=cn.values, name=f"{symbol} 標準化",
                    line=dict(color="#ffffff", width=1.0, dash="dash"),
                    opacity=0.55,
                ),
                row=1, col=1, secondary_y=True,
            )
            fig.add_trace(
                go.Scatter(
                    x=bn.index, y=bn.values, name=f"{bench_name or '基準'} 標準化",
                    line=dict(color="#ff8a65", width=1.0, dash="dash"),
                    opacity=0.7,
                ),
                row=1, col=1, secondary_y=True,
            )
            fig.update_yaxes(
                title_text="標準化(=100)", showgrid=False,
                row=1, col=1, secondary_y=True,
                tickfont=dict(size=10), title_font=dict(size=10),
            )

    # ---- 量 ----
    if "vol" in panel_index:
        op = pd.to_numeric(d["open"], errors="coerce")
        cl = pd.to_numeric(d["close"], errors="coerce")
        colors = ["#26a69a" if (c >= o) else "#ef5350" for o, c in zip(op, cl)]
        fig.add_trace(
            go.Bar(
                x=d.index, y=pd.to_numeric(d["volume"], errors="coerce").fillna(0),
                name="成交量", marker_color=colors, opacity=0.85, showlegend=False,
            ),
            row=panel_index["vol"], col=1,
        )

    # ---- MACD ----
    if "macd" in panel_index and {"macd", "macd_signal", "macd_hist"}.issubset(d.columns):
        hist = pd.to_numeric(d["macd_hist"], errors="coerce").fillna(0)
        macd_colors = ["#26a69a" if v >= 0 else "#ef5350" for v in hist]
        fig.add_trace(
            go.Bar(x=d.index, y=hist, name="MACD 柱",
                   marker_color=macd_colors, opacity=0.7, showlegend=False),
            row=panel_index["macd"], col=1,
        )
        fig.add_trace(
            go.Scatter(x=d.index, y=d["macd"], name="DIF",
                       line=dict(color="#80cbc4", width=1.2)),
            row=panel_index["macd"], col=1,
        )
        fig.add_trace(
            go.Scatter(x=d.index, y=d["macd_signal"], name="MACD 訊號",
                       line=dict(color="#ce93d8", width=1.2, dash="dot")),
            row=panel_index["macd"], col=1,
        )

    # ---- RSI ----
    if "rsi" in panel_index and "rsi14" in d.columns:
        fig.add_trace(
            go.Scatter(x=d.index, y=d["rsi14"], name="RSI14",
                       line=dict(color="#ce93d8", width=1.4), showlegend=False),
            row=panel_index["rsi"], col=1,
        )
        for y_val, dash in ((70, "dash"), (50, "dot"), (30, "dash")):
            fig.add_hline(
                y=y_val, line_dash=dash, line_color="rgba(200,200,200,0.45)",
                row=panel_index["rsi"], col=1,
            )

    fig.update_layout(
        height=h,
        template="plotly_dark",
        paper_bgcolor="#131722",
        plot_bgcolor="#131722",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=11)),
        margin=dict(l=52, r=52, t=56, b=40),
        font=dict(color="#d1d4dc"),
        bargap=0.15,
    )
    fig.update_xaxes(
        showgrid=True, gridcolor="rgba(42,46,57,0.9)", zeroline=False,
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikedash="dot", spikecolor="rgba(255,255,255,0.3)", spikethickness=1,
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="rgba(42,46,57,0.9)", zeroline=False,
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikedash="dot", spikecolor="rgba(255,255,255,0.2)", spikethickness=1,
    )

    if log_scale:
        fig.update_yaxes(type="log", row=1, col=1, secondary_y=False)

    fig.update_xaxes(
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(label="Max", step="all"),
            ],
            bgcolor="rgba(255,255,255,0.05)",
            activecolor="rgba(255,213,79,0.4)",
            font=dict(color="#d1d4dc", size=11),
            x=0, y=1.18,
        ),
        row=1, col=1,
    )

    return fig
