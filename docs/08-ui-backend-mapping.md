# UI ↔ 後端／數據 對應表

> **呢份文件係階段 C 嘅入口。** 六頁前端設計已經定稿（`docs/ui/designs/`），但**每頁嘅後端缺口都散喺各自嗰份稿**。本文把佢哋合埋，答一條問題：
>
> **「定稿嘅 UI 要嘅嘢，後端而家有幾多、爭幾多、爭嗰啲要點做？」**
>
> 建立日期：2026-07-26 · 最後同步：2026-07-29 · 作者：Agent C · 依據：`docs/ui/designs/` 六份定稿 ＋ **實際掃過嘅代碼**（唔係憑記憶）
>
> **2026-07-31 current status**：X／Y已退役，W係P6唯一full-stack owner但現時
> HOLD。舊A4未開始並已撤銷。完整runtime mapping以
> `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`為準；
> 本文任何舊Stage A／Telegram描述唔係permission。
>
> **文件分工**：現行 `docs/03` 同本文都由六份定稿倒推。`docs/03` 驗「畫面／互動有冇齊」，本文答「每個畫面由咩後端／資料供應」。兩份都唔可以反過來改設計。舊版以功能清單驅動前端嘅矩陣已刪除。

---

## 0. 點用呢份文件

| 你想知 | 睇邊節 |
|---|---|
| 成盤數大概點 | §1 一頁睇晒 |
| 後端而家實際有咩（唔係計劃，係真係跑緊嘅） | §2 |
| 某一頁要咩數據、邊度嚟 | §3 逐頁 |
| 階段 C 要做嘅嘢，按次序 | §4 缺口總表 |
| 要新增咩儲存／表 | §5 |
| 舊文件清理紀錄 | §6 |

**狀態標記**（全文一致）：

| 標記 | 意思 |
|---|---|
| ✅ **已有** | 後端真係有，前端照用得（我掃過代碼確認） |
| 🟡 **要擴** | 有底層數據，但冇對外接口／要加欄位 |
| 🔴 **全新** | 完全冇，要由零寫 |
| ⬜ **前端自足** | 唔需要後端，瀏覽器本地就得 |

---

## 1. 一頁睇晒

| 頁 | UI 狀態 | 後端狀態 | 主要爭咩 |
|---|---|---|---|
| **總覽頁** | ✅ 定稿（15 條） | 🟢 **接近齊** | 只爭一個匯總端點（可由現有 API 前端 join，但唔靚） |
| **策略工作台 ①草圖** | ✅ 定稿；#28–#35 instrument 補充已批准 | ✅ **backend instrument／package intake合約已齊** | Y `a6cbbe2`／`[167]`已PASS：normal persistence、package指令case及owner-review Library隔離均凍結；P2 catalog two-speed seam另批 |
| **策略工作台 ②量化確認** | ✅ 定稿；whole-universe 接受／退回 | ✅ **backend v1.4 universe validator／preview已齊** | instrument／whole-universe產品批已收貨；normal live preview仍待獨立工作令 |
| **策略工作台 ③版本庫** | ✅ 定稿 | ✅ **backend＋frontend seam均已收貨** | backend `e5dd179`／`[X-056]`＋frontend seam `7c7ed30`／`[177]`：真守衛／server derive／真刪除歸檔已通；live刪除守衛喺migration前誠實503 |
| **策略工作台 ④市場洞察** | ✅ 定稿；#36 whole-identity可恢復歸檔已批准 | ✅ **repository、archive backend及normal frontend integration已分批收貨** | 保持exact whole identity、bytes/SHA、no-overwrite restore；未有新工作令唔再擴scope |
| **數據頁** | ✅ 定稿（14 條）；現有頁面只呈現aggregate coverage | ✅ **P3-A facts＋two-speed backend已收貨** | backend已有complete/problem dates及longest clean segment；Owner手測確認UI仍缺可用日清單、symbol handoff及「帶去回測」動作，另批處理 |
| **回測頁** | ✅ 定稿修訂 2026-07-27（17 條） | ✅ **P4-A＋P4-B backend已齊** | X `8d1d0c1`＋`beb34b4`已由`[X-052]`正式PASS凍結；Y normal seam `794b716`已由`[175]` PASS收貨 |
| **結果頁** | ✅ 定稿修訂（18 條）；normal read/export/decision/history已接 | ✅ **出口、shared exact gate、CORS correction及eligible供應已收貨** | controlled integration已通；另有exact一次真Owner`use`→default immutable row→P6 eligibility證據，唔代表全頁TRUE E2E |
| **模擬盤頁** | 🟡 2026-07-31完整runtime書面規格及one-shot implementation plan已批准 | 🟠 foundations已有；W等Owner短指令啟動 | Stage A／ledger／review components及部分contract evidence已收；真IBKR Gateway、runtime、simulated execution、positions／PnL、safety、chart及`paper-review.v2`未交付 |

