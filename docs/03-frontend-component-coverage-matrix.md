# 前端畫面覆蓋／驗收矩陣

> 建立日期：2026-07-26
>
> 最後同步：2026-07-29（X-v5按`[X-097]`、Y-v5.5按`[205]`並行做P6隔離式建立Stage A components；兩邊檔案分離，X擁有heavy full-test lane。本文係驗收參考，唔會授權Stage B／runtime／IB。）
>
> 狀態：**有效驗收工具**
>
> 唯一輸入：[`docs/ui/designs/`](ui/designs/) 六份權威設計稿
>
> 配套文件：[`docs/08-ui-backend-mapping.md`](08-ui-backend-mapping.md)（後端供應）· [`docs/09-journey-closed-loop-audit.md`](09-journey-closed-loop-audit.md)（跨頁閉環）

## 0. 呢份矩陣嘅權限邊界

呢份矩陣係由六份已定稿設計**倒推**出嚟，目的只有兩個：

1. 出工作令時，防止漏畫面、漏狀態、漏資料交接；
2. 審交付時，逐行核對畫面、互動、資料、錯誤處理同閉環。

**佢唔係第七份設計稿，亦唔係產品需求來源。**

- 權威次序永遠係：**該頁設計稿最後「實作約束」表 → 同一份稿嘅畫面 → 本矩陣**。
- 如果本矩陣同設計稿衝突，**錯嘅係本矩陣**；修矩陣，唔准反過來改設計稿。
- 本文嘅「畫面單元」係 Owner 睇得到或操作得到嘅驗收單位，**唔規定 React component 點拆**。唯一由設計明文要求共用嘅係四格圖表。
- 矩陣唔可以自行新增頁面、流程、欄位、掣、狀態或後端能力。發現設計缺口，要交 Owner 拍板。
- 每張前端工作令必須寫明：**設計稿檔名＋約束編號＋本矩陣行號**；收貨時亦逐行回填證據。
- `docs/08` 決定某一行嘅資料由邊度供應；`docs/09` 驗跨頁產出下一站認唔認得。本矩陣唔取代兩者。

### 驗收狀態

| 標記 | 意思 |
|---|---|
| ⬜ 未驗收 | 未按現有定稿逐行實證 |
| 🟡 部分／待修 | 有交付，但仍有未通過項或只用 fixture |
| ✅ 通過 | Agent C 已按設計、狀態及資料流實證 |
| ⛔ 不適用 | 設計明文排除，唔係遺漏 |

**「有畫面」唔等於通過。** 每行最少要驗正常、loading、empty、error、disabled／guard（適用者）、資料來源、動作結果同跨頁落點。

---

## 1. 全 App 共通畫面單元

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| G-01 | App shell＋六個主入口 | 六稿畫面；P2 #1 | 當前 route | 導航到總覽／策略工作台／數據／回測／結果／模擬盤 | 唔准再出現獨立「工房」或 MVP 設定／盤前計劃頁 |
| G-02 | 當前頁／分頁定位 | 六稿畫面 | route＋sub-route | 保留正確主頁、分頁、選中項 | deep link／refresh 後仍落正確位置；唔靠記憶猜預設 |
| G-03 | 跨頁帶入提示 | P1 #4；P3 #12；P4 #15；P5 #8；P6 #3b | 來源頁、entity ID、filter | 顯示由邊度嚟；可清除／返回全部 | 要直達正確分頁、項目、展開位或篩選；唔准只去頁頂 |
| G-04 | Loading／查詢中狀態 | 六稿實作約束嘅誠實狀態原則 | request state | 無寫入 | loading 期間 destructive action 必須 fail-closed；唔准當「冇資料」 |
| G-05 | Empty state | P1 #9；P5 #3/#9；P6 #3i/#15 | 真實空資料 | 只提供設計准許嘅下一步 | 唔准用假數字、灰色圖表或 demo 交易填滿 |
| G-06 | 錯誤＋純文字複製 | P2 #7；P3 #13；P4 #14/#16；P5 #12/#16；P6 #17 | 完整原錯／診斷 | 複製無 HTML、行號、UI 前綴嘅文字 | 摘要唔可取代完整錯誤；technical code 要翻成人話（除非設計要求原文） |
| G-07 | 時間三分類 | P1 #12；P2 #25；P3 #11；P5 #11；P6 #14 | 時刻／交易日／範圍 | 本地顯示＋必要 UTC 對照 | 時刻標時區；交易日永不轉時區；範圍 picker 同時顯示 UTC |
| G-08 | 技術詞翻譯 | P1 #11；P3 #13；P4 #16；P5 #12；P6 #2/#14 | 後端 ID／code／metric | 顯示 Owner 用語 | 禁用 `batch`、`runs`、`p50 閘`、`PF`、`MaxDD` 等稿內禁詞 |
| G-09 | 主題＋可觸控＋不遮內容 | P2 #24；`docs/04` | 三個 theme、三種頁寬 | 切換主題 | switcher 唔 overlay 內容；三主題、三頁寬、44px 觸控逐一驗 |
| G-10 | 數字誠實性 | P1 #13；P4 #8；P5 #2；P6 #7/#9 | API／本地真實 count | 顯示精確 count、單位、幣別 | 唔准約數、錯誤封頂、混單位、把多策略數值合併成假單值 |
| G-11 | Dialog（需要複製／下載／確認時） | P2 #7/#26；P5 #16；P6 #3g/#17 | 當前 artifact／鎖定內容 | 下載、複製、確認、關閉 | 唔用 toast 取代；成功前唔顯示成功動作；失敗時 dialog 留住 |
| G-12 | 四格圖表共用組件 | P2 #2；P5 #4–#6c 及稿尾並集；P6 #5 | 四 TF bars、指標、交易、事件、live updates | 同步 crosshair、外部跳時刻、append／update bar | 2×2 同時顯示；唔准單圖＋TF tabs；P2／P5／P6 用同一組件 |

---

## 2. P1 總覽頁

