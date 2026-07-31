# 閉環審計：條鏈接唔接得上

> **同 `docs/08` 嘅分別**：`08` 答「每頁要咩後端」；**本文答「一站嘅產出，下一站認唔認得」**。
>
> Owner 2026-07-26 要求：「確保喺後端同前端，成個 user journey 係閉環，頁面之間嘅資料能夠完全一層層傳落去另一個頁面，唔好有斷點。」
>
> 建立日期：2026-07-26 · 最後同步：2026-07-31 · 作者：Agent C · 方法：**逐個接口對住實際代碼、contracts及現行設計驗**（唔係讀 `docs/02` 就當數）
>
> 判定標記：✅ **通** ／ 🟡 **設計已接、實作未做** ／ ⚠️ **弱**（接得上但有條件） ／ 🔴 **斷**（下一站攞唔到）
>
> **目前證據摘要**：P2 insight normal integration、P5 controlled integration及exact
> 「真Owner `use`→default immutable PromotionDecision→P6 eligible strategy」slice已通；
> P6 Stage A／ledger／review foundations已有components及部分contract evidence，但真IBKR Gateway、
> runtime、simulated execution、position／PnL及`paper-review.v2`未交付。Owner已批准
> 完整P6 one-shot設計，W仍HOLD等書面規格確認。P3→P4可用日期handoff仍係另一工程斷點。

---

## 1. 條鏈應該係點（識別碼視角）

```
草圖              (workshop, sketch-20260727-01) + primary NQ + equity_index_futures
  │  帶住 composite identity + instrument/class 出 app（圖文包）
  ▼
terminal AI       strategy.yaml : composite lineage + primary/class + universe + rationale
  │
  ▼
策略版本          strategy-0003 : composite sketch lineage＋locked universe＋content_sha256
  │  ＋ based_on = strategy-0002（改參數衍生鏈）
  ▼
回測              run manifest : strategy_binding{ id, content_sha256, overrides }
  │              ＋ universe 內 exact expiry 合約 ＋ 時間範圍 ＋ 數據指紋
  ▼
結果              run_id → RunResult ＋ TradeRecords（不可變）
  │
  ├─→ 返轉頭：結果 → 策略版本 → 草圖（Owner 對照「我講嘅」vs「AI 做嘅」）
  │
  ▼
拍板              晉升／打回／放棄 ＋ 理由 ＋ 記分卡快照
  │
  ▼
模擬盤            交易員 = 鎖死嘅 strategy-0003 ＋ 合約 ＋ 虛擬帳戶
  │              ＋ baseline run_id（用嚟對照）
  ▼
偏離              逐單 live vs 回測
  │
  └─→ paper-review.v2 → terminal 改良 → 衍生新策略版本 → 重新走策略工作台／回測／結果／新交易員
```

**閉環成唔成立，睇兩樣**：① 每個箭嘴嘅識別碼**帶得過去**；② **返轉頭嗰幾條**行得通。

---

## 2. 逐個接口