**一句總結**：策略→回測→結果→真Owner決定→P6資格呢段已有exact已驗證slice；而家兩個明確工程缺口係：①數據頁未把backend可用交易日清楚交畀回測頁；②模擬盤只開咗隔離式建立Stage A components，未有runtime／交易／偏離／paper-review／IB。

---

## 2. 後端現況（掃過代碼，最後核實 2026-07-27）

### 2.1 API 端點（FastAPI，讀寫兼有）

| 端點 | 做咩 | 邊頁用 |
|---|---|---|
| `GET /health` | 健康檢查 | — |
| `GET /api/v1/system/ib-status` | IB 狀態（**只做 TCP 探測，未做 API handshake**） | 總覽頁底部細字 |
| `GET /api/v1/data/coverage` | ✅ `b54a6fd`：default／`view=full`維持P3-A exact nested facts；`view=catalog`只回legacy configured-contract identity，避免P2支付full scan成本；其他view 422 | 數據頁（full）· 工作台 instrument catalog（catalog） |
| `GET /api/v1/data/quality-reports` ／ `/{report_id}` | 質量報告列表／詳情 | 數據頁 |
| `GET /api/v1/data/blacklist` ／ `POST` | 讀／寫 Owner 裁決（`trust` ／ `exclude`） | 數據頁 · 總覽頁 |
| `POST /api/v1/data/download` ／ `GET /download-jobs` | **只記錄下載意圖，唔會真下載** | 數據頁（設計已改成「複製指令」，呢個端點建議唔用） |
| `POST /api/v1/strategies/validate` | 四層驗證 | 工作台 ② |
| `POST /api/v1/strategies/import` | 匯入存為 draft | 工作台 ② |
| `GET /api/v1/strategies` ／ `/{id}` | 版本列表／詳情 | 工作台 ②③ · 回測頁 |
| `POST /api/v1/strategies/{id}/confirm` | 確認採用 | 工作台 ② |
| `POST /api/v1/sketches` · `GET /api/v1/sketches` · `GET /api/v1/sketches/{origin}/{id}` · `/images/{filename}` | composite sketch import/list/detail/image | 工作台 ①②③ |
| `POST /api/v1/backtests/preview` | dry-run preview；成功、request-level 422及引擎失敗均回完整 `backtest_preview.v1`、`persisted:false`、無run identity，writer零次 | 工作台 ② |
| `POST /api/v1/batches/precheck` | ✅ `8d1d0c1`／`[X-048]`：`backtest_precheck.v1` shared truth、95日warm-up、coverage、duplicate、assumptions、sanitized 503均正式收貨 | 回測頁 |
| `POST /api/v1/batches/submit` ／ `GET /batches/jobs` ／ `/{batch_id}` | ✅ `beb34b4`：strict standard submit、job-start重驗、`batch_job.v2`逐日進度、五狀態、完整error同operational 503由`[X-052]`正式收貨 | 回測頁 · 總覽頁 |
| `POST /api/v1/batches/jobs/{batch_id}/cancel-queued` | ✅ `beb34b4`：同鎖queued-only cancel、race／idempotency、atomic persistence、v1 read-only normalize由`[X-052]`正式收貨 | 回測頁 |
| `GET /api/v1/runs` ／ `/{id}` ／ `/{id}/trades` ／ `/{id}/events` | 結果、逐筆交易、事件 | 結果頁 |
| `GET /api/v1/batches` ／ `/{id}/runs` | 批次結果 | 結果頁 |
| `GET /api/v1/runs/{id}/chart` ／ `/narrative` | 圖表序列、敘事 | 結果頁 · 四格圖組件 |
| `GET /api/v1/runs/{id}/export` | ✅ `f3d13a5` immutable四成員ZIP＋shared strict/exact main gate；`[X-037]` C獨立兩出口驗收PASS | 結果頁 |
| `POST/GET /api/v1/runs/{id}/promotion-decisions` · `GET /api/v1/promotion-decisions` · `/eligible-strategies` | ✅ append-only Owner決定、列表／篩選、P6資格供應＋同一shared strict/exact main gate；`[X-037]` PASS | 結果頁 · 模擬盤入口 |

### 2.2 儲存（`data/`，全部本機檔案，冇外部服務）