權威稿：[`p1-overview.html`](ui/designs/p1-overview.html)。本頁係**只讀行動中樞**，冇自己嘅業務資料，排最後實作。

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P1-01 | 頁頭「等緊你嘅事」＋精確件數 | 畫面 A/B；#1/#13 | 七類事項聚合 | 無寫入 | 只答「而家要做咩」；唔加成功率、覆蓋、最近活動 |
| P1-02 | 分類、排序、合併器 | 七種事項表；#5/#6/#14 | 類別＋時間＋數量 | 產生清單次序 | 阻住→等判斷→行緊→提醒；同類舊先；失敗最多逐列 3 個，其餘合併 |
| P1-03 | 「回測失敗」行 | 七種事項 #1；#3/#4/#14 | 失敗狀態＋錯誤原文 | 去 P4 對應行，錯誤已展開 | 撳「看原因」後消失；每個失敗獨立列 |
| P1-04 | 「數據日未裁決」行 | 七種事項 #2；#3/#4/#14 | 有問題且未裁決交易日 | 去 P3 已篩選裁決卡 | 全部裁決先消失 |
| P1-05 | 「策略等你確認」行 | 七種事項 #3；#3/#4/#14 | validated、未確認策略 | 去 P2 量化確認，帶住該份 | 確認或放棄後消失 |
| P1-06 | 「回測跑完未睇」行 | 七種事項 #4；#8/#14 | completed run＋前端逐個 read state | 去 P5 已篩選未睇結果 | 打開該詳情先逐個消失；冇「全部標記已睇」 |
| P1-07 | 「回測行緊」行 | 七種事項 #5；#14 | queued／running jobs | 去 P4 正在跑區 | 全部完成後轉為 P1-06；後端冇進度時唔扮有日期 |
| P1-08 | 「草圖已匯出未攞返」行 | 七種事項 #6；#7/#14 | P2 擁有嘅匯出記錄＋是否有相應策略 | 去 P2 量化確認，帶對應草圖 | 文案只講「等緊你由 terminal 攞返」；唔聲稱 AI 已完成 |
| P1-09 | 「草稿未填齊」行 | 七種事項 #7；#14 | P2 草稿＋缺失欄位 | 去 P2 草圖，捲到第一個未填格 | 四格齊或草稿刪除後消失 |
| P1-10 | 每行單一直接行動 | #2–#4 | row context | 只導航 | 每行必有掣；總覽零業務寫入；導航必須帶 context／filter |
| P1-11 | 真空狀態 | 畫面 B；#9 | 0 件事項 | 「開始一個新草圖」→ P2 草圖 | 一句＋一掣；唔加假資訊 |
| P1-12 | 底部 IB 探測細字 | #10/#12 | TCP reachability＋checked_at | 無 | 只寫「port 可達 · 未驗證 session」；標時區；唔做大卡、唔寫已連接 |
| P1-13 | 本頁開工守衛 | #15 | 其他五頁供應能力 | 無 | 未有指定工作令唔開工；最後做 |

---

## 3. P2 策略工作台

權威稿：[`p2-strategy-workbench.html`](ui/designs/p2-strategy-workbench.html)。四個分頁係同一頁，唔准拆返獨立工房。

### 3.1 頁框架

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P2-01 | 四分頁導航 | 畫面流程；#1 | 草圖／策略／版本／洞察狀態 | 切換草圖、量化確認、版本庫、市場洞察 | 保留當前上下文；側欄冇「工房」 |
| P2-02 | 分頁 deep link＋帶入 entity | P1 七種事項；P5 #8 | sketch_id／strategy_id／tab | 定位項目、展開位 | 由總覽／結果入嚟唔使再搵 |

### 3.2 草圖

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P2-S01 | 草稿列表＋目前選中＋新草圖 | 畫面分頁①；#27 | 本地草稿／已匯出記錄 | 揀返、繼續填、新建 | 顯示編號、草稿／已匯出、差咩；新建後舊草稿仍可返回 |
| P2-S02 | 草圖標題 | 畫面分頁①；#5 | Owner 文字 | 存入 sketch/meta | 未填可存草稿，但匯出 disabled；唔准用草圖編號 fallback |
| P2-S03 | 四張 screenshot 上載 | 畫面分頁①；#2/#3 | 本地圖片／拖放 | 生成預覽及圖文包內容 | 四格同時；每格圖、時間框架、角色相連 |
| P2-S04 | 每格時間框架＋角色 | 畫面分頁① | Owner 選擇 | 寫入 sketch/meta | 可改，唔鎖死 D/1H/30m/5m；四格並列 |
| P2-S05 | 指標 chips | 畫面分頁①；#6 | Owner 勾選 | `indicators_shown` | 必填；明確冇剔寫 `[]`，唔等於欄位缺席 |
| P2-S06 | 四格判斷文字＋整體理據 | 畫面分頁①；#5 | Owner 原文 | 存草稿／寫入包 | 四格文字未齊逐項指出；rationale 保留原文 |
| P2-S07 | 「存草稿」 | #5/#27 | 任何完成度 | 本地保存 | **零前置條件**；匯出 disabled 唔可連帶鎖儲存 |
| P2-S08 | 匯出資格提示＋disabled | #5/#6 | 標題、四格文字、指標 | 解鎖匯出 | 要逐項講「標題未填」「30m、5m 未填」；唔准籠統錯誤 |
| P2-S09 | 圖文包生成 | #4/#8/#26 | 畫面同一份六檔內容 | repo 相對包／瀏覽器下載包 | 四圖＋`meta.yaml`＋自包含 `INSTRUCTIONS.md`；指令書不可引用 repo 文件；兩種出口內容完全相同；瀏覽器下載純前端，唔准為下載寫後端 |
| P2-S10 | 匯出 Dialog | #7/#26 | 包路徑、準確檔名 | 下載、複製 terminal 開場白、開資料夾、去分頁② | 明講下載到邊及要搬去邊；唔用 toast |
| P2-S11 | 「唔跟進」／刪草稿擁有權 | P1 七種事項 #6/#7；P1 #2 | P2 本地草圖狀態 | 更新／刪除 P2 擁有嘅草圖 | 呢個寫入只可喺 P2，唔可放總覽 |
| P2-S12 | Primary instrument＋catalog 摘要 | #28/#30 | `/api/v1/data/coverage` configured catalog rows | Owner 選 root symbol；顯示唯讀 name/class/currency/session | frontend 唔硬寫 mapping；catalog loading/5xx/invalid fail-closed；唔由標題／圖片推斷 |
| P2-S13 | Instrument 鎖定＋legacy 狀態 | #29/#30 | draft 是否已有圖／是否匯出／新欄完整度 | 首圖後鎖 instrument；匯出後 duplicate | 舊未匯出 draft 要補揀先可匯出；舊已匯出 package readonly；唔改 legacy bytes |