| # | 接口 | 靠咩接住 | 判定 |
|---|---|---|---|
| 1 | 草圖 → 圖文包 | `origin + sketch_id + instrument + asset_class` 寫入 `meta.yaml`；ZIP root 仍係 id | ✅ `1442cf5`／`[162]`已由C獨立REVIEW PASS：真PNG、canonical六員、exact-four、late-A truth、background local-sync failure誠實alert — 見 B8 |
| 2 | 圖文包 → terminal AI | `INSTRUCTIONS.md` 自包含 ＋ 開場白帶路徑 | ✅ 通（批 1 修正後 7 段 7,255 字，含 schema 精要） |
| 3 | **AI → strategy.yaml** | composite lineage＋`primary_instrument/asset_class/contracts/expansion_rationale/session` | 🟡 backend gate同Y whole-universe產品行為已通；分頁② normal live preview／端到端仍待另批 — 見 B1／B8 |
| 4 | strategy.yaml → 策略版本 | `import_document` 記 composite lineage／whole universe／`based_on`／`content_sha256` | 🟡 backend whole-universe validation/storage同frontend產品gate已通；完整normal端到端仍待逐seam驗收；legacy原檔仍不可開新run |
| 5 | 策略版本 → 衍生新版本 | `based_on` 欄 | ✅ backend `e5dd179`／`[X-056]`＋frontend seam `7c7ed30`／`[177]`均PASS：UI改數值→server衍生direct child、真守衛、真刪除歸檔。live刪除守衛喺真main migration前誠實503 fail-closed |
| 6 | 策略版本 → 回測 | confirmed universe ∩ configured/available data；manifest `strategy_binding{content_sha256, overrides}`＋exact contract | ⚠️ engine/admission/main migration及Activation Gate exact scope已通；但Owner手測發現數據頁未清楚列／傳可用日到回測頁，所以人手由P3揀日期→P4仍斷，需獨立工作令 |
| 7 | 回測 → 結果 | `run_id`，SQLite 不可變 ＋ trigger | ✅ 通 |
| 8 | **結果 → 返轉頭睇策略版本** | p5 約束 #8「用咗策略版本」連結 | ✅ P5 normal detail及exact identity navigation已收貨 |
| 9 | **策略版本 → 返轉頭睇草圖** | composite lineage → origin-aware sketch read API | ✅ backend 已齊；Y `acaf702`／`[144]` 已交 read/parser — 見 B2 |
| 10 | **結果 → terminal（迭代）** | 旅程 A6 `result.v1` 匯出 | ✅ normal single export＋真browser download受控integration已通；exact artifact bytes受保護 — 見 B3 |
| 11 | **結果 → 拍板** | 旅程 A7 PromotionDecision | ✅ normal frontend/backend controlled integration已通；另有獲批exact一次真`use`寫入default immutable store證據 — 見 B4 |
| 12 | **拍板 → 模擬盤建立入口** | 只列獲「用得，去模擬盤」嘅鎖定版本 | 🟠 真decision→eligible strategy exact slice已通；Stage A components已有，完整runtime journey等W one-shot |
| 13 | **模擬盤 → 對照回測** | Owner 親手揀並鎖定 baseline `run_id`＋timeframe identities | 🟠 create／ledger foundation已有；真runtime comparison未證 — 見 B5 |
| 14 | 模擬盤 → 偏離記錄 | Runtime ledger＋DivergenceLog | 🔴 未做；真decision／fill／position／PnL／divergence等W |
| 15 | **偏離 → 返 terminal 改良** | runtime `paper-review.v2` 自包含不可變 snapshot | 🟡 完整設計已批；現有v1只係zero-runtime historical profile — 見 B6 |
| 16 | 洞察 → 策略 | **刻意唔連**（p2 約束 #22） | ✅ **決定咗嘅開口，唔算斷點** |
| 17 | 盤前計劃 A3 | **MVP 明確不做；Post-MVP 重新研究** | ✅ Owner 決定，唔再係斷點 |

---

## 3. 接口缺口清單

### ✅ B1 · Composite sketch lineage 已落地並通過 REVIEW

**Owner 2026-07-26 拍板方案 A**：identity=`origin + sketch_id`。`docs/05` §1.1a–b v1.3 規定 `meta.based_on_sketch_origin + meta.based_on_sketch` 成對必填；完整 pair 但本機搵唔到草圖要照收，任一欄缺失／invalid 就打回。

**完成證據**：X Batch 1 correction `f29dcfa`＋ASCII 邊界 correction `f36da9e` 已由 `[X-010]` 通過 C 獨立 REVIEW。同一 canonical parser 供 package intake、filesystem lookup 同 strategy references 使用；完整 nonlocal pair 照收，任一缺失／invalid fail-closed；StrategyVersion additive 記 origin；legacy API contract、mutation、239 full tests、真 legacy hash／16 historical runs 均已核實。

**Legacy 裁決（2026-07-26）**：`strategy-0001`／`strategy-0002` 原檔同既有 run／result 保留可讀，但舊 `source_text` 缺呢欄，所以唔准開新 run。唔補假 sketch、唔改舊檔、唔開第二套寬鬆 validator；詳見 `docs/05` §1.1b／渠道 `[X-005]`。