| 位置 | 內容 | 格式 |
|---|---|---|
| `data/market/contract_id=…/` | canonical 1 分鐘 bar | Parquet 分區 |
| `data/market-daily/` | **原生 settlement 日線**（策略暖機靠佢） | Parquet |
| `data/quality-reports/` ／ `-daily/` | 四類質量檢查結果 | JSON |
| `data/strategies/strategy-*.json` | 策略版本（含 `status`／`content_sha256`／`based_on`／`based_on_sketch`＋`based_on_sketch_origin`／`based_on_insights`／參數列／`unquantified_notes`） | 每版本一個 JSON |
| `data/backtests/runs.sqlite3` | **回測結果（不可變）** | SQLite |
| `data/backtests/promotion-decisions.sqlite3` | Batch 4 lazy-created append-only decision store；目前真default檔未建立／未migration | SQLite |
| `data/backfills/` ／ `jobs/` ／ `shutdown-check/` | 回填、下載意圖、關機檢查 | JSON |

### 2.3 SQLite schema（`data/backtests/runs.sqlite3`）

```sql
runs(run_id PK, manifest_json, result_json, created_at, completed_at)
trades(run_id, ordinal, trade_id, entry_timestamp, exit_timestamp,
       net_pnl, record_json, PK(run_id, ordinal), UNIQUE(run_id, trade_id))
INDEX trades_by_run_exit ON trades(run_id, exit_timestamp)
TRIGGER runs_are_immutable      -- UPDATE 會 ABORT
TRIGGER runs_cannot_be_deleted  -- DELETE 會 ABORT
```

**🔴 呢個不可變設計係資產，唔准為咗方便而拆。** 「每個 run 事後查得返當時真相」係整個系統可信度嘅地基（D19 可審計 replay）。

**要留意**：`manifest_json` ／ `result_json` ／ `record_json` 係**大 JSON blob**。好處係加欄位唔使改 schema；代價係**查詢唔到**（例如「邊幾個 run 用過 2026-06-03 呢日數據」要逐個 run 解 JSON）。§4 有幾個缺口撞正呢一點。

---

## 3. 逐頁對應

### 3.1 總覽頁

| UI 元素 | 要嘅數據 | 來源 |
|---|---|---|
| 回測失敗咗 | 失敗狀態 ＋ 完整錯誤原文 | ✅ 隊列 ＋ `runs` |
| 數據日未裁決 | 有問題但未裁決嘅交易日 | 🟡 **可由 `quality-reports` × `blacklist` 前端 join 推導**，但建議加匯總端點 |
| 策略等你確認 | `status = draft` 嘅版本 | ✅ `GET /strategies?status=draft` |
| 回測跑完未睇 | 完成狀態 | ✅ `runs` ／ 隊列（「未睇過」⬜ 前端記） |
| 回測行緊 | 隊列狀態 | ✅ `/batches/jobs` |
| 草圖已匯出未攞返 | — | ⬜ 前端本地 |
| 草稿未填齊 | — | ⬜ 前端本地 |
| 底部 IB 狀態 | TCP 探測結果 | ✅ `/system/ib-status`（**措辭止於「port 可達 · 未驗證」**） |

**總覽頁係六頁入面後端最齊嗰頁**——因為佢只讀唔寫（約束 #2）。

### 3.2 策略工作台

