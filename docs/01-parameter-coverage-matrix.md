# 參數溯源矩陣（Parameter Coverage Matrix）

日期：2026-07-23 | 最後同步：2026-07-27 | 狀態：**有效溯源參考；UI 位置以六份定稿設計稿為準** | 範圍：Phase 1（MVP）為主，Phase 2 項目標明
配套：TRADING_SPEC_v0.41（§15 參數初值表）、六頁 app 骨架、文件合約模式

## 0. 約定

**輸入渠道**（每個參數必須恰好一個主渠道）：
- `檔` = 策略文件（terminal AI 生成，app 讀入驗證）
- `UI` = app 頁面表單輸入
- `系統` = 版本化設定或實作預設；**MVP 冇獨立設定頁**

**覆蓋規則**（驗收標準）：
1. 每個參數：一個主渠道 + 至少一個消費邏輯 + 至少一頁顯示；
2. 每個前端輸入元素：必須對應本表一個參數（唔准有無主孤魂掣）；
3. 每段後端邏輯需要嘅參數：必須喺本表搵到入口。

**頁面代號**：P1 總覽 / P2 策略工作台 / P3 數據 / P4 回測 / P5 結果 / P6 模擬盤

---

## 1. 策略文件參數（渠道：檔；顯示：P2 策略工作台，唯讀）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| EMA 週期組 | 18 / 50 / 90（邏輯用 18/90） | 各層指標計算、交叉狀態 | P1 |
| ATR 週期 | 14 | regime 主閘、記錄層 | P1 |
| TF trio | D / 1H / 5m | 三層分工流程 | P1 |
| 中 TF 選項 | ∈ {4H, 2H, 1H, 30m} | 同上 | P1 |
| 入市 TF 選項 | ∈ {5m, 3m, 1m} | 同上 | P1 |
| 入市層數 | 三層（兩層=變體） | 入市流程分支 | P1 |
| regime sep_mult / flat_mult | 自身分佈 60–70 percentile | regime 主閘（需預計算 pass） | P1 |
| 斜率窗 N | Daily 5；intraday **10**（2026-07-24 定，範圍 10–20 可調） | regime 主閘 | P1 |
| shrink_mult | 0.6（範圍 0.5–0.7） | Congestion 判別 | P1 |
| Congestion 觀察 K | 逐層（D 2–3 … 5m 20–30） | Congestion 判別 | P1 |
| 價格盒窗口 | 20 日（10 變體） | Daily range 證據 | P1 |
| Signal bar 啟用組 | Inside + Magic（LMR=P2） | 入市觸發 | P1 |
| Inside bar entry/stop 變體 | mother bar 版（aggressive 變體） | 入市觸發、stop 計算 | P1 |
| Pullback 掂線 | 90 EMA（18 / value zone 變體） | 第一次回踩生命週期 | P1 |
| Take profit | 1R | 離場 | P1 |
| Time stop | off（留位） | 離場 | P1 |
| 時段 filter | off（record tag on） | 核心閘 / 記錄層 | P1 |
| LMR stop 變體 | A（step2 段）；B 變體 | LMR 狀態機 | **P2** |
| 進階 LMR 進取變體 | off | 跨層 LMR | **P2** |
| `rationale`（必填文字） | — | 記分卡維度 8、晉升清單 | P1 |
| `unquantified_notes` | — | P2 策略工作台顯示、Owner 確認 | P1 |
| `universe.contracts`＋`expansion_rationale` | primary 必在內；新增 member 逐個非空理由 | P2 whole-strategy 確認、P4 run 子集資格、審計 | P1 |
| `universe.session` | `rth`／`eth` | catalog 支援 gate、回測資料範圍、execution session | P1 |

### 1.1 Market identity 分層（2026-07-27 Owner 拍板）

| 欄位 | 主渠道 | 消費邏輯 | UI／artifact |
|---|---|---|---|
| sketch `instrument`／strategy `primary_instrument` | **UI**：Owner 喺 P2 草圖揀一次；Terminal 只可照抄 | 草圖語境、strategy universe primary、P4 run eligibility | P2 #28–#34；root/product symbol |
| `asset_class`／`display_name`／`currency`／`sessions` | **系統**：canonical `config/contracts.yaml` | sketch export、strategy validation、P4 合約選擇、Nautilus adapter | P2 顯示唯讀；唔准 frontend/Terminal 自由輸入 |
| strategy `contracts`／`expansion_rationale` | **檔**：Terminal self-contained strategy | same-class/session/USD/exact-key validation；P4 只選子集 | P2 唯讀整份接受／退回 |
| exact expiry contract id | **系統＋P4 UI**：由已確認 root universe 同可用資料選 | run execution、result、paper baseline | 只住 immutable run/baseline snapshot，唔回寫 strategy |

## 2. 資金與風控參數（渠道：檔，Trading Profile 段；顯示：P2 + P6）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| risk_pct（每單風險） | 1%（0.5–2%） | position sizing | P1 |
| 日虧損限額 | 3R | 即日停開新單 | P1 |
| 每點價值 / tick size | 按合約（系統合約表） | sizing、stop 計算 | P1 |

