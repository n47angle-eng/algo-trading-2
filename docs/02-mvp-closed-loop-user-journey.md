# MVP 完整閉環 User Journey

日期：2026-07-24 | 最後同步：2026-07-27 | 作者：Agent C | 狀態：v1.1（Owner 已確認設計閉環）
原則：每步有輸入、輸出 artifact、完成 gate、下一步入口；冇「只有對話冇紀錄」、冇「有畫面冇儲存」。

> **⚠️ 2026-07-27 修訂註（頁面名同閉環現況）**
>
> **旅程同頁面設計已完整閉環**。本文件定義 user journey，唔再內嵌容易過時嘅每批實作進度；現行工程狀態只睇 `docs/PROJECT_STATE.md`、兩份最新 briefing 同渠道。
>
> | 本文寫 | 而家實際 |
> |---|---|
> | 「草圖工房」（獨立頁 P2.5） | **已取消**，併入 **策略工作台分頁 ①**（p2 約束 #1） |
> | 「P2 策略庫」 | **策略工作台分頁 ②③**（量化確認／版本庫） |
> | 「P6 部署列表」 | **模擬盤：交易員分頁**（總覽 ＋ 每個交易員一個分頁） |
> | S2 instrument 語境 | **每包一個 primary instrument**；P2 約束 #28–#35。Terminal universe 只可整份接受／退回，P4 先選本次 run 子集 |
> | S3 盤前計劃（A3） | ✅ **MVP 明確不做**；Post-MVP 才重新研究，唔再係閉環斷點（Owner 2026-07-26） |
>
> **第 3 節「鏈條連續性規則」仍然係硬要求。** 佢有冇被守住，逐個接口嘅審計喺 **`docs/09-journey-closed-loop-audit.md`**。
>
> **⚠️ 2026-07-31 修訂（頁面獨立）**：閉環仍然存在（artifact 溯源、可選 PromotionDecision），
> 但**唔再用頁面硬閘**。每頁獨立讀 DB／backend；冇資料＝soft empty。
> 模擬盤可直接揀已確認策略，唔強制先有「用得」決定。詳見 **`docs/10-page-independence-principle.md`**。
>
> **⚠️ 2026-07-31 修訂（頁面獨立）**：閉環仍然存在（artifact 溯源、可選 PromotionDecision），
> 但**唔再用頁面硬閘**。每頁獨立讀 DB／backend；冇資料＝soft empty。
> 模擬盤可直接揀已確認策略，唔強制先有「用得」決定。詳見 **`docs/10-page-independence-principle.md`**。

## 0. 閉環總覽

```
S1 數據準備 → S2 策略入庫 → S4 回測設定 → S5 執行
   → S6 結果檢視 ⟲ AI 迭代（返 S2 出新版本）
   → S7 晉升決定 → S8 新增模擬交易員 → S9 覆盤 ⟲（返 S2 或停）

[Post-MVP 候選：S3 盤前計劃；唔屬於目前 MVP 閉環]
```

## 1. Artifact 目錄（全部有唯一 id、可追溯）

