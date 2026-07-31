# 項目現況 — 新 agent 開工前必讀

> **呢份文件嘅用途唔係總結，係提取。** 設計稿喺 `docs/ui/designs/`、決定喺 `docs/00`、規格喺 `docs/05`——如果本文只係重複佢哋就冇價值。
>
> 本文答嘅係：**一個新 agent 開工前必須知、但唔會自己發現嘅嘢。** 尤其係第 4 節（踩過嘅坑）同第 5 節（睇落應該改但唔准改）。
>
> 最後更新：**2026-07-31**。現役監督係C-v5；Owner已改用「一個Agent負責一個
> 完整功能」，P6模擬盤由新Agent W跨frontend/backend接管。W零context入口係
> `docs/AGENT_W_HANDOVER.md`，唯一正式渠道係`AGENT_CHANNEL_W.md`。X A2及
> Y A3最後corrections已由C `[X-147]`／`[245]`獨立收貨，X/Y P6 lanes已結束；
> 舊channels、handover／briefing、87份work orders及被取代P6稿已從現行工作樹
> 刪除，Git history仍可精確恢復。
> 舊`[W-ACTIVATE]`從未轉發／未開始，已由`[W-001]`撤銷。Owner已逐段批准
> `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`：
> W之後會一口氣完成整個P6；書面規格及單一implementation plan已完成，而家只等
> C最新channel工作令由Owner短指令轉發後啟動。Owner其後明確批准
> **IBKR Gateway (Simulated Trading)取代TWS**：`127.0.0.1:7498`、client ID 7
> 由app提交、Read-Only API，仍然只供行情，任何IB order保持零permission。

---

## 1. 呢個項目係咩

Owner 係一位期貨交易者，喺建立一個**個人策略研究平台**（唔係產品，冇其他用戶）。

```
同 terminal AI 傾策略 → AI 出策略文件 → app 驗證＋回測（真 IB 數據）
→ 評估記分卡 → Owner 拍板 → 自家模擬盤 → 偏離對照 → 循環改良
```

- **引擎**：NautilusTrader（Rust core ＋ Python 控制層）
- **數據**：真 Interactive Brokers，美國期貨 NQ／YM／GC
- **零 AI API**：app 唔會叫任何 AI。app 同 terminal agent 之間**靠有 schema 嘅檔案溝通**（`sketch.v1` 出、`strategy.v1` 入、`result.v1` 出、runtime `paper-review.v2` 出；舊zero-runtime `paper-review.v1`保持可讀）
- **前端** `apps/web/`：React 19 ＋ TypeScript ＋ Vite
- **後端** `src/futures_research/`：Python ＋ FastAPI（讀寫 API）＋ SQLite ＋ Parquet

**角色**：**Agent C**同Owner定設計、出工作令、審交付同守跨頁閉環；
**Agent W**係P6唯一full-stack executor，按C exact工作令同時負責相關
`src/futures_research/`、`apps/web/`、tests及cross-layer evidence。X／Y只完成
transition correction，之後舊channels read-only。

### Owner 長期工作偏好（2026-07-26）

**凡係頁面、畫面、版面或互動設計要 Owner 拍板，必須先交可打開嘅 HTML ＋ PNG 設計圖；唔准只靠文字描述叫 Owner 想像。每次要有兩套完全相同副本：一套留喺 project、一套放 Desktop 方便即時打開。**文字只用嚟解釋圖中功能、資料流同取捨。討論稿未獲 Owner 批准前，唔得覆蓋 `docs/ui/designs/` 內嘅權威設計稿，亦唔得據此向執行者出工作令。

**P6 runtime一次性例外（Owner 2026-07-31明確批准）**：舊P6 visual因包含已推翻嘅
Telegram／固定timeframe／無價自動平倉而先退役；Owner已逐段批准完整書面runtime
設計，真implementation通過後先重建current P6＋full-flow HTML。呢個例外唔授權
W自行改visual hierarchy；任何超出已批結構嘅新layout仍要先交HTML＋PNG。