| 分頁 | UI 元素 | 來源 |
|---|---|---|
| ① 草圖 | 四格上載／打字／草稿列表 | ⬜ 前端本地（階段 B 已做） |
| ① 草圖 | **匯出真圖文包落持久化 store** | ✅ X Batch 1 已完成並通過 REVIEW：`origin + sketch_id`；`data/sketches/<origin>/<sketch-id>/`；同名不同 origin 可共存 |
| ① 草圖 | **instrument catalog／asset class／lock metadata** | ✅ X `e00a565`：`/data/coverage` additive回人話名、class、currency、sessions；新sketch package強制exact instrument/class pair，舊package只讀 |
| ② 量化確認 | 兩種匯入 ＋ 四層驗證 | ✅ `/strategies/validate` |
| ② 量化確認 | 匯入存 draft | ✅ `/strategies/import` |
| ② 量化確認 | **左邊對照（Owner 原文四圖四段）** | ✅ backend origin-aware API 已齊；Y `acaf702`／`[144]` 已交 origin-aware read/parser |
| ② 量化確認 | **唯讀 strategy universe＋instrument gate** | ✅ X `e00a565`：同一catalog/parser強制v1.4 primary/class/contracts/exact rationale/session/USD/local-sketch gate，validation failure零寫入 |
| ② 量化確認 | 確認採用 | ✅ `/strategies/{id}/confirm` |
| ② 試跑預覽（漏斗 ＋ 系統解讀） | dry-run 唔存 artifact | ✅ X `e00a565`：success／request validation／engine failure全路徑完整envelope，四格D/1H/30m/5m，`persisted:false`、無run identity及零寫入；C已核實 |
| ③ 版本庫 | 列表／過目 | ✅ `GET /strategies` ／ `/{id}` |
| ③ 版本庫 | **改參數 → 衍生新版本** | ✅ **`e5dd179`／`[X-056]`已收貨**：additive derive、approved numeric leaf、direct parent、原版不變；C-v4八組mutation及full 674重跑核實 |
| ③ 版本庫 | **刪除 ＋ 歸檔** | ✅ **`e5dd179`／`[X-056]`已收貨**：standard-run守衛、fail-closed unknown、lossless archive、碰撞不覆蓋、ID不重用；未migration前live delete誠實503 |
| ③ 版本庫 | **「有 N 個 standard run 用緊」** | ✅ X Batch 2 `8b38732`／`[X-022]`：derived run index＋strategy mode；只計 standard run，unknown fail-closed。真 main migration仍未授權 |
| ④ 市場洞察 | 匯出 insight 圖文包 | 🔴 **全新**（同 ① 共用寫入器，`kind: insight`） |
| ④ 市場洞察 | 匯入 `insight.v1` ＋ 洞察庫 | ✅ **base repository、可恢復歸檔backend及normal frontend integration已分批收貨**：whole identity、exact bytes/SHA、archive list、no-overwrite restore及零永久清除保持凍結；未有新工作令唔擴scope |

### 3.3 數據頁

| UI 元素 | 來源 |
|---|---|
| 合約範圍、bar 數、質量摘要、不回測名單 | ✅ `/data/coverage` |
| 體檢記錄列表／詳情 | ✅ `/data/quality-reports` |
| 兩個裁決掣 | ✅ `POST /data/blacklist` |
| **交易日數 · 完整日 · 有問題日** | 🔴 **全新計算**（而家只有 bar 數同起訖時間） |
| **連續完整嘅最長一段** | 🔴 **全新計算** |
| **原生日線覆蓋日數** | 🟡 **要擴**：`data/market-daily/` 已落地，但 `/data/coverage` 冇講日線 |
| **改裁決時警告「有 N 個回測用過呢一日」** | ✅ X Batch 2 `8b38732`／`[X-022]`：trading-date mode＋實際 consumed-date side table；legacy日期證據不足時回 unknown candidate。真 main migration仍未授權 |
| 複製補數據指令 | ⬜ 前端組字串 |

### 3.4 回測頁

| UI 元素 | 來源 |
|---|---|
| 揀策略（只列已確認） | ✅ `GET /strategies?status=confirmed` |
| 揀合約、時間範圍、提交 | ✅ `POST /batches/submit` |
| **第四步：資金、所選合約手續費、滑點可調；策略風險與成交原則唯讀** | 🟡 frontend defaults已由`a6cbbe2`封；backend P4-A要令每個「策略 × 合約」各自用完整初始資金，submit時鎖死資金、成本同系統成交假設。策略風險只讀、唔由P4 override；唔准把多次回測共同攤分一筆資金（P4 #17） |
| 歷史列表 ＋ 失敗原因 | ✅ `/batches` ／ `runs` |
| 「再跑一次」 | ✅ 用同樣設定重新 submit |
| **檢查①數據覆蓋** | 🟡 **要擴**：`/data/coverage` 有數據，要加「呢段範圍夠唔夠」嘅判斷 |
| **檢查②暖機不足** | 🔴 **P4-A**：由confirmed strategy＋actual engine dependencies推算。現行Trend因EMA90 Daily regime＋5日slope exact要**95個run前已收市合資格原生日線日**；94 short／95 sufficient。不足只可回填更早資料或將start推後，唔准計run內future bars |
| **檢查③已跑過相同組合** | ✅ X Batch 2 `8b38732`／`[X-022]`：duplicate mode exact比對策略版本＋root symbol＋UTC range；只列 standard run，unknown fail-closed。真 main migration仍未授權 |
| 🔴 **實時進度（測到邊一日、幾筆交易、而家贏輸）** | 🔴 **引擎改動**：而家係「跑完才報完成」。要**每處理完一個交易日上報一次**（P4 約束 #11 已標紅） |
| 取消未開始嘅回測 | 🟡 隊列要支援「只殺 queued，唔殺 running」 |

### 3.5 結果頁

