# CLAUDE.md — Claude Agent 開工指引

> 建立：2026-07-28（Agent C-v4）· 最後更新：2026-07-31（X/Y transition
> 已收貨；W成為單一P6 feature owner）。本檔係**入口索引同行為守則摘要**，唔係新權威：
> 任何內容同下面權威文件或 Owner 最新指示有衝突時，**以權威文件同 Owner 為準，
> 並要喺渠道提出修正本檔**。

## 0. 你係邊個角色？

現行採用「一個Agent負責一個完整功能」。P6模擬盤由W跨前後端負責；開工前先
確定自己角色（睇Owner指示／prompt）：

| 角色 | 職責 | 唯一渠道 | 入口文件 |
|---|---|---|---|
| **Agent C-v5**（設計／監督／審查） | 同Owner拍板、寫工作令、獨立審W交付；**唔寫產品code**（臨時mutation probe除外） | `AGENT_CHANNEL_W.md` | `docs/AGENT_C_V5_HANDOVER.md` |
| **Agent W**（P6 full-stack executor） | 擁有完整P6 Python＋React＋runtime＋contracts＋integration＋evidence；按最新one-shot工作令一次完成 | `AGENT_CHANNEL_W.md` | `docs/AGENT_W_HANDOVER.md` |
| **Agent X／Y**（retired） | 最後A2／A3 corrections已由C `[X-147]`／`[245]`收貨；冇新P6工作 | 舊channels已從現行樹刪除 | 歷史只可由C按需要從Git history精確取回 |

舊`[W-ACTIVATE]`A4從未開始，已由`[W-001]`撤銷。W只可由
`AGENT_CHANNEL_W.md`最新one-shot WORK-ORDER加Owner短指令轉發開工。
任何executor只可以按自己channel最新正式工作令開工；六頁定稿、
spec或plan都唔係自行開工權。

## 1. 必讀次序（所有角色共通核心）

1. 自己角色嘅現役handover（上表）；
2. `docs/PROJECT_STATE.md` —— 🔴 現況、五個踩過嘅坑、五樣唔准改；
3. P6角色完整讀
   `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`及
   `docs/superpowers/plans/2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md`；
4. `docs/ui/designs/README.md`＋P1–P5現行 `.html`；P6舊HTML已從現行樹刪除；
5. `docs/03-frontend-component-coverage-matrix.md`（驗收防漏，唔可反改設計）；
6. `docs/08-ui-backend-mapping.md`（UI↔後端供應）＋
   `docs/09-journey-closed-loop-audit.md`（B1–B8 閉環）；
7. `docs/05-file-contract-schemas-draft.md`（檔案合約權威）＋
   `docs/00-design-baseline-conclusions.md`（D1–D20）；
8. 自己渠道最新訊息（由最新讀落去到最近一份WORK-ORDER／REVIEW）。

退役handover、work orders、channels、舊P6規格及視覺稿已從現行工作樹刪除。
只有C做事故審計時，先可從Git history精確取回。

## 2. 權威順序（狀態衝突時）

```text
最新 Owner 決定
→ 現役feature channel最新正式WORK-ORDER／REVIEW
→ 現行P6 design＋implementation plan（P6範圍）
→ P1–P5權威設計稿最後「實作約束」表
→ docs/05 文件合約
→ PROJECT_STATE／briefing／matrix
→ C按需要由Git history精確取回嘅歷史證據
```

## 3. 硬規則（每條背後都有真實事故，見 PROJECT_STATE §4）

1. **渠道只准 append**：新訊息加喺「訊息紀錄」最頂，歷史不改。
   **commit 前必跑 `git diff --numstat -- <渠道檔>`**——刪除數必須等於你刻意
   改嘅行數（executor REPORT-only 必須係 0）；多咗＝工具背後重寫咗檔案，
   要由 HEAD 還原重做。長檔用 binary-safe append，唔好信編輯工具。
2. **出 REPORT 前必須先 git commit 交付物**；冇 commit 當未交貨。
3. **時間三分類**：時刻（可轉時區、必標時區）／交易日（**絕對唔准轉時區**）／
   範圍輸入（本地 picker＋UTC 對照，送後端一定 UTC）。要有兩個時區同日測試。
4. **令測試紅一次**：永遠綠嘅測試＝冇測試。每批要有 mutation 證據
   （改壞 production source → 指定測試 RED → 還原 GREEN），唔准只寫
   「理論上會紅」。
5. **用證據唔用判斷**：聲稱要由真 artifact／真 ASGI／真 browser 核實；
   test count 唔係行為證據。REPORT 入面「我未做／做唔到／唔確定」比
   「我做完咗」更有價值。
6. **fail-closed**：loading／error／unknown 一律唔准當 known-zero 或放行；
   任何 failure path 要證明 zero visible write。