**執行者指令 gate（Owner 2026-07-26；2026-07-31更新傳遞方式）**：遇到未裁決
問題，Agent C只向Owner提出證據、選項同推薦，唔先行append工作令。Owner同C
拍板後，C把完整長工作令寫入現役file channel；回覆Owner只附一段可轉發俾
executor嘅短指令。

**Review 後 instruction gate（Owner 2026-07-27；2026-07-31改為feature owner）**：
C完成每份executor REVIEW後，無論係PASS、correction或需Owner裁決，都必須俾
現役feature owner一份可執行instruction（next work order、correction work order
或explicit hold）。**唔准只寫「PASS／等下一步」令executor無限停低。**

**C嘅代碼邊界（Owner 2026-07-28澄清）**：Agent C唔寫產品代碼、唔做
production fix——修改代碼係現役executor責任，C發現問題就喺file channel出
correction。但C獨立審查時嘅**臨時mutation probe（故意整壞一行→證明指定
測試會紅→即刻byte-exact還原，零淨改動）係必須驗證手段**。任何會留低淨
產品改動嘅C動作，先問Owner攞明確批准。

---

## 2. 而家喺邊個階段：完整P6設計／plan已批，等Owner轉發啟動W

P1–P5既有頁面設計保留；P6舊Telegram／固定timeframe／無價自動平倉視覺稿已
退役。Owner已逐段批准新完整P6 runtime書面設計，final current HTML會喺真實作
通過後重建，避免mockup冒充工程真相。完整路徑係：

```text
策略工作台（Owner primary instrument → Terminal self-contained universe）
  → 數據 → 回測（confirmed universe 子集 → exact contract）→ 結果
  ├─ result.v1 → Terminal → 衍生 strategy.v1 → 返回策略工作台
  └─ （可選）PromotionDecision「用得」作歷史意圖
模擬盤：獨立入口，直接讀 confirmed 策略 + verified baseline（唔再硬閘「用得」）
       → paper-review.v2 → Terminal → 衍生 strategy.v1 → 循環
```

**頁面獨立原則（2026-07-31 Owner）**：詳見 `docs/10-page-independence-principle.md`。
每頁係獨立入口；資料真相喺 DB／backend；冇資料＝soft empty，唔係上一頁交接失敗。

**工程仍未由頭到尾接通**。Executor切換已完成：

- **X-v5／Y-v5.5 retired**：A2／A3 corrections已由C `[X-147]`／`[245]`
  收為各自`COMPONENT PASS`；舊channels永久read-only。
- **W full-stack owner／現時HOLD**：舊A4已撤銷；單一plan已完成。等Owner轉發
  C最新短指令後，一口氣完成frontend、backend、runtime、contracts及TRUE E2E。

### 2.1 整體進度基準（C每份REVIEW後必更新）

截至C `[245]`已收貨證據；由於Owner已將真IB market-data＋app自家runtime納入
今次MVP，分母由舊「provision-only MVP」擴大：

```text
舊provision-only流程口徑（只供歷史比較）             約84%
現行批准MVP（含完整P6 local paper runtime）          約65–70%
```

84%口徑已計入：策略工作台、真數據／回測核心、結果與匯出、immutable
PromotionDecision、P6 eligibility、舊Stage B producer→consumer contract，以及
Option A A1–A3各自component收貨。新P6 runtime、IBKR Gateway、simulated execution及
runtime UI仍未交付，所以設計批准唔增加工程百分比。

目前批准MVP剩餘critical path：

```text
Owner轉發W one-shot啟動短指令
→ W完成contract／integration／runtime／Gateway／default TRUE E2E
→ C獨立review
→ P3數據頁可用日期→回測頁交接
→ final whole-journey regression/manual acceptance
```

真IB **market-data session**、app自家paper runtime、模擬落單／成交／持倉／PnL
及基於真runtime嘅偏離，而家係現行MVP明確scope。IB只供應行情；app永遠唔向
IB live或paper account發單，Owner嘅IB paper account保留手動練習。

由2026-07-30起，每份C對任何executor嘅REVIEW除四級verdict、evidence、remaining boundary
同next/correction/hold外，必須更新：整體百分比、今次delta、已通／施工中／未通
journey及下一個critical gate。Active executor REPORT未經C收貨一律唔先加進度。