### 3.3 量化確認

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P2-Q01 | 對應草圖 selector／lineage 提示 | 畫面分頁②；#9 | `based_on_sketch_origin`＋`based_on_sketch`＋本地草圖 | 揀／顯示 lineage | 任一 composite 欄缺席／invalid＝引用層問題；完整 pair 但本機搵唔到＝本機缺失；後者仍可驗證／確認 |
| P2-Q02 | 兩種策略匯入 | 畫面分頁② | YAML 檔／貼文字／repo path | 送驗證 | 三種入口產生同一文件內容；唔靜靜改寫 |
| P2-Q03 | 四層驗證結果 | 畫面分頁② | 格式／引用／語義／溯源結果 | 顯示逐層結果 | 前層失敗要講後層未跑；可複製全部純文字錯誤 |
| P2-Q04 | 左邊「我想講嘅」 | #9 | 分頁①保存嘅四圖、四段、rationale | 唯讀對照 | 唔由 AI YAML 重建，唔要求 Owner 重打 |
| P2-Q05 | 右邊通用 YAML 參數樹 | #10 | 匯入 YAML | 唯讀樹 | 唔硬寫 schema 欄位；新增欄位唔可隱形 |
| P2-Q06 | `unquantified_notes`、rationale、provenance、原文 | #11 | 匯入文件 | 唯讀／可摺（除 notes） | notes 永不摺；0 項明寫「0 項」 |
| P2-Q07 | 試跑預覽觸發＋範圍 | #12 | validated strategy＋日期範圍 | dry-run | 唔自動跑；唔存 artifact、唔佔 run 編號、唔入結果頁 |
| P2-Q08 | 預覽漏斗＋系統解讀 | #13/#14 | dry-run funnel／rejects | 顯示解讀 | 日級同 5m 評估級分開比例尺、標單位；0 成交都要講原因 |
| P2-Q09 | 預覽四格圖 | #2；稿尾四格並集 | chart series／rejects | inspect | 用 G-12；0 成交仍顯示過閘／reject 位置 |
| P2-Q10 | 確認採用／返 terminal | #15 | validated strategy | confirm 或離開改良 | 撳確認前 import/confirm **零寫入**；要有 write-count 測試 |
| P2-Q11 | 唯讀 strategy universe 對照 | #31/#32 | primary/class/contracts/rationale/session | 顯示原草圖 vs Terminal 完整聲明；整份接受／退回 | 冇 member checkbox、冇 `approved_instruments`、唔改原文；每個擴展理由可見 |
| P2-Q12 | Instrument/universe fail-closed gate | #33/#34 | strategy＋catalog＋optional local sketch | 顯示逐欄驗證及人話修法 | primary membership、same class、session、USD、exact rationale keys；本機缺 sketch 只降級對照，唔繞過自包含驗證；失敗零寫入 |

### 3.4 版本庫

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P2-L01 | 版本列表＋溯源 | 畫面分頁③ | strategy versions | 過目／刪除 | 顯示版本、名、來源；列表冇直接「改參數」 |
| P2-L02 | 過目詳情 | #16 | 版本完整內容 | 展開／收起 | notes 永不摺；參數、provenance、原始 YAML 可追查 |
| P2-L03 | 編輯閘 | #16/#17 | 過目中版本 | 先撳「編輯參數」先可改 | 只准數值；structures／入市序列唯讀 |
| P2-L04 | 儲存為衍生新版本 | #18 | 原版本＋數值差異 | 新 strategy version | 原版本不變；`based_on` 必指**直接 parent**；來源「Owner UI 微調」 |
| P2-L05 | 刪除引用守衛 | #19 | standard-run reference lookup | enable／disable delete | validation run 唔計；引用狀態 loading／error／unknown 一律 fail-closed |
| P2-L06 | 延遲刪除＋復原 | #20/#21 | 可刪版本 | 約 5 秒後歸檔；期間 undo | 離頁／refresh＝確認；真正寫入延遲；歸檔保留 YAML 原文、時戳、狀態；唔覆蓋同名 |

### 3.5 市場洞察

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P2-I01 | 洞察四圖＋文字編輯器 | 畫面分頁④ | screenshots、逐圖判斷、rationale | 存／匯出 `kind: insight` | 互動模式同草圖一致，但 artifact 種類不同 |
| P2-I02 | `insight.v1` 匯入 | 畫面分頁④ | AI 文件 | 驗證、寫入洞察庫 | 版本、origin、ID 可追溯；錯誤完整可複製 |
| P2-I03 | 洞察庫 | #22 | 洞察版本、tag、手動狀態 | 過目、改狀態、歸檔 | 純記錄；狀態唔自動變；MVP冇永久刪除 |
| P2-I04 | 刻意冇連結策略 | #22/#23 | 無 | 無 | 冇「用洞察跑回測」、冇策略連結；執行層永不 runtime 查洞察庫 |
| P2-I05 | 洞察 instrument 語境 | #35 | sketch instrument/class＋catalog | 匯出／匯入 `insight.v1` | 一包一 primary；兩欄同 sketch/catalog exact match；仍然唔連策略／回測 |
| P2-I06 | 洞察 whole-identity 可恢復歸檔 | #36＋2026-07-28 archive spec | active `origin + insight_id` 全版本、archive list | 5秒延遲歸檔／復原／已歸檔恢復 | 零永久刪除；exact bytes＋雙SHA＋manifest；離頁取消 pending；restore no merge/no overwrite |

---

## 4. P3 數據頁