| # | Artifact | 產生於 | 內容 | 可變性 |
|---|---|---|---|---|
| A1 | DataSnapshot（隱含） | S1 | canonical Parquet 覆蓋範圍 + 質量報告 + 黑名單 | 只增 |
| A2 | StrategyVersion | S2 | 策略文件全文 + 驗證結果 + Owner 確認時戳 | **不可變**；改＝新版本 |
| A2a | SketchBundle（sketch.v1） | S2a | 一個 Owner 選定嘅 primary instrument＋catalog asset class＋≥4 張 TF 圖 PNG＋meta.yaml（逐圖文字判斷）；strategy.yaml 以 composite sketch identity 引用 | **不可變**；改＝新包 |
| A3 | PreMarketPlan | S3 | **Post-MVP 候選；MVP 不產生、不讀取** | 待重新拍板 |
| A4 | RunManifest | S4 | 版本 id + 合約 + 範圍 + 資金 + 成本 + 成交模型 + 數據指紋 + optional batch_id | **不可變** |
| A4b | BatchManifest（batch.v1，D19） | S4 | batch_id + run_ids + 共用設定快照 | **不可變** |
| A5 | RunResult + TradeRecords | S5 | 指標 + 逐筆交易連 record tags + 事件日誌 | **不可變** |
| A6 | ResultFile (`result.v1` 包) | S6 | 結構化結果匯出（俾 terminal AI 讀） | 由既有不可變 A5 打包；**唔重算** |
| A7 | PromotionDecision | S7 | 晉升/打回/放棄 + 理由 + 記分卡快照 | 只增 |
| A8 | PaperDeployment + DivergenceLog | S8–S9 | 鎖定版本 + 合約 + baseline run + baseline 初始資金複製成獨立帳戶 + 風控 + 實時成交 + 偏離 | 交易員設定不可變；log 只增 |
| A9 | Runtime PaperReviewSnapshot (`paper-review.v2`) | S9 | 截至一致 cutoff 嘅行情來源、模擬記錄、偏離＋完整鎖定 `result.v1` baseline | **不可變**；明確再次匯出＝新 snapshot；舊v1只作zero-runtime compatibility |

## 2. 逐步定義

### S1 數據準備（P3 數據頁）
- **輸入**：合約、TF、日期範圍（矩陣 §3）
- **動作**：分段下載 → canonical 儲存 → 四類質量檢查 → 可疑日 Owner 裁決
- **輸出**：A1
- **Gate**：目標範圍覆蓋齊 + 無未裁決 error + 黑名單生成
- **下一步**：S4（或隨時補數據）

### S2 策略入庫（策略工作台分頁 ① → Terminal → 分頁 ②／③）〔2026-07-27 instrument 閉環修訂〕
- **S2a 草圖（策略工作台分頁 ①）**：Owner 先由 canonical catalog 揀一個 primary instrument；asset class/name/currency/session 唯讀帶入。未有圖可以改，首圖後鎖 instrument；Owner 上載 ≥4 張 TF 圖（D/1H/30m/5m）、逐圖寫判斷 → 匯出 **A2a `sketch.v1` 草圖包**（instrument＋asset_class＋chart-*.png ×4＋meta.yaml＋自包含 instructions）。匯出後整包不可變，改＝複製成新 sketch id。
- **S2b 參數化（Terminal）**：AI 讀草圖包 → 按 schema 寫 self-contained strategy.yaml；composite sketch lineage、`universe.primary_instrument` 同 `universe.asset_class` 不可改。可建議同 asset class instruments，但每個新增 member 要寫 `expansion_rationale`。
- **S2c 匯入確認（策略工作台分頁 ②；版本存入分頁 ③）**：匯入 → 驗證 composite lineage、primary/class/catalog、same-class、session、USD、rationale exact keys → **左邊原草圖、右邊完整 AI strategy＋dry-run 證據對照** → Owner 過目 `unquantified_notes` 同 universe 理由 → 接受或退回**整份**策略；App 唔設 per-instrument checkbox、唔改 YAML。確認前零寫入。
- **輸出**：A2（新 StrategyVersion，連 A2a 溯源鏈）
- **Gate**：驗證通過 + Owner 確認
- **下一步**：S4

### S3 盤前計劃（Post-MVP 候選；MVP 跳過）
- **Owner 2026-07-26 決定**：MVP 不設 A3、獨立設定頁或市場狀態輸入。
- **MVP 行為**：策略按鎖定條件自行判斷；市況唔適合就唔觸發。
- **Post-MVP**：完成 MVP 後先重新研究 level、方向、超展開、值博率及輸入形式；舊「文件模式」唔再係現階段實作依據。