| UI 元素 | 來源 |
|---|---|
| 結果列表、摘要、逐筆交易、事件 | ✅ `/runs` ／ `/{id}` ／ `/trades` ／ `/events` |
| 圖表序列、敘事 | ✅ `/runs/{id}/chart` ／ `/narrative` |
| 記分卡（八維度含三子項） | ✅ `scorecard.py` |
| **匯出完整 `result.v1` 結果包俾 Terminal** | ✅ backend `f3d13a5`：只讀四成員ZIP、原bytes、canonical refs及shared strict/exact main gate；全份非標準JSON常數、raw identity空白、engine blank／trimmed均兩出口fail-closed，`[X-037]` PASS。normal前端接線另列工程seam（P5 #16） |
| **用得／打回／放棄 PromotionDecision** | ✅ backend `f3d13a5`：三決定、理由、snapshot、append-only store、eligible及同一shared exact gate；invalid source新DB零建立、既有row零append，`[X-037]` PASS。normal前端接線及P6消費另列工程seam（P5 #15／P6 #3b） |
| **逐筆決策答四條**（為何入市逐層列數值／止損擺邊點解／點樣離場邊個先觸發／有咩保守假設） | ✅ X Batch 3最終 `d5f1a1a`／`[X-028]`：structured evidence及new-complete main/sidecar integrity共用fail-closed gate已過C獨立corruption probes；唔再誤降級成legacy |
| **0 成交時完整 rejects＋deterministic 摘要** | ✅ X Batch 3最終完整保存timestamped rejects，summary/count漂移fail-closed；UI closest-three ranking只用已拍板deterministic contract，唔自行猜 |
| 機會漏斗（雙單位） | ✅ 已有 |

**呢兩項曾經係全項目最高價值嘅後端缺口，而家已由 Batch 3 收貨。** 理由：Owner 手上 **16 個回測全部 0 成交**，而呢兩項正正令「0 成交時仍然睇得出嘢」。後續唔准把佢哋重新列成未做，亦唔准為見到成交而放鬆策略閘。

### 3.6 模擬盤頁

Stage A／ledger／review foundations已有components及cross-layer evidence；完整runtime
仍未實作。現行權威係2026-07-31 P6 runtime設計；舊P6 HTML已退役。前後端閉環如下：

| UI／流程 | 後端／API 要提供 |
|---|---|
| 策略選擇器只列結果頁曾記錄「用得，去模擬盤」嘅版本 | PromotionDecision 查詢；回傳不可變策略版本識別，唔係只睇 `confirmed` |
| 策略揀完只列真正有可用baseline嘅合約；一個候選都要Owner親手揀 | Owner批准方案C：`GET /api/v1/paper/contracts`，係baseline candidate predicate嘅只讀投影；唔用全域coverage／run list推斷，規格`319911c` |
| 合約揀完先列相符 baseline；Owner 親手揀，唔自動取最近一次 | 按策略版本＋合約列 runs，附日期、交易日、筆數、R、初始資金及完整性狀態 |
| 每個交易員建立全新獨立帳戶，初始資金等於 baseline | 建立時複製 baseline RunManifest 初始資金；唔引用共享資金池 |
| 策略、合約、baseline、帳戶起點建立後鎖死 | PaperDeployment 保存四個不可變引用；修改要另建交易員 |
| Runtime preflight | exact identities、baseline、timeframes、IBKR Gateway read-only market data及truthful mode、calendar、store、single-writer、cursor及safety；notification唔係check |
| 休市仍可建立，成功後顯示等候 | readiness 同 market-session 狀態分開；休市唔當依賴錯誤 |
| 建立中禁止重複撳；結果不明先查同一次請求 | 建立命令必須可安全重試／去重，回傳同一交易員或明確未建立 |
| 成功後新增交易員分頁 | 建立結果包含交易員識別、鎖定摘要、帳戶及`provisioned`狀態；建立唔等於開始 |
| 明確開始／暫停／恢復／永久停止 | Versioned idempotent commands，帶request ID、expected lifecycle version及selection fingerprint |
| MVP 冇 A3／市場狀態輸入 | 建立 payload 同模擬器依賴不得包含 `premarket.v1` |
| app 自家模擬成交，只攞 IB 實時價格 | 絕不可向 IB 真實或 paper account 發出訂單 |
| 證據後、控制前匯出單一 trader 偏離 snapshot | 建立一致 cutoff／high-water marks；同一 request 冪等，新匯出先建立新 snapshot |
| 自包含 runtime `paper-review.v2` 偏離包 | market provenance＋decisions／orders／fills／trades／equity／events＋expected-actual＋鎖定完整baseline；舊zero-runtime v1保持可讀 |
| 運行中有持倉／零成交都可匯出 | 有持倉照實記 open position，唔平倉；零成交用合法空 trades 同未成交原因 |
| 缺檔、引用錯或雜湊錯 | fail-closed，回傳準確缺口；唔建立可下載部分 zip |
| 複製 Terminal 開場白 | 帶準確檔名／snapshot／trader；要求正確 lineage，新策略重新走完整流程，唔回寫舊 trader |