權威稿：[`p3-data.html`](ui/designs/p3-data.html)。本頁只講數據本身夠幾多，**唔判斷夠唔夠跑某一個策略**。

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P3-01 | 頁頂一句目的 | #1 | 無 | 無 | 用 Owner 語言講「夠唔夠數據、點裁決／補數據」 |
| P3-02 | 來源頁＋已帶篩選 banner | #12 | contract／trading dates／source | 睇全部／清 filter | 由 P1／P4 入嚟要直接見到並高亮嗰日 |
| P3-03 | 每合約覆蓋卡 | #2/#3 | 日期範圍、完整／問題／排除日 | 唯讀 | 顯示交易日數，唔以 bars 當答案；唔講某策略夠唔夠 |
| P3-04 | 原生日線＋最長連續完整段 | #2 | native daily coverage＋連續區段 | 唯讀 | 日線獨立列；最長段日期同日數要準 |
| P3-05 | 未裁決日卡：問題＋現時後果 | #4/#5 | 已知問題交易日＋人話描述 | 無 | 系統逐日列，唔叫 Owner 手打日期 |
| P3-06 | 「信呢日數據」 | #4 | 該日問題 | 記錄 trust decision | 旁邊明講回測點處理及偏樂觀風險 |
| P3-07 | 「唔好回測呢日」 | #4 | 該日問題 | 記錄 exclude decision | 旁邊明講完全跳過及樣本縮細風險 |
| P3-08 | 複製補數據指令 | #6/#9/#10 | 合約＋交易日 | 純文字 terminal command | 日期／UTC 由 app 換；唔扮 app 直接下載 |
| P3-09 | 已裁決列表＋改返 | #7 | 過往 decision＋使用該日嘅 run count | 新增修訂 decision | 警告「N 個回測用過；舊結果唔自動更新」 |
| P3-10 | 體檢記錄 | #8/#11 | contract、checked_at、summary | 捲到對應裁決卡並高亮 | 唔顯示檔名；checked_at 標時區；交易日唔轉 |
| P3-11 | 手動補數據區 | #9/#10 | 合約＋由／到交易日 | 複製 command；重新檢查 | 明講 app 唔直接向 IB 攞數據；唔叫 Owner 打 UTC 字串 |
| P3-12 | 問題碼翻譯 | 稿內翻譯表；#13 | quality code＋數值 | 人話問題 | 後端問題碼唔出畫面；正常 settlement diff 唔誤報做錯 |
| P3-13 | 交易日／時刻測試 | #11 | 兩個 timezone fixture | 無 | 交易日在兩個 TZ 顯示同一日；體檢時刻則正確轉換兼標 TZ |
| P3-14 | 本頁開工守衛 | #14 | 指定工作令 | 無 | 唔在窄路徑；未收到工作令唔開工 |

---

## 5. P4 回測頁

權威稿：[`p4-backtest.html`](ui/designs/p4-backtest.html)。全頁三段：設定、正在跑、最近回測。

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P4-01 | 已確認策略多選 | 畫面設定；#1/#2 | confirmed versions | 選擇策略 | 冇 validation-run toggle；冇 parameter override |
| P4-02 | 合約多選 | 畫面設定 | available contracts | 選擇合約 | 同策略形成明確乘積 |
| P4-03 | 本地時間範圍＋UTC 對照 | #3 | local picker | 產生 canonical range | picker 本地時間；旁邊同時顯示 UTC |
| P4-04 | session 唯讀摘要 | #4 | 每個策略文件 session | 唯讀 | 多策略不同 session 要逐個列，唔合成假單值 |
| P4-05 | 資金與成交假設收合摘要 | #17 | defaults＋策略＋合約 | 展開／收起 | 預設收起；摘要講每次回測完整起始資金、成本、滑點、保守成交 |
| P4-06 | 可改假設欄位 | #17 | 初始資金、所選合約費用、整數 tick 滑點 | 更新待提交設定 | 資金 > 0；成本／滑點 ≥ 0；滑點只整數 tick；錯誤落欄位旁並阻提交 |
| P4-07 | 唯讀策略風險／系統成交原則 | #17 | 每策略風險、每日上限；保守成交、1m 精度 | 唯讀 | 多策略逐個列；唔准喺 P4 改 |
| P4-08 | 每組獨立帳戶＋假設快照 | #17 | 策略×合約矩陣 | submit immutable snapshots | 每次各自完整 USD 100,000 起步，唔共同攤分；資金、成本、成交假設逐 run 鎖死 |
| P4-09 | 開始前：數據覆蓋 | #5 | P3 coverage＋所選範圍 | pass／block／去 P3 | 缺口要講日期及下一步；去 P3 帶住 filter |
| P4-10 | 開始前：暖機 | #5/#6 | confirmed strategy＋engine dependencies＋run前原生日線 | warning／建議下一步 | 現行 Trend exact 95 個run前合資格日；講實際可評估日數。不足只可回填更早資料或將開始日推後；唔准用run內future bars、策略名或frontend常數推算 |
| P4-11 | 開始前：相同組合已跑過 | #5/#7 | 版本＋合約＋範圍 | 去結果／明確照跑 | 三者全同先算重複 |
| P4-12 | 人話預覽＋開始掣 | #8 | 選中數量＋估時 | submit | 「2 策略×2 合約＝4 次」；缺選項 disabled 並逐項講差咩 |
| P4-13 | 總進度 | #9 | queued／running／done count＋time | 收合／展開列表 | 完成、行緊、等緊數字精確 |
| P4-14 | 逐個進度列表 | #9–#11 | 日期、交易數、PnL progress | 去結果 | 正在跑行高亮；每交易日更新；fixture 與真 API 狀態要明確分開 |
| P4-15 | 取消未開始回測 | #12/#13 | queued/running states | cancel queued only | 文案完整講 running 會跑完；唔准寫「停止」 |
| P4-16 | 最近回測列表 | #15 | recent runs | 看結果／再跑一次 | 只夠辨認；再跑用同樣策略、合約、時間及鎖定假設 |
| P4-17 | 失敗展開＋複製全文 | #14 | 完整錯誤原文 | 去 P3／複製純文字 | 一個失敗唔拖累其他；唔只顯示摘要 |
| P4-18 | 全頁用語守衛 | #16 | 所有 UI 文案 | 無 | 按稿內禁詞表逐字掃描 |