### ⚠️ 定稿唔等於可以自行開工

每批工作令都要指明正式設計稿、約束及exact files。W係full-stack owner都只做
工作令範圍，唔准因六頁定稿或plan存在就自行提前；亦唔准只做畫面唔接真資料，
或者只做API冇production consumer。

**P1總覽及P3數據目前冇新工作令。P6舊Stage B完整cross-layer contract已由
`[X-136]`收妥；Option A A1–A3已分別由`[X-142]`、`[X-147]`、`[245]`
收妥；舊A4已撤銷，W而家HOLD。仍未做真runtime、模擬成交、持倉／PnL或
runtime TRUE E2E。**
Owner手測另發現P3→P4仍缺「清楚列出可用交易日／一鍵帶日期去回測」接線；
係獨立缺口，W唔准順手塞入P6。

---

## 3. 已經建成咩（唔使重做）

| 範圍 | 狀態 |
|---|---|
| 數據管線 | ✅ IB 連接、1m bar 下載、四類質量檢查、歷史回填、**原生 settlement Daily**。NQ 156,912 根 1m bar、YM 136,222、GC 364,601 |
| 回測引擎 | ✅ canonical → Nautilus、MTF 合約、策略狀態機、中框架回踩閘、**保守成交語義**、不可變 artifact、可審計 replay |
| 評估 | ✅ 八維度記分卡（含 2b／3b／5b 三個子項共 11 項）＋ PSR／DSR／CVaR／Top-N ＋ 機會漏斗（雙單位） |
| 策略文件 | ✅ `strategy.v1` parser 四層驗證 ＋「parse 出嚟 vs 手搭」bit-for-bit 一致 |
| 前端基建 | ✅ Vite React shell、**三主題 token 系統**（stylelint 擋硬編碼）、唯讀 API client、測試基建 |
| 前端頁面 | 🟡 P2 normal insight及P5 controlled normal integration已驗收；P4 normal回測頁已接。P3未提供可用日期→回測嘅清楚手動交接。P6 Stage A、舊Stage B consumer及Option A A3 component已收貨；完整runtime UI設計已批、未實作。 |
| 模擬盤 | 🟠 Stage A temp integration已通；舊Stage B五route producer→consumer由C `[X-136]`收為`CONTRACT PASS`；Option A A1–A3各有`COMPONENT PASS`。One-shot plan已完成，W等Owner短指令啟動；真IBKR Gateway、runtime、simulated order/fill、position/PnL、safety及`paper-review.v2`未交付。Telegram已移除。 |
| 策略工作台圖文包 | ✅ composite store／origin-aware read／lineage、backend intake、catalog、preview、版本生命週期、insight base repository及whole-identity可恢復歸檔前後端seams已分批收貨；任何真洞察寫入仍只可按獨立授權。 |
| 回測反查索引 | ✅ X Batch 2 `8b38732` 已收貨；真 main DB migration＋Activation Gate亦已由`[X-071]`完整PASS，三種 standard-run lookup現有真known truth；唔准重跑migration／smoke／真export |

**最近獨立驗證基線**：C `[X-147]`親跑A2風險節點 **6 passed**、ruff、
mypy 68 source files及兩個mutation；C `[245]`親跑A3三檔 **275 passed**、
typecheck/lint/build及兩個mutation，全部byte-exact還原。較早C `[X-136]`
直接以production consumer重播保留producer evidence：5 calls／9 errors、
54,917-byte ZIP、10 ordered members，正式收為`CONTRACT PASS`。現行protected
基線：`data/` **790 files／34,849,558 bytes**，runs DB SHA
`1e102c3c…b8f04012`，PromotionDecision DB 24,576 bytes／exact one immutable
`use` row／SHA `5b340c32…9977ed79`，default `data/paper/` absent。當時
5173／8000／8876／7498 listeners及IB processes全0；呢個只係transition evidence，
唔係Owner而家已開IBKR Gateway嘅current fact。2026-07-31 C read-only核實
`127.0.0.1:7498`由`ibgateway.exe`監聽；W開工仍要重新量度。`[X-135]`保留嘅OS-temp
evidence root因安全政策拒絕刪除而仍留低882,117 bytes；不影響tracked/default
facts，唔當成產品artifact。