剩餘P6子系統：IBKR Gateway read-only market-data adapter、timeframe-agnostic bus／aggregator、
shared strategy＋conservative execution runtime、append-only journal、single-writer
lease、多trader帳戶／position／PnL、disconnect／restart recovery、8R／8-loss
safety、runtime chart、divergence及`paper-review.v2`。Telegram已移除；PWA Push、
Tailscale、cloud、auto-start及外置備份列post-MVP。

---

## 4. 缺口總表（按依賴排序）

### ✅ 第 1 組 · 解鎖 composite sketch 路徑（X Batch 1 已收貨）

| # | 缺口 | 點解排第一 |
|---|---|---|
| 1 | **圖文包真寫檔** | ✅ `f36da9e`：composite store、canonical schema、atomic 負測試已通過 |
| 2 | **sketch 讀取 API** | ✅ origin-aware detail／image URL 已通過；Y `acaf702`／`[144]` 已交 read client/parser |

### ✅ 第 2 組 · Run evidence／preview（X Batch 3最終 `d5f1a1a`／`[X-028]` 已收貨）

| # | 缺口 | 點解 |
|---|---|---|
| 3 | ✅ **結構化決策記錄**（逐筆因果鏈） | complete evidence及sidecar integrity共用fail-closed gate已通過corruption probes |
| 4 | ✅ **逐次 reject 記錄**（走到第幾層、被咩截住、差幾多） | 完整保存；0-record summary漂移、partial record及缺sidecar全部拒絕 |
| 5 | ✅ **dry-run 模式**（跑但唔寫 artifact） | success／request 422／engine failure全路徑完整 `backtest_preview.v1`，`persisted:false`、無run identity，writer零次 |

### 🟡 第 2b 組 · P2 primary instrument 合約（兩端產品批已收貨；normal live seam接線中）

| # | 缺口 | 合約 |
|---|---|---|
| 5a | Canonical contract catalog | `config/contracts.yaml` 加 display name/class/currency/sessions；backend/Nautilus/API 共用，唔硬寫第二份 mapping |
| 5b | `sketch.v1` instrument/class | 一包一 primary；catalog exact match；legacy bytes 不改 |
| 5c | `strategy.v1` v1.4 universe | primary/class/contracts/rationale/session；same-class/session/USD/exact-key/local-sketch gate；validation failure 零寫入 |
| 5d | `insight.v1` 語境 | instrument/class 合約已定；insight repository 仍屬之後獨立缺口 |

**2026-07-27實作狀態**：X backend contract已收貨。Y到 `ae0837c`嘅exact parser、Insight zero-write、origin race已收貨；`1442cf5`已把分頁① browser ZIP接入既有POST，並封四PNG／rationale、canonical filenames、exact-four、exact-201 late-A背景真相及background local-sync誠實alert。Y-v3再以`a6cbbe2`封owner-review Library隔離、P4 approved defaults及strategy指令case，C focused77、full251、typecheck/lint/build後由`[167]`正式PASS。preview、catalog shared request同insight persistence仍另批。

### ✅ 第 3 組 · 反查（X Batch 2 已收貨；真 main migration未授權）

| # | 缺口 |
|---|---|
| 6 | ✅ 「有 N 個 standard run 用緊呢個策略版本」 |
| 7 | ✅ 「有 N 個回測用過呢一個交易日」 |
| 8 | ✅ 「呢個策略 ＋ 合約 ＋ 時間範圍已經跑過」 |

**已採用原方案 (b)**：新增 regenerable derived `run_lookup`／`run_trading_dates`＋
read-only process-local catalog，唔郁 immutable `runs`／`trades`。Batch 2 最終
`8b38732` 以完整 proof publication checksum守住 derived publication；`[X-022]`
C獨立核實 59 focused／300 full、default 503零寫入後收貨。部署到真 main之前仍要
另行批准 explicit migration；未 migration時 API誠實 503，唔會掃 JSON blob或靜靜寫入。

### ✅ 第 4 組 · 數據頁「夠唔夠用」（`b54a6fd`／`[X-043]` backend已收貨）