---

## 6. P5 結果頁

權威稿：[`p5-results.html`](ui/designs/p5-results.html)。兩層：結果主頁 → 單一回測詳情。

### 6.1 主頁

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P5-M01 | 結果數目＋策略／合約篩選 | 畫面第一層 | runs、filter | 篩選列表 | 可接 P1 未睇 filter；數目同篩選結果準確 |
| P5-M02 | 結果行三組資料 | #1/#2 | strategy、contract、score summary、reason | 點整行入詳情 | 只顯示策略·合約／成績／一句為咩 |
| P5-M03 | 0 成交行 | #3 | zero-trade bottleneck | 入詳情 | 唔空白；寫被邊層截住＋「點入去睇為咩」 |
| P5-M04 | 未睇狀態 | P1 #8 | front-end read state | 打開詳情後標已睇 | 逐個記，冇全部標記 |

### 6.2 詳情

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P5-D01 | 返回＋結果摘要 | #1 | 單一 run | 返回所有結果 | 時間、交易日、交易、勝率、R、USD、回撤用人話 |
| P5-D02 | 「用咗策略版本」回鏈 | #8 | locked strategy version＋sketch lineage | 去 P2 過目完整參數＋原始草圖 | 唯一策略回鏈；本機缺草圖要誠實顯示，唔斷策略內容 |
| P5-D03 | 四格圖表 2×2 | #4–#6 | chart series、volume、indicators、trades、events | crosshair／inspect | 用 G-12；四格同時，唔用 TF tabs |
| P5-D04 | 圖上交易時間／價位標記 | #6b/#6c | entry／exit timestamp、price、ordinal | 點擊／高亮 | 5m 直接標入離場；D/1H/30m 垂直線標 #N＋時刻；唔只 hover |
| P5-D05 | 交易選取同步 | #5/#6c | selected trade | 四圖跳同一時刻 | #N 同下方逐筆解釋一一對應 |
| P5-D06 | 逐筆因果解釋 | #7/#13 | 結構化 decision evidence | 展開每筆 | 四答：入市條件＋數值／止損＋理由／先觸發離場／保守假設；唔係 event log |
| P5-D07 | 0 成交漏斗 | #9/#10/#14 | stages、reject counts | 顯示 | 日級與評估級分尺、標單位 |
| P5-D08 | 最接近三次＋判斷 | #9/#14 | timestamped near-misses | 點一項→四圖跳時刻 | 固定三次；逐次差咩；附「邏輯正常／定義有分歧」判斷 |
| P5-D09 | 機會漏斗＋評估記分卡 | 畫面詳情；#10/#15 | funnel＋scorecard | 唯讀 | 11 項評估可顯示樣本不足；系統只警告，唔代 Owner 拍板 |
| P5-D10 | 匯出結果交接卡 | #16 | 當前 immutable run artifacts | 開匯出 Dialog | 位於全部證據之後、決定之前；每次只當前一個 run；同拍板完全獨立 |
| P5-D11 | `result.v1` zip Dialog | #16 | result＋trades／equity／events sidecars | 下載、複製 Terminal 開場白、關閉 | 包內相對引用可解；0 成交合法且 events 帶最接近三次；開場白用準確檔名並叫 Terminal 先讀主檔、需要時先讀 sidecar；只打包既有 artifact，唔重跑／重算／改寫 |
| P5-D12 | 匯出準備／失敗 | #16 | packaging state／完整錯誤 | 重試／複製錯誤 | zip 真正可下載先成功；失敗 Dialog 留住完整可複製錯誤 |
| P5-D13 | 決定理由輸入 | #15 | Owner 一句理由＋score snapshot | enable 三個決定 | 理由必填；三掣共用同一條規則 |
| P5-D14 | 三個不可變決定 | #15 | `use`／`rework`／`abandon` | 記錄 PromotionDecision | 三種都記；只增不改；系統唔自動決定 |
| P5-D15 | 晉升／打回／放棄下一步 | #15/#16 | decision＋run context | use→P6；rework→Terminal；abandon→留記錄 | 只有「用得」可令策略出現喺 P6；匯出唔自動作決定 |

---

## 7. P6 模擬盤頁

現行權威：
[`2026-07-31-p6-local-paper-runtime-mvp-design.md`](superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md)。
舊`p6-paper.html`已從現行工作樹刪除，因為內含Telegram、固定timeframe及冇可信
價仍自動平倉等過時規則。真implementation通過後先重建current P6 HTML。
一個交易員＝一個鎖死策略版本＋合約＋baseline＋timeframes＋獨立帳戶。