---

## 4. 🔴 踩過嘅坑（呢節係本文最重要嘅部分）

以下每一個都真實發生過，而且每一個都令一條規則存在。**唔讀呢節就會重犯。**

### ① 把「交易日」當「時刻」處理 → 數據錯配 −2 日

**發生咩事**：WO-003b 3b-1 下載原生 Daily bar 時，把交易日（`2026-07-06`）當成一個時刻去做時區轉換，結果 `07-06` 嘅 O/H/L 錯配去 `07-08` 嘅 session。**數字睇落完全合理**，要做 forensic 價格對齊（逐根 bar 比對高低點）才捉到。

**規則**：時間分三類，處理方式完全唔同——

| 類 | 例子 | 處理 |
|---|---|---|
| **時刻** | `checked_at`／`created`／batch 開始完結／事件 timestamp | 可轉本地時區顯示，**必須標時區** |
| **交易日** | `trading_date`／`roll_blackout_dates`／判斷簿「交易日」欄 | **絕對唔准轉時區**——佢係標籤唔係時刻 |
| **範圍輸入** | P4 `range_start`／`range_end` | 本地 picker，**同時顯示 UTC 對照**，送後端一定 UTC |

**要有一條測試**：同一個 `trading_date` 喺兩個時區下輸出同一個日子。

### ② fixture 嘅值恰好等於硬寫死嘅值 → 655 個綠燈證明唔到行為

**發生咩事**：一個匯入功能把 `validation_status` 硬寫成 `'unverified'`，丟棄文件原值。測試寫咗 `expect(created.validationStatus).toBe('unverified')`——**但兩個 fixture 嘅值都恰好係 `unverified`**，所以「保留原值」同「硬寫死」兩種實作都會過。655 個測試全綠，證明唔到嗰個行為。

**規則**：**一個永遠綠嘅測試同冇測試等價，而唯一嘅證明方法就係令佢紅一次。** 寫完測試之後故意把被測嘅嘢改壞（例如 `if (false && …)`），確認啱啱好嗰條紅、其餘全綠。

同一類問題嘅另一面：`getByText` 證明唔到「冇摺埋」——jsdom 唔計視覺摺疊，`<details>` 入面嘅字一樣 pass。**要守住嘅係「Owner 一定睇到」，唔係「DOM 入面有呢段字」。**

### ③ 編輯工具背後重寫整個檔案（三次）

**發生咩事**：① 另一個項目嘅 worklog 被編輯工具重寫（連 Markdown 行尾兩個空格嘅強制換行都被剝走）；② 同一個 app 嘅前任 agent 撞過 LF／CRLF 全檔改寫；③ **Agent C 自己**用 Python 改三行狀態板，`numstat` 顯示 **1382 insertions / 1350 deletions**——Python 把 LF 轉成 CRLF，令每一行都算改過。改用 binary mode 重寫之後變返 **35/3**。

**規則（渠道規則 1a）**：**commit 前必跑
`git diff --numstat -- AGENT_CHANNEL_W.md`**，刪除數必須等於「你今次刻意改嘅
行數」。多過呢個數就係工具背後重寫咗——要即刻由HEAD還原、byte-compare
核實、再重做。現役長channel只可做窄、可核對嘅修改。

**規則 1a 第一個捉到嘅人就係佢自己嘅作者。**

### ④ UI 有個掣，但後面從來冇接住嘢

**發生咩事**：批次回測隊列由頭到尾**冇注入 `strategy_spec`**——即係將來嘅「策略版本選擇器」會係假掣，揀唔同版本會出一模一樣結果。**呢個唔係審查捉到，係執行者自己揭發。**