| # | 缺口 |
|---|---|
| 9 | ✅ P3-A：交易日數 · 完整日 · 有問題日 · Owner overlay · **連續完整最長一段**；whole document對slow oracle exact |
| 10 | ✅ 原生日線喺同一minute range嘅覆蓋日數；`view=catalog|full`分流、catalog≤2秒、full≤15秒、single-scan已通 |

### 第 5 組 · 回測頁三個檢查 ＋ 實時進度（written spec已批准）

| # | 缺口 |
|---|---|
| 11 | ✅ **P4-A `8d1d0c1`** exact warm-up：confirmed strategy＋engine dependencies；現行Trend 95個run前合資格原生日線 |
| 12 | ✅ **P4-A `8d1d0c1`** composite precheck＋submit/job-start同一truth重驗；Owner trust/exclude真正控制replay；locked assumptions |
| 13 | ✅ **P4-B `beb34b4`** 引擎每個admitted交易日上報進度＋五狀態守恆＋queued-only cancel；Y normal consumer `794b716`已交、待C-v4審 |

### ✅ 第 6 組 · 結果頁 backend 閉環出口（`f3d13a5`／`[X-037]` 已收貨；normal frontend seam另批）

| # | 缺口 |
|---|---|
| 14 | **PromotionDecision**：用得／打回／放棄＋必填理由＋記分卡快照；只增不改 |
| 15 | **`result.v1` 完整結果包匯出**：既有不可變主檔＋sidecar，自包含、唔重算 |

### 第 7 組 · 模擬盤子系統

| # | 缺口 |
|---|---|
| 16 | 全部（見§3.6，含runtime `paper-review.v2` snapshot）——W one-shot P6 scope；唔分舊Stage工作令 |

---

## 5. 儲存／數據庫要新增咩

| 新增 | 內容 | 建議形式 | 依據 |
|---|---|---|---|
| `config/contracts.yaml` | root symbol → `display_name`／`asset_class`／`currency`／`sessions` | canonical controlled catalog；NQ/YM=`equity_index_futures`，GC=`commodity_futures` | P2 #28；`docs/05` §1.1c |
| `data/sketches/<origin>/<sketch-id>/` | PNG×4 ＋ `meta.yaml` ＋ `INSTRUCTIONS.md`；identity=`origin + sketch_id`；新包必有 instrument/class | 檔案夾；ZIP 內仍以 `<sketch-id>/` 為 root | `docs/05` §3.5；P2 #28–#30 |
| `data/insights/` | `insight.v1` 記錄 | 每個洞察一個檔；**版本不可變，改＝新版本**；唯一鍵 ＝ `origin` ＋ `insight_id` | `docs/05` §3.5.1 |
| `data/strategies/_deleted/<id>.yaml` | 刪除歸檔：YAML 原文 ＋ 刪除時戳 ＋ 當時狀態 | 檔案；**目標檔已存在唔准靜靜覆蓋** | p2 約束 #20 |
| **run 索引表** | ✅ 已實作：`run_id` → strategy／contract／root symbol／UTC range／實際交易日；完整 proof＋checksum | SQLite derived tables，**唔郁原表**；真 main migration仍待批准 | §4 第 3 組；`8b38732`／`[X-022]` |
| **決策記錄** | 逐筆：邊幾個條件、邊個數值、邊個先觸發 | 擴 `trades.record_json` **或** 新表（睇實作時查詢需要） | p5 約束 #13 |
| **reject 逐次記錄** | 每次評估：走到第幾層、被咩截住、差幾多 | run artifact 內新增（**數量可能好大，要諗保留策略**） | p5 約束 #14 |
| **PromotionDecision** | ✅ `f3d13a5` 已落lazy-created SQLite append-only store、immutable triggers及shared strict/exact result-main入口；`[X-037]` 最終PASS | 獨立SQLite；真default檔未migration／未建立 | p5 約束 #15；p6 #3b |
| 模擬盤全套 | deployments ／ 持倉 ／ 偏離記錄 ／ 韌性日誌 | 待模擬盤 WO | D17／D19 |
| Runtime `paper-review.v2` snapshots | market/runtime provenance、snapshot manifest／high-water marks／自包含zip／成員雜湊／request冪等狀態 | 不可變記錄＋artifact；ready後不可更新或刪除；舊v1兼容 | 2026-07-31 P6 runtime設計§13 |

**一個仍待裁決嘅風險＋一個已解決嘅架構選擇：**