### ✅ B2 · Composite 草圖讀取同前端接線已交

**證據**：X Batch 1 已有並通過 ZIP import、list、origin-aware detail、原 PNG read API、byte preserve、同 pair 409 no-overwrite、同 id 不同 origin 共存、canonical validator、ZIP／atomic 負測試。

**後果（兩個，都係真嘅）**：
1. **分頁 ② 左邊對照**：如果策略文件係喺另一部機／另一個 app 出，或者 Owner 清咗瀏覽器資料 → 左邊冇嘢。
2. **結果頁返轉頭**（p5 約束 #8）：Owner 見到一筆奇怪入市，撳「用咗策略版本」，設計話會見到**當時完整參數 ＋ 原始草圖**。**冇 sketch 讀取，「原始草圖」呢半邊永遠見唔到。**

**完成證據**：Y `1448de6` 後經 `e141b7c`、`acaf702` correction；`[144]` 報告 154/154、typecheck/lint/build 綠。Client 用 origin-aware URL，request/response identity exact match，charts/images 1:1 fail-closed，loading/404/5xx/invalid 分態，pending request 轉頁失效。新 instrument producer 不屬 B2，另見 B8。

### 🟠 B3 · `result.v1` 匯出已定稿 —— backend主體已有，normal live seam待接

**原本斷點**：六份定稿設計稿零次提及「匯出結果」／`result.v1`，但 `PROJECT_STATE` 明文寫住檔案方向係「`sketch.v1` 出、`strategy.v1` 入、`result.v1` 出」，旅程 S6 亦寫住「匯出 A6 逐 run」。

**後果**：閉環最重要嗰個迴圈——**S6 結果 → 返 terminal 傾 → S2 出新版本**——冇咗傳遞媒介。Owner 要靠自己複製貼上，而回測結果係全項目最複雜嘅資料（漏斗、逐筆、記分卡 11 項、最接近三次）。

**呢個係我設計時漏咗。** 我出結果頁設計稿嗰陣專注咗「Owner 睇得明」，冇諗「Owner 點樣把佢帶返 terminal」。

**2026-07-26 已拍板設計**：結果頁約束 #16 採方案 A——喺全部證據之後、三個決定掣之前放**獨立交接卡**；匯出同用得／打回／放棄互相獨立。每次只匯出當前一個 run，一個 zip 內含 `result.json`（`result.v1`）＋佢引用嘅 `trades`／`equity`／`events` sidecar；引用喺包內可解。只打包既有不可變 artifact，唔准重算。Dialog 提供下載、複製 Terminal 開場白、完整錯誤。

**而家判定**：設計層已接通；P5 Owner-review已能產生自包含真ZIP。X `f3d13a5`已交backend immutable ZIP及shared strict/exact main gate；`[X-037]` C重跑focused 182／full 523、22組兩出口payload probe及三組runtime mutation後最終PASS，所有已知scorecard、strict JSON、raw identity及engine exactness缺口已封。B3仍係🟠只因normal live frontend seam未接，唔係backend待修。

### 🟠 B4 · 晉升決定已定稿 —— backend主體已有，normal live seam待接

**原本斷點**：結果頁冇「用得／打回／放棄」，模擬盤亦無法判斷邊個策略版本有資格。D12 要求人手拍板、系統只顯示警告，而且連「放棄」都要保留原因。

**已拍板設計**：結果頁約束 #15 已採用證據後拍板：

- 三個決定：`用得，去模擬盤`／`打回，返 Terminal 改`／`放棄呢個方向`。
- 三個都要必填理由、保存記分卡快照、產生不可變 PromotionDecision。
- 新決定只增不改；系統只警告，唔代 Owner 判斷。
- 模擬盤約束 #3b 只列曾有「用得，去模擬盤」決定嘅不可變策略版本，一般 confirmed 不足以取得資格。

**而家判定**：UI同跨頁資格規則已接通；backend immutable PromotionDecision、eligible-strategies及shared strict/exact main gate已由`f3d13a5`／`[X-037]` 最終PASS，invalid source新DB零建立、既有row零append。B4保持🟠只因normal frontend seam同P5→P6消費未接；Batch 4只供應資格，唔建立P6 trader。