7. **UI 唔准出現技術詞**（`batch`／`runs`／run id／`p50 閘`／`rth`／`eth`…）；
   禁用表喺 `docs/ui/designs/p4-backtest.html`，適用全部頁面。UI 亦唔准聲稱
   自己做唔到嘅嘢（IB 只可寫「port 可達 · 未驗證 session」）。
8. **artifact 不可變**：`runs`／`trades` immutable triggers、legacy strategy／
   sketch／run／result bytes 一律唔改寫、唔補假 lineage、唔開寬鬆 validator。
9. **執行者唔准改設計稿、docs/05或工作令以外範圍**；W雖然跨前後端擁有P6，
   仍只改one-shot plan批准嘅P6 source/tests/contracts/docs。發現規格矛盾→渠道列證據、兩種讀法、
   建議，等C／Owner裁決，唔准靜靜哋估。
10. **未經 Owner 明確批准唔准**：重跑真 main DB migration／Activation Gate或
    超出工作令真寫入。2026-07-31 Owner已批准W one-shot P6嘅真IBKR Gateway
    market-data、app自家simulation runtime、真server/browser及exact
    `data/paper/**` default operational write；**仍然零IB order permission**。
    X/Y A2/A3 corrections已由C `[X-147]`／`[245]`收貨並退役。Exact範圍以
    current design／plan／channel最新工作令為準；P3→P4、cloud、PWA、Push、
    Telegram及其他`data/**`仍未授權。
11. **C每份executor REVIEW必附整體進度**：現行批准MVP百分比／窄range、今次
    delta、已通／施工中／未通journey、下一個critical gate；active但未經C
    REVIEW嘅工作唔計完成。包含IB/runtime嘅最終產品如係另一口徑，必須分開報。
    詳見`docs/AGENT_C_V5_HANDOVER.md` §3.3及`docs/PROJECT_STATE.md` §2.1。
12. **IB只係市場數據來源，唔係模擬券商**：app自家模擬order/fill、虛擬帳戶、
    持倉、PnL、斷路器及偏離；不得向IB live或paper account發單。禁止寫
    「IB paper trading／IB模擬成交」，統一寫「IB行情驅動嘅app自家模擬成交」。
13. **Timeframe角色唔可以混**：MVP market input／execution係typed 1m，
    chart display可揀1m／30m；immutable strategy-0003仍係D／1H／5m。30m
    display唔准覆蓋策略decision timeframe；future intervals靠capability擴展。

## 4. 五樣睇落應該改但唔准改（詳見 PROJECT_STATE §5）

16 個歷史回測 0 成交唔係 bug／記分卡「八維度」11 chips 係 8＋3 子項／
IB 狀態只寫 port 可達／P3 下載只出 terminal 指令／placeholder 誠實空白。

## 4.5 系統說明書：改功能就要一齊改（Owner 2026-07-31 指示）

App 內置一本《系統說明書》，路由 `/guide`，內容喺
`apps/web/src/guide/content.ts`（有目錄、章節、搜尋，像電子書）。

**硬規則**：任何 agent／人只要改動到頁面、分頁、控制項、流程、檔案合約或
journey 步驟，**必須喺同一次改動入面更新對應章節**。冇說明書條目嘅功能＝未做完
嘅功能。新頁面要開新章節；唔准淨係刪章節而唔補返解釋咗嘅嘢。

寫法跟返本檔 §3 硬規則 7 同 §5：**廣東話＋繁體中文**、用平實頁面名
（總覽／策略工作台／數據／回測／結果／模擬盤／日內模擬／設定），
**唔准出現 P1–P6 內部代號同 UI 本身都唔准顯示嘅技術詞**。

同一次改動亦要同步 UI 內嘅說明氣泡（`InfoButton`）——每個頁面標題同每張功能卡
右邊嗰個圓形「i」掣，係同一份說明嘅短版；說明書係長版。

## 5. 溝通約定

- Owner 人手轉達所有訊息：寫嘢要**精簡、結論行先**，最後附可直接轉發嘅
  **短指令**。長背景、工作令及review寫入現役file channel；P6用
  `AGENT_CHANNEL_W.md`。
- 同 Owner 傾嘢用**平實廣東話頁面名**（總覽／策略工作台／數據／回測／結果／
  模擬盤），唔用 P1–P6 內部代號或技術術語。
- 頁面／版面要 Owner 拍板嘅嘢，必須先交 HTML＋PNG 討論圖（project＋Desktop
  各一套），唔准齋文字叫 Owner 想像；未拍板唔改權威稿、唔發 executor 指令。

## 6. 常用驗證命令

Backend（repo root）：

```text
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Frontend（`apps/web/`）：

```text
npm run test / typecheck / lint / build
```

真 data 基線每批由最新渠道before facts作authority；現行已知係`data/`
790 files／34,849,558 bytes、`runs.sqlite3` SHA
`1e102c3c…b8f04012`、PromotionDecision exact一個immutable `use` row，
default `data/paper/` absent。唔准用舊count覆蓋新現況。