## 3. 數據參數（渠道：UI = P3 數據頁）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 合約清單 | NQ / YM / GC | 下載、回測、模擬盤 | P1 |
| 基礎 bar | 1 分鐘（大 TF 由此合成） | 數據儲存、intrabar 模擬 | P1 |
| 下載日期範圍 | — | 分段下載器 | P1 |
| 轉倉跳過窗口 | 前後共 N 日（初值待定，建議 3） | 不交易日名單 | P1 |
| IB 連接設定（host/port/帳戶） | — | 連接管理 | P1 |

## 4. 數據質量參數（渠道：系統；顯示：P3 質量報告）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 漏 bar 容忍度 | 0（開市時段內見漏即標） | 完整性檢查 | P1 |
| 異常 spike 門檻 | 14-period prior Wilder ATR × 8（2026-07-24 定；只記錄不刪數據） | 異常值檢查 | P1 |
| 自洽性抽查比例 | 抽樣對數 | 1m 合成 vs 原生 bar 對照 | P1 |
| 可疑日處理 | 標記後 Owner 裁決 | 不回測名單 | P1 |

## 5. 回測參數（渠道：UI = P4 回測頁）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 策略版本 | —（由 P2 揀） | 引擎載入 | P1 |
| 合約 + 日期範圍 | — | run 定義 | P1 |
| 初始資金 | — | sizing、權益曲線 | P1 |
| 手續費（每邊每手，全包） | **NQ $2.50／YM $2.50／GC $2.80**（2026-07-24 定；IBKR $0.85 + 交易所/監管費，保守取整） | 成本模型 | P1 |
| 滑點（tick 數，按訂單類型） | **入市突破 1 tick／止損離場 2 ticks／目標限價 0／日終市價 1 tick**（2026-07-24 定；P2 變體：目標要穿過先成交） | 成本模型 | P1 |
| 成交模型 | 保守（stop 先；跳空用開市價） | 引擎執行語義（spec §14） | P1 |
| 執行模擬粒度 | 1 分鐘 | intrabar 觸發模擬 | P1 |
| 批量：多策略 × 多合約矩陣（D19；涵蓋原「三市場一鍵」） | 揀 N 個策略版本 × M 合約 → N×M runs 隊列 | run 隊列 + P5 對比、記分卡維度 6 | P1 |
| 參數掃描網格 | — | 參數平原自動化 | **P2** |
| Walk-forward 設定（IS/OOS%、段數） | 70/30、6 段 | 維度 5 自動化 | **P2** |
| Monte Carlo 次數 | 1000 | 維度 5 自動化 | **P2** |

## 6. 評估／記分卡參數（渠道：系統；顯示：P5 結果頁）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 最少交易次數警告線 | 100 | 維度 1 | P1 |
| 交易數÷參數數警告線 | 30:1 | 維度 1 | P1 |
| 成本敏感度情境 | ×1 / ×1.5 / ×2 | 維度 4（重定價） | P1 |
| 時段切割方式 | 按年 | 維度 6 | P1 |
| 分佈形狀置信度 | 95%（VaR/CVaR 用） | 維度 3b：偏度/峰度/tail ratio/VaR/CVaR | P1 |
| 利潤集中度 Top-N | N = 5、10 | 維度 2b：好運依賴檢測 | P1 |
| PSR 基準 Sharpe | 0 | 維度 2b：真 Sharpe>0 概率 | P1 |
| MC 概率情境設定 | 模擬 1000 次、置信 95% | 維度 5b：P(回撤>X)、破產概率、bootstrap CI、DSR | **P2** |

## 6.5 圖表檢視器（P5 結果頁；Owner 拍板 2026-07-24 入 P1）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| Timeframe 切換 | 策略 trio 之內（D/1H/5m） | 圖表數據載入（由 canonical 1m 合成） | P1 |
| 指標線 | **無輸入**——由策略文件自動推導（18/50/90 EMA） | 圖表 overlay 渲染 | P1 |
| 交易標記／止損目標線 | **無輸入**——由 record layer／run 結果推導 | 圖表 marker/price line 渲染 | P1 |
| 交易跳轉 | 由 P5 交易列表點擊觸發 | 圖表視窗定位 | P1 |
| Regime 著色、LMR/倉結構、逐 bar 回放、多圖同步、盤前 level 上圖 | — | 圖表進階層 | **P2** |

## 6.6 策略工作台分頁 ①草圖／分頁 ②試跑預覽（D18；P1.5）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 草圖四格 TF 組合 | D / 1H / 30m / 5m（可換） | 策略工作台草圖四格圖 | P1.5 |
| 草圖顯示範圍 | 逐格自訂（default 各 TF 近 120 支 bar） | 圖表載入 | P1.5 |
| 繪圖類型 | 趨勢線、水平位（P2 再擴） | 畫作 → meta.yaml 座標 | P1.5 |
| 草圖包匯出路徑 | 系統默認資料夾 | A2a 寫入器 | P1.5 |
| 策略預覽 dry-run 窗口 | 最近 30 個交易日（系統默認可調） | S2c 預覽圖（引擎 dry-run） | P1.5 |