### ✅ B5 · baseline及timeframes已拍板（2026-07-26；2026-07-31修訂）

**原本證據**：舊P6稿嘅「同回測比」只寫同一段日子，冇講baseline係邊個run。

**後果**：「同回測比」係模擬盤存在嘅唯一理由（D18）。冇 baseline `run_id`，呢個表計唔出——或者更差，實作者隨便揀一個 run，Owner 睇到嘅「偏離」係假嘅。

**已定做法**：新增交易員嗰刻由Owner **親手揀一個 baseline `run_id`**，唔准系統
自動取最近一次；策略版本、合約、baseline、typed timeframes、獨立帳戶起點一齊
鎖死。初始資金等於baseline run，目前每個交易員各自完整USD 100,000起步。
現行正式依據：2026-07-31 P6 runtime設計 §§4–5；舊P6稿已從現行工作樹刪除。

### 🟡 B6 · runtime `paper-review.v2`已定稿 —— 偏離返Terminal待實作

**原本斷點**：模擬盤詳情有「同回測比＋系統解讀」，解讀最後會講「值得返 terminal 傾」，但冇任何 artifact 帶得走。

**保留設計**：喺「同回測比＋系統解讀」之後、「安全網＋交易控制」之前放
獨立交接卡；總覽唔放匯出掣。running、paused、tripped、permanently-stopped
都可以匯出，匯出唔改變trader。

每次明確匯出建立一個截至一致cutoff嘅不可變`paper-review.v2` snapshot；zip包含
market provenance、decisions、simulated orders／fills／trades、equity、positions、
runtime／safety events、expected-vs-actual偏離及鎖定完整baseline。零成交合法；
有持倉照實記open position；缺檔、引用錯或hash錯整包fail closed。舊
`paper-review.v1`只保留zero-runtime historical profile。

**正式依據**：
`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md` §13。

**而家判定**：設計層已接通；現有v1 producer／consumer foundation唔等於runtime
v2，W仍要實作及證明，所以保持🟡。

### ✅ B7 · 已由 Owner 決定排除於 MVP（2026-07-26）

MVP 不設 A3、獨立設定頁或市場狀態輸入；策略喺市況唔適合時應自然唔觸發。盤前 level、方向及相關參數留待 MVP 完成後重新研究，所以六頁冇 A3 入口係**刻意範圍界線，唔係斷點**。

### 🟡 B8 · Primary instrument 身份鏈已定稿；草圖出口已通，catalog／下游seam續接（2026-07-27）

**Owner 拍板**：MVP 每個草圖包一個 primary instrument。Owner 揀 root symbol；asset class/name/currency/session 由 canonical catalog 帶入。首圖後 instrument 鎖定，匯出後整包不可變。

Terminal 回傳嘅 `strategy.v1` 自包含 primary/class/contracts/rationale/session。P2 唔准用 checkbox 改 universe，只可接受或退回整份文件；P4 先由已確認 universe ∩ configured/available data 揀本次 run subset，manifest 再鎖 exact expiry contract。Insight 亦保留 instrument/class，但仍純記錄。

**閉環檢查**：

- Owner 市場意圖 → sketch artifact：有欄、有 catalog、有不可變 gate；
- sketch → Terminal → strategy：lineage 同 primary/class 都鎖定；
- strategy → P4：完整 universe 不變，本次 run subset 另層處理；
- P4 → P5／P6：exact contract 住 run/baseline snapshot，唔由當前 catalog 重猜；
- P5／paper export → Terminal：帶回當時 strategy/run identity，可出直接 child 再走 P2。

**MVP 誠實邊界**：同 asset class 唔等於已證明成功；唔做 OCR、跨 class strategy、FX conversion 或 insight runtime linkage。舊未匯出 draft 可由 Owner 補揀；舊已匯出 package／strategy bytes 不改。