### S4 回測設定（P4 回測頁）〔D19 升級：批量矩陣〕
- **輸入**：揀 **1..N 個 A2 版本 × 各版本已確認 universe 內嘅 1..M 合約**；實際可揀範圍 = strategy universe ∩ configured/available data。多選 chips＋選擇器預覽 N×M run，另加日期範圍、資金、成本、成交模型（矩陣 §5）
- **輸出**：**A4b BatchManifest**（batch_id＋run_ids）＋ N×M 個 A4 RunManifest（各含數據指紋；optional batch_id）
- **Gate**：每個合約必須屬策略已確認 universe；範圍喺 A1 覆蓋內；黑名單日逐 run 自動剔除。每個 run 各自完整 USD 100,000 起步；manifest 鎖 expiry-specific contract id
- **執行共用**：數據讀取＋MTF/指標預計算按合約共用一次（詳見 docs/06 §1 三層架構）
- **下一步**：S5

### S5 執行（引擎，無 UI 輸入）
- **動作**：引擎按 A4 行；§14 事件優先次序；record layer 全開
- **輸出**：A5
- **Gate**：run 完成無 fatal error；同 bar 衝突全部按保守規則記錄
- **下一步**：S6

### S6 結果檢視（P5 結果頁）〔D19 升級：兩層結構〕
- **動作**：batch 完成 → **對比總表**（每 run 一行：淨利R/勝率/PF/MaxDD/記分卡 chips）橫向比較 → 點行深挖單 run 詳情（指標/記分卡/多 TF 四圖＋逐圖文字/判斷鏈/逐筆）；**疊加模式**：剔 2–4 run 權益曲線同圖比較；匯出 A6 逐 run
- **Gate**：無（探索步）
- **下一步**：迭代 → S2；或滿意 → S7（**逐策略**晉升，一個 batch 可晉升 0..N 個）

### S7 晉升決定（P5）
- **輸入**：記分卡（8+擴充維度）+ Owner 判斷（含 rationale 自問）
- **輸出**：A7（**人手拍板**，系統只顯示警告——D12）
- **Gate**：決定被記錄（就算係「放棄」）
- **下一步**：晉升 → S8

### S8 新增模擬交易員（P6 模擬盤頁）
- **輸入**：只可揀已獲 A7 晉升嘅 A2 版本 + 合約 + Owner 親手選定 baseline `run_id` + typed timeframe selection；注碼／風控／交易時段跟鎖定策略；初始資金等於 baseline run
- **動作**：先建立全新獨立虛擬帳戶，再由另一個明確動作啟動runtime；runtime preflight檢查exact identities、baseline、timeframe、IBKR Gateway read-only market-data、行事曆、store、single-writer及cursor continuity。Telegram已移除，通知唔係readiness。休市可建立但唔扮運行
- **輸出**：A8
- **Gate**：所有runtime preflight checks明確通過 + Owner最後確認；版本、合約、baseline、timeframes、帳戶起點鎖定。請求結果不明先查同一次請求，唔直接重建
- **下一步**：S9

### S9 覆盤（P6 + P5）
- **動作**：DivergenceLog 對照 live vs 回測預期（成交價差、訊號一致性、滑點）；需要討論時，喺證據後、控制前匯出 A9。匯出唔改變交易員，四種狀態都可用；有持倉照實擷取、零成交合法、證據唔完整整包 fail-closed
- **輸出**：A9 runtime `paper-review.v2`（舊zero-runtime `paper-review.v1`保持可讀）＋ 決定——繼續行／停／返 S2 迭代
- **Gate**：行夠預定觀察期（Owner 自定）
- **迭代入口**：Terminal 讀 A9；如要修改，只輸出直接由鎖定策略衍生嘅新 A2，再返 S2 匯入、驗證、回測及建立新交易員

## 3. 鏈條連續性規則

每步所需欄位必須來自：前一 artifact／用戶輸入／系統推導——**唔准要求前面冇產生過嘅數據**；每個重要數字追溯到 version/run id；唔需要 Excel 或外部筆記先行到下一步。
