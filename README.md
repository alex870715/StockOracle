# StockOracle

> 多策略選股 + 資產規劃 + 走步式回測（walk-forward）的 Streamlit 桌面工具，支援台股／美股、繁中／英文雙語切換。

不是訊號搬運工，是把你「想得到的紀律」寫成一個可量化、可回測的工作流：選股、評分、配置、再平衡、回測、檢討，全在一個頁面跑完。

> ⚠️ 本工具為**研究示範用途**，不構成任何投資建議。歷史回測表現不代表未來收益。

---

## 主要特色

### 多策略 Engine（5 套，模組化擴充）

| 策略 | 適用週期 | 風險 | 進場條件 |
|---|---|---|---|
| 短期激進做多 | 短線 | 高 | 漲幅 ≥ 0.8×ATR + 量比 ≥ 1.5 + 收於日內上 60% |
| 動能突破 | 中期 | 中 | 站上 20 日新高 + 量比 ≥ 1.3 + RSI 50–75 |
| 中長期趨勢 | 中長期 | 低 | Close > MA200 + MA50 > MA200 + 60D RS ≥ 0% |
| 反轉接刀 | 短線 | 高 | RSI < 30 + 觸布林下軌 + 紅 K |
| 防守型 ETF | 中長期 | 低 | 60D 年化波動 < 25% + MDD > ‑10% + Close > MA200 |

每個策略都會給出：策略命中度（0–10）、進出場規則文字、即時觸發狀態、歷史進出場標記（圖上黃／紅三角）。

### 資產規劃 + 真 Walk-Forward 回測

- **目標 CAGR 計算**：輸入資金、目標、期間，自動算出可行性（綠 / 黃 / 紅燈）
- **三段式風險偏好**：保守 / 平衡 / 積極，控制現金緩衝、單檔上限、ATR 停損倍數、停利 R 倍數
- **Goal-based Allocator**：依當下評分加權配置，含手動勾選或自動 Top N
- **三層再平衡**：
  - 個股層級：ATR 停損、N×R 停利
  - 組合層級：總值回撤、總值漲幅觸發
  - 時間層級：定期再排
- **Walk-Forward 回測**：每次再平衡都用「截至當天」的資料重新挑 Top N，無 look-ahead bias
- **真實交易成本**：手續費、證交稅、滑價（台股 / 美股不同預設值）
- **基準對照**：^TWII / ^GSPC NAV 同期間疊圖

### 圖表

- TradingView 風格 K 線 + 可動態切換的 5/10/20/30/60/120/200 MA
- MACD、RSI、布林通道、量能可獨立開關
- 大盤同期間疊圖（次 y 軸）
- 策略歷史進出場標記（已做 rising-edge + 1-to-1 配對，畫面乾淨不雜亂）

### 中英雙語

Sidebar 一鍵切換 zh / en，全部 UI、策略名稱、條件描述、回測 metric、推薦解讀都跟著變，但資料層的 key 維持中文以保持邏輯穩定。

### 台股全市場支援

一鍵同步 TWSE 上市 + 上櫃白名單（~2300 檔），包含 ETF 與較冷門個股。

---

## 安裝

需要 Python 3.9+。

```bash
git clone <your-repo-url>
cd StockOracle

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 啟動

```bash
streamlit run app.py
```

預設會跑「台股 中型 ~50 檔」清單；想跑全市場：

1. Sidebar → 股票池規模選「全 TW 上市」或「全 TW 上市+上櫃」
2. 按「🔄 同步台股全市場清單」（首次 ~30 秒）
3. 等指標計算完（首次 5–15 分鐘，之後走 disk cache）

---

## 模組架構

```
app.py                    # Streamlit UI（4 個 tab：排名 / 個股 / 短期 / 規劃）
i18n.py                   # 雙語字典 + t() / set_lang()
data_loader.py            # Yahoo Finance 抓取 + retry + disk cache
analysis.py               # 技術指標：MA / RSI / ATR / Bollinger / MACD / 相對強弱
strategies.py             # 5 套策略：evaluate / historical_signals
daily_pick.py             # 多因子綜合分數 + 排名 + 短期戰術訊號
planner.py                # Goal-based 配置 + 三層再平衡 + walk-forward 回測
recommendation.py         # 推薦解讀 markdown 生成
charts.py                 # OHLCV 多子圖 + 指標 + 進出場標記 + 基準對照
universe.py               # 預設股票池
symbol_meta.py            # 代號↔名稱映射
tools/sync_tw_universe.py # TWSE / TPEx 名單抓取
tests/test_analysis.py    # 12 個 sanity test
```

---

## 跑測試

```bash
python3 tests/test_analysis.py
# 或
python3 -m pytest tests/test_analysis.py -v
```

---

## 設計取捨與已知限制

**已處理（v2）：**
- ✅ 走步式 walk-forward 回測（不偷看未來）
- ✅ 真實交易成本（手續費、證交稅、滑價）
- ✅ 股息已自動還原（yfinance `auto_adjust=True`）
- ✅ 出場訊號 rising-edge + 1-to-1 entry/exit 配對（圖不再雜亂）

**還沒做的：**
- 個股基本面（EPS / PE / 殖利率）尚未進入評分
- 沒有融資、放空、選擇權、ETF 槓桿模擬
- 沒有單一日內成交量上限的「真實流動性約束」
- 預設 yfinance 為唯一資料源（盤中即時資料品質有限）

---

## 免責聲明

本工具的所有評分、推薦、回測結果**僅供研究與教育用途**，不構成任何形式的投資建議或勸誘。所有歷史回測都會 over-fit 到特定樣本期間，不能保證未來收益。任何投資決策請自行評估風險、自負盈虧。

---

## License

[Apache License 2.0](./LICENSE)