### 7.1 總覽與新增交易員

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P6-A01 | 總覽＋每交易員一個分頁 | #1/#13 | traders＋active tab | 切換 | 唔做列表鑽入；多過 6 個橫向捲；總覽釘左 |
| P6-A02 | 分頁狀態色 | #4 | normal／warning／stopped | 顯示 | 即使開緊另一交易員都睇到黃／紅 |
| P6-A03 | 總覽交易員卡 | 畫面 A；#2/#7 | status、position、R、trades、divergence | 去該交易員／睇停因 | 最重要係「要唔要理」；UI 叫交易員，唔叫部署 |
| P6-A04 | 「新增交易員」入口 | #2/#3b | promotion-qualified strategies | 開新增流程 | 只列 P5 曾獲「用得」決定嘅版本 |
| P6-A05 | 策略版本＋合約＋timeframe選擇 | 新P6 spec §§4–5 | eligible version、contracts、backend capabilities | 選擇 | MVP chart display顯示1m／30m；input／execution為typed 1m；策略D／1H／5m由immutable strategy.v1鎖定；建立後鎖死；frontend唔自建永久清單 |
| P6-A06 | baseline 親手選擇 | #3b | matching immutable runs | 選擇一個 run | 唔自動揀最近；由結果入嚟只標「啱啱睇」，仍未選中 |
| P6-A07 | 獨立帳戶起點 | #3c/#3d | baseline initial capital | 顯示／鎖定 | 每 trader 新帳戶；跟 baseline，目前各自 USD 100,000，唔共同攤分 |
| P6-A08 | 鎖定設定摘要 | 新P6 spec §§2／4／5 | version、contract、baseline、timeframes、capital、risk、execution | 覆核 | 明講app自家模擬成交、IB只供行情、唔落live／paper order |
| P6-A09 | Runtime開始前檢查 | 新P6 spec §8.2 | identities、baseline、timeframes、IBKR Gateway、mode、calendar、store、lease、cursor、safety | pass／fail／unknown | Notification唔係check；任一required fail／timeout／unknown都禁止開始並講人話 |
| P6-A10 | 休市狀態 | #3f | exchange calendar | 仍可建立 | 休市唔係依賴失敗；建立後等下一可交易時段 |
| P6-A11 | 失敗後重新檢查 | #3e | retained selections＋diagnostics | recheck | 保留已揀內容；開始掣灰；唔叫 Owner 重填 |
| P6-A12 | 建立前最後確認 Dialog | 新P6 spec §4 | 所有將鎖死內容 | 返回修改／確認建立 | 建立同開始係兩個動作；只覆核，唔加新欄位或隱藏預設 |
| P6-A13 | 建立中防重＋結果未知恢復 | #3g/#3h | idempotency request | 查同一次 request | 建立中禁重複撳；未知先查狀態，確認未建立先可重試 |
| P6-A14 | 建立成功＋新分頁 | 新P6 spec §4 | provisioned trader | 開新 trader tab | 明講未啟動；休市／冇訊號誠實顯示；唔畫假交易 |
| P6-A15 | MVP 無盤前計劃 | #3j | 無 | 無 | 唔顯示、唔要求、唔消費市場狀態／盤前參數 |
| P6-A16 | 明確開始runtime | 新P6 spec §8 | fresh preflight＋request identity＋expected lifecycle version | starting／running | LIVE同TEST-DELAYED分清；double-submit exact one；create success唔自動start |

### 7.2 單一交易員詳情

| ID | 畫面單元／責任 | 權威來源 | 輸入 | 動作／輸出 | 必驗狀態與規則 |
|---|---|---|---|---|---|
| P6-B01 | 交易員 header＋鎖定策略回鏈 | #3/#3d | trader＋locked strategy | 去 P2 過目 | 顯示開始時間、交易日、狀態；鎖定內容不可改 |
| P6-B02 | Timeframe-aware runtime圖 | 新P6 spec §§5／14 | forming display bars、closed bars、indicators、position、events | cursor update／crosshair／jump | forming bar只顯示唔決策；timeframe／LIVE或TEST-DELAYED持續可見；唔全量reload |
| P6-B03 | 未平倉持倉卡 | P5 稿尾並集；畫面 B | entry、stop、target、mark、unrealized PnL | 唯讀 | 圖同卡資料一致；冇持倉有誠實 empty state |
| P6-B04 | 由開始到今日成績 | 畫面 B | trades、R、drawdown | 唯讀 | 唔搶過偏離比較；單位準確 |
| P6-B05 | 逐單列表＋漏做交易 | #6 | actual trades＋baseline expected trades | 點一行→四圖跳時刻 | 「回測做咗但佢冇做」都列，並講原因 |
| P6-B06 | 同 baseline 比 | #3b/#7 | locked baseline＋actual window | 顯示逐項差異 | 比固定 run；入市次數、勝率、每單平均、成交價差等用同口徑 |
| P6-B07 | 系統偏離解讀 | #7 | divergence evidence | 唯讀 | 分市場／執行／策略理解；唔只顯示 PnL |
| P6-B08 | 偏離匯出交接卡 | #17 | current trader＋locked baseline | 開匯出 Dialog | 只放單一 trader 詳情；位置在偏離解讀後、安全網前；匯出不改 trader 狀態 |
| P6-B09 | runtime `paper-review.v2` 快照＋zip | 新P6 spec §13 | 一致 cutoff 下已保存嘅 market／runtime／paper／divergence／baseline evidence | 下載、複製 Terminal 開場白 | 舊v1保持可讀；新匯出＝新snapshot；同request重試返原snapshot；relative refs＋SHA-256 |
| P6-B10 | 運行／暫停／熔斷／永久停止匯出 | #17；畫面 B1 | trader state | 匯出 | 四狀態均可；有持倉照實記 open position；零成交合法；匯出後狀態不變 |
| P6-B11 | 匯出 fail-closed | #17；畫面 B1 | preparing／failure／unknown recovery；missing member／bad ref／hash mismatch | recheck／複製診斷 | 準備、成功、失敗、結果未知分清；任一必要成員錯整包不可下載；唔產生部分 zip；準確講缺咩 |
| P6-B12 | 安全網實際數值 | 新P6 spec §§9–10 | 8R／8-loss limits＋current values＋blind／stale state | 唯讀 | 唔只寫「正常」；>5分鐘或缺口不完整會trip；冇可信價保留position，禁止假平倉 |
| P6-B13 | 暫停／永久停止 | 新P6 spec §§8–9 | active trader＋trusted market state | stop new decisions／flatten pending／state change | 有可信價先模擬平倉；冇行情保持pausing／stopping；永久停止不可重開；記錄永不刪 |
| P6-B14 | 熔斷原因＋逐單證據 | 畫面 C；#10 | trigger、trades、baseline comparison | 睇逐單詳情 | 講觸發規則、系統做咗咩、同 baseline 點唔同 |
| P6-B15 | 重新開始守衛 | #10 | 已睇逐單證據 flag | re-arm | 預設灰；睇過逐單詳情先可用；必須 Owner 人手 |
| P6-B16 | 誠實未實作／零交易員狀態 | #15/#16；畫面 D | subsystem readiness＋trader count | 只提供設計准許下一步 | 唔畫假數字／灰色圖；未有後端時明講未做；本頁最後開工 |

---

## 8. 跨頁資料交接覆蓋

呢張表只驗「上一站產出，下一站認唔認得」。完整接口風險仍以 `docs/09` 為準。