## 7. 模擬盤參數（渠道：UI = P6 模擬盤頁）

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| 交易員策略版本（鎖定） | 只列結果頁已記錄「用得，去模擬盤」嘅不可變版本 | 模擬器載入 | P1 |
| 合約 | NQ/YM/GC 之一 | 模擬器 | P1 |
| 對照基準 `run_id`（鎖定） | Owner 親手揀；唔准自動取最近一次 | live vs 回測偏離嘅唯一 baseline | P1 |
| 模擬初始資金（鎖定） | 等於所選 baseline run 嘅初始資金；目前 USD 100,000 | 每個交易員建立自己嘅獨立模擬帳戶，唔共同攤分 | P1 |
| Timeframe configuration（鎖定） | typed `market_input=1m／execution=1m／display=1m或30m`；策略profile由immutable strategy.v1供應（strategy-0003=D／1H／5m） | 全程identity、聚合、cursor、chart及artifact；display唔准覆蓋策略語義；禁止散落hard-code | P1 |
| 交易時段 | 日市（spec §7 remark） | 模擬器運行窗口 | P1 |
| 偏離記錄開關 | on | live vs 回測對照 | P1 |
| 盲區容忍（行情失明分鐘數） | 5 分鐘 | ≤5分鐘且補回完整先auto continue；否則trip＋Owner手動resume；冇可信價禁止虛構平倉（D17） | P1 |
| 交易員最大回撤斷路器 | 8R | 由runtime equity high-water計；觸發即trip，有可信價先平倉，人手 re-arm（D17） | P1 |
| 最長連敗斷路器 | 8個completed losing trades | 同上；cancel／zero-fill唔計一單 | P1 |
| 交易所行事曆 | `config/calendar-<year>.yaml`（離線工具生成，可人手改） | 質量檢查預期分鐘、日終平倉時點、模擬盤排程、不交易日（D17） | P1 |
| 外部通知 | MVP無；唔係runtime readiness | Telegram永久移除；PWA Web Push＋Tailscale列`docs/POST_MVP_BACKLOG.md` | **Post-MVP** |
| 更多timeframe | 3m／5m／15m／1h／Owner自訂 | capability、calendar、aggregation及execution ambiguity逐項enable | **Post-MVP** |

## 7.5 盤前計劃／市場狀態輸入（MVP 明確不做；Post-MVP 待重新拍板）

> **Owner 2026-07-26 修訂：**MVP 不設 A3、獨立設定頁或每日市況輸入。策略喺市況唔適合時應自然唔觸發。以下只保留做歷史研究清單，**唔係 MVP 實作依據、唔可出現喺總覽或模擬盤啟動流程**。

| 參數 | 初值 | 消費邏輯 | 階段 |
|---|---|---|---|
| Owner 標註 level 清單（價位、來源 TF、支持/阻力、強度） | 未定 | MVP 不消費 | **Post-MVP 待重新拍板** |
| 當日方向睇法（long / short / 唔做） | 未定 | MVP 不消費 | **Post-MVP 待重新拍板** |
| 自動 S/R 偵測（swing 位、多 TF level 庫） | 未定 | MVP 不消費 | **Post-MVP 待重新拍板** |
| 超展開條件（連跌/升 N 日、累計 M×ATR） | 未定 | MVP 不消費 | **Post-MVP 待重新拍板** |
| 值博率門檻（潛在回報÷止損距離） | 未定 | MVP 不消費 | **Post-MVP 待重新拍板** |

歷史背景（Owner 2026-07-23）：殺倉只係 S/R 嘅最基本定義；重要 S/R 有多來源多層級。呢個研究方向仍保留，但 2026-07-26 嘅較新決定優先：MVP 完成後先重新研究輸入形式、參數同是否需要。

## 8. 記錄層 tag（無輸入；自動產出；顯示：P5 逐筆交易）

殺倉加分有無(P2)、signal 類型、regime 強弱(P2)、時段、兩層/三層、多重 inside、LMR 腿幅÷ATR(P2)、ATR 擴張比率、MFE/MAE — 全部 P1 起自動記錄（可記錄嘅部份）。

---

## 9. 開放問題（2026-07-23 Owner 已答 1–2）

1. ~~策略參數喺 UI 可唔可以改？~~ **已決定：MVP 唯讀**——改參數經 terminal AI 出新版文件。
2. ~~轉倉跳過窗口初值~~ **已決定：3 日**。
3. **回測成本初值**：手續費/滑點逐合約定初值——Agent C 稍後研究 NQ/YM/GC 實際成本後建議，實作前拍板。
4. ~~盤前計劃輸入形式：文件模式~~ **已由較新決定取代：MVP 不做；Post-MVP 重新研究**（Owner 2026-07-26）。
5. **超展開／值博率門檻初值**：Phase 2 前定。