**規則**：加一個控制之前，先確認後面真係接住嘢。而**證明方法唔係「讀代碼覺得應該通」，係跑兩次出唔同結果**——後來嘅驗收就係用兩個只差參數嘅策略版本各跑一次，證明 funnel 唔同（3 日／8 次 vs 0／0），再用 override 砌成第一個嘅參數，證明 funnel 完全等於第一個。**參數一樣就一樣、唔同就唔同，證明係同一條注入路徑而唔係撞啱唔同數字。**

### ⑤ agent 之間嘅語言漏到 UI

**發生咩事**：Owner 撞到「validation_run（工程驗證，非策略主張）」同「開啟參數 override」兩個控制，**完全唔明**（佢把 override 聽成 OpenRice）。

**結論唔係改個名，係整個刪除**——因為呢兩樣本來就唔係為佢而設：第一個係開發者驗證引擎用，第二個已被「喺策略庫改參數出新版本」取代。

**規則**：`docs/ui/designs/p4-backtest.html` 有一張**技術詞禁用表**（`batch`／`runs`／`nq-20260725-val-364e22`／`p50 閘`／`range_start`／`rth`／`eth`…），逐個列咗改成咩。**適用全部頁面。**

---

## 5. 🔴 睇落應該改，但唔准改

新 agent 見到呢幾樣會想「順手修好」。**唔准。**

| 現象 | 為何唔准改 |
|---|---|
| **全部 16 個回測都係 0 成交** | 唔係 bug，已完全診斷：Phase 1 範圍排除殺倉／橫行流程，而近期市場正是橫行（NQ 只有 26 個趨勢日、GC 一個都冇）。**唔准為咗見到成交而放鬆任何閘。** 真正要做係完善策略視覺確認閉環；盤前計劃已由 Owner 於 2026-07-26 明確押後到 MVP 完成後再研究 |
| 記分卡標題寫「**八維度**」但畫面 11 個 chip | 唔係 bug。`docs/00` D11 明文係「八維度記分卡（含 2b／3b 擴充）」＝ 8 主維度 ＋ 3 子項。**Agent C 一度誤判成 bug，查證後確認 UI 冇錯** |
| IB 狀態措辭止於「**port 可達 · 未驗證 session**」 | 刻意。而家只做 TCP 探測，未做 IB API handshake。**唔准改成「已連接」**——UI 唔准聲稱自己做唔到嘅嘢 |
| P3 下載掣**只記錄意圖唔會真下載** | 刻意，畫面有黃色告示叫 Owner 去 terminal 跑 `futures-research download`。**唔准偷偷實作真下載**（會撞 IB pacing 限制） |
| P1 兩張卡明確標 `placeholder` | 刻意誠實。**唔准填假數字入去令佢睇落完整** |

---

## 6. 前端為何要重新設計（根因唔係實作）

2026-07-25，Owner 親手逐頁試用之後：

> 「我發覺每個頁面都有問題，例如 UI 唔清晰啦，用戶嗰個流程體驗完全唔理解啦，見面之間唔連貫啦，最少喺前端嚟講用戶好難懂。」

**根因係 Agent C 嘅設計失誤，唔係執行者嘅實作失誤。**

Agent C 一直用**舊版** `docs/03` 組件矩陣驅動前端——每頁對應矩陣邊幾行，所以每頁單獨睇都「符合規格」，執行者亦忠實實作咗。**但從來冇人寫過「Owner 開呢頁嗰刻想做咩、做完之後去邊一頁」。** 舊矩陣係功能清單，唔係用戶流程。結果七頁各自正確、合埋一齊唔連貫。舊版已刪除；現行 `docs/03` 只係六稿之後倒推出嘅驗收工具。

**所以交俾執行者嘅嘢性質改變咗**：唔再係功能清單，而係 **mockup ＋ 逐項拍板決定**（`docs/ui/designs/`）。執行者唔需要做設計判斷。

### 設計稿狀態

**🟢 六頁全部定稿；P2 於 2026-07-27 加入批准補充，設計閉環仍然完整。**