| Flow ID | 由 → 去 | 必須帶過去嘅 identity／artifact | 對應畫面行 | 成功條件 |
|---|---|---|---|---|
| F-01 | P2 草圖 → Terminal | composite sketch id＋instrument/class＋六檔圖文包＋自包含 instructions | P2-S09–S10/P2-S12–S13 | 下載包同畫面包完全相同；instrument 由 Owner 選並已鎖，Terminal 唔准改 |
| F-02 | Terminal → P2 量化確認 | `strategy.v1`＋composite lineage＋primary/class/contracts/rationale/session | P2-Q01–Q06/P2-Q11–Q12 | 分清 lineage invalid vs 本機草圖缺失；same-class/session/USD/rationale gate 全通；App 唔改 universe |
| F-03 | P2 確認 → 版本庫 | immutable strategy version | P2-Q10、P2-L01 | 確認前零寫入；確認後可選 |
| F-04 | P2 版本 → P4 | exact strategy version＋hash＋confirmed universe | P2-Q11–Q12、P4-01、P4-08 | 回測合約只可選 universe ∩ configured/available data；每 run 各自 USD100k；manifest 鎖 exact expiry contract |
| F-05 | P3 → P4 | coverage／decision facts | P3-03–P3-09、P4-09 | 未裁決日阻開始；裁決／補數據後可重檢 |
| F-06 | P4 → P5 | immutable run＋sidecars | P4-14/P4-16、P5-M02/P5-D01 | 進度完成可直達該結果；結果身份不漂移 |
| F-07 | P5 → P2 | locked strategy version＋sketch lineage＋primary/universe | P5-D02、P2-L02 | 一撳見當時參數＋原始草圖＋市場聲明；缺草圖誠實降級 |
| F-08 | P5 → Terminal | self-contained `result.v1` zip | P5-D10–D12 | 當前 run、包內引用可解、唔重算 |
| F-09 | Terminal → P2 再迭代 | new self-contained `strategy.v1` with direct lineage＋instrument universe | P5-D15、P2-Q01/P2-Q11–Q12、P2-L04 | 新版本唔覆蓋舊版，重新驗證整份文件／確認／回測 |
| F-10 | P5 → P6 | immutable PromotionDecision＝use | P5-D13–D15、P6-A04 | 只有獲准版本可選；一般 confirmed 不足 |
| F-11 | P5 run → P6 baseline | Owner 親手揀嘅 exact run_id＋capital | P6-A06–A08 | 唔自動鎖最近；版本／合約／baseline 相容 |
| F-12 | IB realtime → P6 | 價格 only＋connection state | P6-A09、P6-B02 | fail-closed；唔向 IB 發任何訂單 |
| F-13 | P6 trader → divergence | locked baseline＋actual＋expected／missed | P6-B05–B07 | 同一口徑逐項比較，可解釋漏做交易 |
| F-14 | P6 → Terminal | immutable runtime `paper-review.v2` zip | P6-B08–B11 | 一致cutoff、market/runtime evidence、完整baseline、hash全通；舊v1保持可讀 |
| F-15 | Terminal → 新一輪 P2 | direct-child self-contained `strategy.v1`＋instrument universe | P6-B09、P2-Q01/P2-Q11–Q12 | 舊策略／trader／baseline／snapshot 全部不變；新版本重新過完整 market gate |
| F-16 | 其他頁 → P1 | 七類 read-only action facts | P1-01–P1-12 | P1 零寫入；每項出現／消失條件精確 |

---

## 9. 約束反查：證明六份稿冇漏

矩陣共 **145 個可見／可操作驗收單元＋16 條跨頁交接＝161 個不重複 ID**。

| 權威稿 | 每條正式約束 → 驗收行 |
|---|---|
| P1 | `#1→P1-01` · `#2→P1-10` · `#3→P1-10` · `#4→G-03/P1-10` · `#5→P1-02` · `#6→P1-02` · `#7→P1-08` · `#8→P1-06/P5-M04` · `#9→P1-11` · `#10→P1-12` · `#11→G-08` · `#12→G-07/P1-12` · `#13→G-10/P1-01` · `#14→P1-02–P1-09` · `#15→P1-13` |
| P2 | `#1→P2-01` · `#2→G-12/P2-S03/P2-Q09` · `#3→P2-S03` · `#4→P2-S09` · `#5→P2-S02/P2-S06–P2-S08` · `#6→P2-S05/P2-S08` · `#7→G-11/P2-S10` · `#8→P2-S09` · `#9→P2-Q04` · `#10→P2-Q05` · `#11→P2-Q06/P2-L02` · `#12→P2-Q07/P2-Q10` · `#13→P2-Q08/P2-Q09` · `#14→P2-Q08` · `#15→P2-Q10` · `#16→P2-L01–P2-L03` · `#17→P2-L03` · `#18→P2-L04` · `#19→P2-L05` · `#20→P2-L06` · `#21→P2-L06` · `#22→P2-I03/P2-I04` · `#23→P2-I04` · `#24→G-09` · `#25→G-07` · `#26→P2-S09/P2-S10` · `#27→P2-S01` · `#28→P2-S12` · `#29→P2-S13` · `#30→P2-S12/P2-S13` · `#31→P2-Q11` · `#32→P2-Q11` · `#33→P2-Q12` · `#34→P2-Q12/F-04` · `#35→P2-I05` · `#36→P2-I06` |
| P3 | `#1→P3-01` · `#2→P3-03/P3-04` · `#3→P3-03` · `#4→P3-05–P3-07` · `#5→P3-05` · `#6→P3-08` · `#7→P3-09` · `#8→P3-10` · `#9→P3-08/P3-11` · `#10→P3-08/P3-11` · `#11→G-07/P3-13` · `#12→P3-02` · `#13→G-08/P3-12` · `#14→P3-14` |
| P4 | `#1→P4-01` · `#2→P4-01` · `#3→P4-03` · `#4→P4-04` · `#5→P4-09–P4-11` · `#6→P4-10` · `#7→P4-11` · `#8→G-10/P4-12` · `#9→P4-13/P4-14` · `#10→P4-14` · `#11→P4-14` · `#12→P4-15` · `#13→P4-15` · `#14→G-06/P4-17` · `#15→P4-16` · `#16→G-08/P4-18` · `#17→P4-05–P4-08` |
| P5 | `#1→P5-M02/P5-D01` · `#2→P5-M02` · `#3→P5-M03` · `#4→G-12/P5-D03` · `#5→G-12/P5-D05` · `#6→P5-D03` · `#6b→P5-D04` · `#6c→P5-D04/P5-D05` · `#7→P5-D06` · `#8→P5-D02/F-07` · `#9→P5-D07/P5-D08` · `#10→P5-D07/P5-D09` · `#11→G-07` · `#12→G-08` · `#13→P5-D06` · `#14→P5-D07/P5-D08` · `#15→P5-D09/P5-D13–P5-D15` · `#16→P5-D10–P5-D12/F-08` |
| P6 | `#1→P6-A01` · `#2→G-08/P6-A03/P6-A04/P6-B13` · `#3→P6-A05/P6-B01` · `#3b→P6-A04–P6-A06/F-10/F-11` · `#3c→P6-A07` · `#3d→P6-A05–P6-A08/P6-B01` · `#3e→P6-A09/P6-A11` · `#3f→P6-A10/P6-A14` · `#3g→P6-A08/P6-A12/P6-A13` · `#3h→P6-A13` · `#3i→P6-A14` · `#3j→P6-A15` · `#3k→P6-A08` · `#4→P6-A02` · `#5→G-12/P6-B02` · `#6→P6-B05` · `#7→P6-B06/P6-B07` · `#8→P6-A08` · `#9→P6-B12` · `#10→P6-B14/P6-B15` · `#11→P6-B12` · `#12→P6-B13` · `#13→P6-A01` · `#14→G-07/G-08` · `#15→G-05/P6-B16` · `#16→P6-B16` · `#17→P6-B08–P6-B11/F-14/F-15` |