1. ✅ **reject 逐次記錄保留策略已由Owner拍板。** C 於 2026-07-26 量度現有
   17 個 events files：合共 15,653 events／8,849 `signal_rejected`／4.55 MB；
   最大單 run 2,691 events／1,686 rejects／782,509 bytes。現有實證未到幾百
   MB。決定係：**完整保存全部逐次structured evidence，另做deterministic摘要；
   唔只存三筆、唔靜靜截斷。** 不同單位嘅「最接近三次」排名唔喺Batch 3
   自行發明，等真evidence分布出嚟再由Owner＋C裁決。書面設計：
   `docs/superpowers/specs/2026-07-26-run-evidence-and-preview-design.md`。
2. ✅ **`runs` 表加索引欄 vs 加衍生表已解決**：Batch 2 採衍生表＋proof，冇改 immutable原表；見 §4 第 3 組同 `[X-022]`。

---

## 6. 舊文件清理紀錄（最後同步 2026-07-27）

| 文件 | 判定 | 已執行 |
|---|---|---|
| 舊版 `docs/03` 前端組件矩陣 | 內容同定稿 UI 唔同，唔可再驅動實作 | ✅ 已刪除，只可由 git history 追查 |
| 現行 `docs/03-frontend-component-coverage-matrix.md` | 由六份權威稿倒推嘅 coverage／驗收工具 | ✅ 已重建；同本文及 `docs/09` 分工，唔取代設計稿 |
| `docs/02` 用戶旅程 S1–S9 | 主閉環仍然啱 | ✅ **已修**：S3／A3 排除於 MVP；S8 改成已批准嘅新增交易員流程 |
| `docs/00` D1–D21 核心決定 | 有效 | ✅ D20 primary instrument閉環；D21洞察whole-identity可恢復歸檔 |
| `docs/01` 參數溯源矩陣 | 參數 → 消費者對應仍然有效 | ✅ **已修**：移除「待審」狀態、對齊六頁名稱、baseline、獨立帳戶資金及 A3 Post-MVP 界線 |
| `docs/05` 檔案合約 | 策略／結果仍係權威；`premarket.v1` 只保留歷史草案 | ✅ **已標**：MVP 不產生、驗證或讀取 A3 |
| `docs/06` D19 並行設計 | 三層架構仍然有效 | ✅ 已對齊交易員分頁、baseline 同 MVP 冇 A3 |
| `docs/PROJECT_STATE.md` | 新 Agent 現況入口 | ✅ 已更新為階段 A 完成、六頁定稿及雙 Agent 狀態 |
| 舊 UI mock／手測指南 | `ui-design-reference-v2.html` 嘅頁面同 `manual-test-guide.html` 已被六份定稿取代 | ✅ 已從現行工作樹刪除；token實際值以 `apps/web/src/styles/tokens.css` 為準 |
| 已拍板嘅方案比較／流程補充稿 | 決定已合併入 p4／p5／p6 正式稿，繼續保留會造成雙重真相 | ✅ 已從現行工作樹刪除；正式實作只讀 `docs/ui/designs/` |
| P2 instrument v1 | per-instrument checkbox 會改寫 Terminal universe，與 Owner 批准 v2 衝突 | ✅ v1 已移除；v2 三態保留為 P2 #28–#35 明確引用嘅批准附件 |
| `docs/AGENT_C_HANDOVER.md` | v1→v2 舊 handover，早於雙渠道／X／docs 08/09 | ✅ 已移除；現役只用 `docs/AGENT_C_V5_HANDOVER.md` |
| `docs/04` 設計系統 | token 原則不變 | ✅ **不變** |

---

## 7. 一句話

**回測主線嘅後端已經好紮實**——數據管線、引擎、記分卡、不可變 artifact全部真係跑緊。現況係：

1. ✅ **composite sketch 圖文包 store／read**（第 1 組）已收貨；新 instrument/class 合約屬第 2b 組；
2. ✅ **structured evidence／preview**（第 2 組）及 instrument backend（第 2b 組）已收貨；
3. ✅ **反查索引**（第 3 組）已收貨；只餘真 main explicit migration要另行批准。
4. ✅ **P3交易日／原生日線coverage facts**（第 4 組）`b54a6fd`／`[X-043]` facts、two-speed、效能、zero-write已收貨；
5. ✅ **結果頁backend出口**（第 6 組）已由 `[X-037]` 最終PASS並凍結；normal frontend seam另批。

現況（2026-07-29）：真main migration＋Activation Gate、P2 normal insight integration、P5 controlled integration，以及exact一次真`use`→default immutable PromotionDecision→P6 eligibility slice均已按各自級別收貨。X-v5按`[X-097]`、Y-v5.5按`[205]`並行做P6隔離式建立Stage A；P3→P4可用日期handoff另待工作令。**IB、真P6 runtime同paper order仍另行授權，未開。**