**工程證據更新**：X backend B8已收貨。Y instrument／whole-universe及Normal Live Seam A已PASS；Y-v3 `a6cbbe2`／`[167]`再封owner-review隔離、P4 approved defaults及package instruction case，C focused77、full251、typecheck/lint/build全通。呢批唔冒充B8 catalog seam。

**判定**：頁面、schema、legacy同跨頁資料設計完整；草圖→repo出口工程已通，X `b54a6fd` fast catalog同Y `a6cbbe2` truth correction均已收貨。P2 `view=catalog`＋shared request、preview及P2→P4仍另批，所以B8整體保持🟡。

---

## 4. 修補次序（我建議）

| 序 | 修 | 點解排呢個位 |
|---|---|---|
| 1 | ✅ **B1** composite sketch lineage 驗證 | X Batch 1 已完成並收貨 |
| 2 | ✅ **B2** 圖文包寫檔 ＋ origin-aware 讀取 | X backend 同 Y frontend 已交 |
| 3 | 🟡 **B8** primary instrument identity chain | instrument／whole-universe、normal草圖持久化及Y-v3 truth correction到`a6cbbe2`已PASS；catalog／preview／P2→P4仍各自等獨立工作令 |
| 4 | 🟠 **B4** 晉升決定 | backend＋shared exact gate已由 `[X-037]` PASS並凍結；待normal frontend seam |
| 5 | 🟠 **B3** 結果匯出 `result.v1` live seam | backend＋shared exact gate已PASS並凍結；待normal frontend seam |
| 6 | **B5** baseline run 鎖定 | 設計已定；同模擬盤一齊實作，交易員記錄由第一日就要有呢一欄 |
| 7 | **B6** runtime `paper-review.v2` 偏離交接 | 同完整P6 runtime一齊做；舊v1 zero profile保持可讀。**B7 已由 Owner 排除於 MVP** |

**B1、B8 同 B5 有個共通點：資料建立嗰刻唔記低，將來補唔返。**
- B1：今日寫低嘅策略文件冇完整 `origin + sketch_id`，三個月後你查唔返佢由邊個 app 嘅邊張草圖嚟。
- B8：草圖／策略冇 instrument/class/universe rationale，之後只剩圖同規則，查唔返係 NQ 定 GC、點解擴展 YM。
- B5：部署咗先至諗 baseline，嗰段時間嘅偏離數據就冇對照。

**兩項設計都已拍板；實作仍要優先，因為資料一旦冇記低就補唔返。**

---

## 5. 已經通咗嘅嘢（唔使擔心）

- **草圖 → 圖文包 → terminal**：批 1 已驗證，zip 內容同畫面字串全等，指令書自包含
- **策略版本 → 回測 → 結果**：`content_sha256` ＋ overrides 逐欄記錄；`runs` 表有不可變 trigger（UPDATE／DELETE 都 ABORT）
- **改參數衍生鏈**：`based_on` 欄已存在，**記錄格式已支援**，爭端點
- **洞察唔連策略**：呢個係**決定**（p2 約束 #22／#23），唔係遺漏。鐵律係「策略文件永遠自包含，執行層永不運行時查洞察庫」

---

## 6. 一句話

**條鏈嘅中段（策略 → 回測 → 結果）好紮實**，因為佢由第一日就用不可變 artifact ＋ 內容雜湊做接口。

**設計層冇斷點；工程層尚未全部接通**：

- **前面**：策略工作台正常路徑同insight integration已有證據；但數據頁未把完整／有問題日期變成Owner可理解、可選並帶去回測嘅操作，P3→P4仍斷。
- **中後段**：回測→結果、single export、PromotionDecision/history及exact真`use`→P6 eligibility slice已通；證據只覆蓋列明slice，唔可擴張成全站TRUE E2E。
- **最後**：P6 foundations已有components／部分contract evidence；完整runtime設計已獲批但W未開工。真IBKR Gateway、runtime、模擬成交、position／PnL、偏離及`paper-review.v2`仍未交付。

因此可確認：**六頁UX、artifact身份同返程設計已形成完整設計閉環；工程閉環未完成，現時至少有P3→P4手動選日交接及P6建立後runtime→paper-review兩大缺口。**