| 頁 | 狀態 |
|---|---|
| **P2 策略工作台**（原工房 ＋ 策略庫合併，側欄「工房」取消） | ✅ 定稿 · **36 條約束**；#28–#35 係 instrument 閉環；#36 係 2026-07-28 Owner批准whole-identity可恢復洞察歸檔，MVP冇永久清除 |
| **P4 回測** | ✅ 定稿修訂 2026-07-27 · 17 條約束 ＋ 技術詞禁用表；第四步「資金與成交假設」採摘要收起方案，每次回測各自用完整初始資金起步。#6已對齊no-lookahead engine：現行Trend exact 95個run前合資格原生日線；不足只可backfill或將開始日推後 |
| **P5 結果** | ✅ 定稿修訂 2026-07-26 · 18 條正式約束列（`#1–#16`，另含 `#6b/#6c`）；證據後、拍板前獨立匯出完整 `result.v1` 結果包俾 Terminal（組件並集已加「實時更新」三項） |
| **P1 總覽** | ✅ 定稿 · 15 條約束 —— **行動式**（答「我而家應該做咩」），**只讀零寫入，掣只做導航** |
| **P3 數據** | ✅ 定稿 · 14 條約束 ＋ 技術詞翻譯表 —— 覆蓋要答「**夠唔夠用**」，唔係 bar 數 |
| **P6 模擬盤** | 🟡 **2026-07-31完整local runtime書面規格及implementation plan已批准**——IB只供行情、app自家paper execution、timeframe-agnostic、persistent multi-trader、safety/recovery及`paper-review.v2`；等Owner轉發啟動W；舊v4 visual已退役，唔再係runtime authority |

**兩份橋接文件**（階段 C 入口）：`docs/08-ui-backend-mapping.md`（由 UI 倒推後端）· `docs/09-journey-closed-loop-audit.md`（**閉環審計：設計已通、工程 seam 邊度未通**）

**跨頁共用**：四格圖表係**一個可重用組件**（P2 分頁 ②／P5 詳情／將來 P6 共用），介面要照三個用途嘅並集設計——詳見 `docs/ui/designs/README.md`。

---

## 7. 一個罕有優勢：同一個格式，另一個 app 已經行通

Owner 有另一個項目（交易日記 App），由另一個 agent 負責，曾實作共同嘅 `sketch.v1` v1.1 基礎格式。

**兩個 app 完全獨立、零代碼耦合。** 本項目 2026-07-27 已把合約升至 instrument/class v1.2；另一 app 未經自己工作令更新前，**唔准再假設兩邊最新輸出完全相同**。跨 app copy 必須先通過本 app 現行 validator，唔會為兼容而放鬆。

佢哋實作 v1.1 期間**捉到 Agent C 五處規格錯誤**（包命名前綴矛盾、example 遺漏欄位、`role` 只喺其中一份文件、要求 app 知道佢唔可能知嘅嘢、必填表遺漏 `indicators_shown`），全部已修。呢段只係規格審查經驗，**唔係現行實作依據**。

**對執行者嘅意思**：規格會有漏。撞到含糊位**停低問，唔准靜靜哋估**。佢哋五次都係「對讀兩份文件發現矛盾 → 喺渠道列出來源同兩種讀法 → 等裁決」，冇一次自己拍板改格式。

---

## 8. 文件地圖：邊度查咩

### 日常開工要讀

| 檔 | 內容 |
|---|---|
| **本文** | 現況、坑、唔准改嘅嘢 |
| `docs/AGENT_W_HANDOVER.md` | P6單一full-stack executor零context入口 |
| `AGENT_CHANNEL_W.md` | P6現役正式渠道；長工作令／REPORT／REVIEW全部落呢度 |
| `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md` | 現行完整P6 runtime設計；被取代P6 specs／visuals已從現行樹刪除 |
| `docs/superpowers/plans/2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md` | W單一完整施工及驗收plan；存在唔等於自行開工 |
| `docs/POST_MVP_BACKLOG.md` | PWA Push、Tailscale、cloud、auto-start及更多timeframe；唔係permission |
| `docs/ui/designs/README.md` | 六頁權威設計稿索引。全部已定稿，但只做最新工作令明確指定嘅範圍 |
| `docs/ui/designs/*.html` | 你要砌嘅嘢 ＋ 實作約束表 |
| `docs/03-frontend-component-coverage-matrix.md` | 六稿倒推嘅畫面 coverage／驗收行號；唔係設計來源，衝突時修矩陣 |