反查規則：

1. 設計稿新增／經 Owner 修訂約束後，先更新設計稿；
2. 再喺本節確認新編號有落到一個或以上畫面行；
3. 再更新 `docs/08` 嘅供應狀態同 `docs/09` 嘅接口狀態；
4. 未完成以上三步，唔准出 X／Y 工作令。

---

## 10. 目前實作驗收基線（2026-07-29）

呢度只記**已實證狀態**，唔用「已有 code」推定通過。

| 範圍 | 前端驗收 | 後端／資料供應 | 下一個 gate |
|---|---|---|---|
| G 全局 | 🟡 shell／三主題／誠實狀態原則已有；未按六稿做最終橫向驗收 | 混合 | 每頁收貨時一齊驗；四格組件最終要跨 P2／P5／P6 驗 |
| P1 總覽 | ⬜ 未按定稿驗收 | 接近齊；部分聚合＋前端本地 read state | 其他頁真資料流走通後做定稿批次 |
| P2 工作台 | 🟢 origin-aware read、instrument／whole-universe、版本庫、catalog、preview及insight normal/archive seams已逐批收貨；exact integration只限已留下證據嘅slice | backend composite store／package intake／catalog／preview／version lifecycle及insight whole-identity archive/list/restore已收貨 | 保持凍結；未有新P2工作令，唔由矩陣自行擴 scope |
| P3 數據 | 🟠 已有aggregate coverage畫面，但Owner手測證明「有咩可用日、點揀去回測」仍唔清楚，query symbol亦未形成可靠手動交接 | ✅ backend已有complete/problem dates同longest clean segment；main migration已完成 | 另開P3→P4產品接線工作令；**唔屬於Y `[205]`** |
| P4 回測 | 🟢 normal回測頁、admission／queue／progress及Activation Gate exact scope已收貨 | ✅ P4-A／P4-B、真main migration及獲批standard activation smoke已驗證；唔准重跑真smoke | 凍結現行seam；日後只接P3可用日期handoff，需獨立工作令 |
| P5 結果 | 🟢 normal read、charts、single export、PromotionDecision/history及受控frontend↔backend integration已收貨；Owner批准exact一次真`use`決定 | ✅ export、append-only decision、history、eligible-strategies及CORS exact correction已收貨 | exact真slice已證明`use`→default immutable row→P6 eligibility；唔代表整頁或全journey TRUE E2E |
| P6 模擬盤 | 🟠 `[205]` frontend Stage A施工中：strict mock、Owner手選baseline、四狀態、確認、idempotent create／unknown recovery、truthful list/detail | 🟠 `[X-097]` backend Stage A施工中：temp/injected state、default-deny、immutable provisioning；唔建default DB | 兩份REPORT及C REVIEW後先決定contract/integration批；runtime、orders、PnL、偏離、paper-review、IB全部未授權 |

**頁面收貨條件**：該頁全部 row 有證據；相關 `F-*` 由前站到後站真實跑通；`docs/08` 冇被 fixture 冒充真 API；design constraint 反查零漏項。

**Legacy 驗收補充（唔係用矩陣改設計）**：`strategy-0001`／`0002` 同既有 run／result 必須保留可讀；任何新 run 仍用同一套現行 validator，冇完整 composite lineage 或 v1.4 instrument universe 一律拒絕。舊未匯出 frontend draft 缺 instrument/class 可由 Owner 補揀；舊已匯出 package 只讀、要 duplicate。任何一方都唔准推斷或改寫 legacy bytes。

---

## 11. 工作令／REVIEW 使用模板

### 工作令

```text
權威稿：docs/ui/designs/pX-....html
實作約束：#...
本矩陣：PX-...、G-...、F-...
範圍：只做以上 rows；矩陣不得用來改設計
資料：按 docs/08 對應項；fixture／真 API 要明確分開
驗收：正常＋loading＋empty＋error＋disabled/guard＋跨頁落點
```

### REVIEW

```text
逐 row：
- 畫面有冇
- 文案／位置／層級對唔對
- 輸入係真資料、fixture 定本地狀態
- 動作有冇產生設計准許嘅唯一輸出
- loading／empty／error／unknown 有冇 fail-closed
- deep link／filter／artifact identity 有冇帶到下一站
- 測試證據係咪真係測緊呢條規則，而唔係探測方法出錯
```

落判斷前固定問一次：**「係執行者個 code 錯，定係我個探測錯？」**