### 撞到唔明先查

| 檔 | 內容 |
|---|---|
| **`docs/08-ui-backend-mapping.md`** | 🔴 **階段 C 入口**：由已定稿 UI 倒推後端。六頁 × 要嘅數據 × 已有／要擴／全新 ＋ 缺口按依賴排序 ＋ 要新增嘅儲存 |
| **`docs/09-journey-closed-loop-audit.md`** | 🔴 **閉環審計**：逐個接口驗「一站嘅產出下一站認唔認得」。B1–B8 分清設計閉環同工程 seam |
| `docs/00-design-baseline-conclusions.md` | **D1–D21 核心決定**（全部 Owner 拍板）。最常引用 |
| `docs/02-mvp-closed-loop-user-journey.md` | 用戶旅程 S1–S9 |
| `docs/05-file-contract-schemas-draft.md` | 既有檔案合約權威（含zero-runtime `paper-review.v1`）；runtime `paper-review.v2`由2026-07-31 P6設計凍結，等W實作後同步本文 |
| `docs/strategy-v1-schema-to-fsm-mapping.md` | 策略文件欄位 → 引擎狀態機嘅對應 |
| `docs/canonical-market-data-schema.md` | canonical bar 儲存格式 |
| `docs/06-d19-parallel-design.md` | 多策略並行回測嘅三層架構 |
| `TRADING_SPEC_v0.41.md` | **Owner 老師嘅方法原文**。標住 `[課程原文]` 嘅參數**唔准擅改** |
| `apps/web/src/styles/tokens.css` | 三主題實際 token 值嘅唯一真相；舊全站 mock已從現行樹刪除 |

### ⚠️ 狀態特殊

| 檔 | 注意 |
|---|---|
| 舊版 `docs/03` | 🔴 **2026-07-26 已刪除，只可由 git history 追查，唔准引用。** 內容係獨立 P2.5／四卡總覽／部署列表年代 |
| `docs/ui/design-discussions/p2-instrument-closed-loop-v1.*` | 🔴 已刪除。v1 用 checkbox 改 Terminal universe，與批准 v2 衝突 |
| `docs/AGENT_C_HANDOVER.md` | 🔴 已刪除。舊 v1→v2 handover；現役只可用 `docs/AGENT_C_V5_HANDOVER.md` |
| 舊X/Y handover／briefing／channels | 🔴 已從現行工作樹刪除；需要事故審計時只由C從Git history精確取回 |
| `docs/work-orders/`舊X/Y檔 | 🔴 87份已從現行工作樹刪除；active目錄只留現況索引 |
| 舊P6 specs／plans／visuals | 🔴 已從現行工作樹刪除；唔保留第二份可被誤當authority嘅副本 |
| `docs/01-parameter-coverage-matrix.md` | 有效參數溯源參考；UI 位置仍以六份定稿設計稿為準 |
| `docs/04-frontend-design-system.md` | token 系統原則。實作以 `apps/web/src/styles/tokens.css` 為準 |

### 歷史追查

退役文件唔再留喺現行工作樹。Git history仍保存原bytes；只有C遇到具體事故或
legacy contract證據需要時，先按exact commit/path取回單一文件。W唔自行遍歷歷史。

---

## 9. 執行者歷史（知一知就夠）

| 執行者 | 範圍 | 為何離開 |
|---|---|---|
| **B** | WO-001 → 003 | tokens 用完 |
| **A** | WO-003b → 006 6-4 | Owner 評較弱，交接 |
| **Z** | 6-5 → WO-009 第 1 批 | **交付節奏太慢**（唔係質量——佢技術判斷係最強嘅，見下） |
| **Y／Y-v5.5** | WO-009起前端批次；最後P6 A3 correction | 2026-07-31完成transition後退役 |
| **X-v5** | 階段C後端閉環批次；最後P6 A2 correction | 2026-07-31完成transition後退役 |
| **W** | 完整P6 frontend＋backend＋runtime＋contracts＋integration | 現役feature owner；one-shot plan／工作令ready，等Owner短指令轉發啟動 |

**Z 做得好嘅四樣，係現任要繼承嘅標準**：

1. **用證據唔用判斷**——要證明格式化零行為改動，token stream 比對有 6 個檔誤報，佢冇寫「查過係正常行為」就 commit，改用 `ast.dump(ast.parse(...))` 逐個檔再驗
2. **令測試紅一次**——故意把 route 改壞，確認新測試啱啱好一條紅
3. **守門員嘅守門員**——測試 helper 自己都可能寫錯而永遠 pass，所以斷言 helper 一定 throw
4. **主動講唔利於自己嘅事實**——「實際係 12 個檔唔係 11 個，第 12 個係我自己上一批整走樣嘅」

**對執行者嘅期望**：REPORT 裡面「我未做／做唔到／唔確定」嗰部分，比「我做完咗」嗰部分更有價值。

---

## 10. Agent C 嘅審查標準

每份 REPORT 都會：**逐行讀 diff、親手重跑風險相稱嘅focused/protected tests、
做對抗mutation，並由真artifact核實聲稱**——唔係讀報告就算。Full suite按
現行工作令最多一次及同heavy lane錯峰；C唔會只為重複executor數字而再耗一次
全機資源。

實例：執行者聲稱 12 個檔 AST 相等 → Agent C 自己由 git 取兩邊重跑一次逐個比對；執行者聲稱策略注入係真嘅 → Agent C 去讀真 run manifest 核實 `strategy_binding` 有 `content_sha256` 同 overrides 逐欄記錄。

### 四級 REVIEW verdict（Owner 2026-07-28 永久規則）

所有現任及未來 Agent C 都必須用以下最高已證實級別；**禁止普通無級別
`PASS`暗示跨層或整體完成**：

| 級別 | Exact意思 |
|---|---|
| `COMPONENT PASS — <scope>` | 只證明單邊component／frontend behavior／backend function。 |
| `CONTRACT PASS — <seam>` | C逐欄核對producer＋consumer，並以真backend response→strict frontend parser等cross-layer probe證明method/path/schema/identity/error exact。 |
| `INTEGRATION PASS — <flow>` | 無mock／fixture代替該seam；actual frontend runtime實際call registered backend並得到正確UI/state結果。隔離test state仍可，所以未等於真journey。 |
| `TRUE E2E PASS — <journey slice>` | 真browser normal mode→真frontend→真backend→獲批真data/artifact→指定下一站，並有事前事後facts、exact授權寫入及零越權副作用。 |

`DESIGN APPROVED`獨立於工程四級；fixture／mock browser最高只係component；
backend同frontend分開PASS唔會自動變contract/integration；真backend payload
餵parser只係contract；整頁／整體以最低required seam為準。每份REVIEW必列：

1. `Verdict level + exact scope`；
2. `Evidence`；
3. `Not proven／remaining boundary`；
4. correction／next work order／explicit hold；
5. `Overall progress update`：現行MVP百分比／range、今次delta、已通／施工中／
   未通journey、下一個critical gate；如包含IB/runtime嘅最終產品口徑不同要分開報。

歷史plain `PASS`唔追認為integration／E2E。只有全部MVP required journey
edges均有`TRUE E2E PASS`，先可稱「工程閉環」。完整硬分界及例子見
`docs/AGENT_C_V5_HANDOVER.md` §3.1；所有後任C handover必須繼承，未經Owner
書面批准不得刪除或放寬。

**Agent C 亦會出錯，而且錯咗會喺渠道記錄。** 已記錄嘅自我更正包括：一度把符合規格嘅記分卡標題誤判成 bug；一度因為 grep 唔夠深而以為執行者冇記錄 override（差啲用誤判寫 REVIEW）；提議「一律寫 tombstone」後自己收回（過度工程）；commit message 寫錯內容後 amend 修正。

**所以：如果你覺得 Agent C 講錯，喺渠道講。前任捉到五處規格錯誤，每次都改咗規格。**
