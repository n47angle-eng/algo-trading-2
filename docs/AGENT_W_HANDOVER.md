# Agent W zero-context handover — P6 full-stack feature owner

最後更新：2026-07-31
現行狀態：**READY FOR OWNER FORWARD — one-shot plan已完成；未收到Owner短指令前
仍係EXPLICIT HOLD**

> Agent W係P6模擬盤唯一frontend＋backend executor。
> Agent C-v5只做設計、監督及獨立review，唔寫產品code（review臨時mutation除外）。

---

## 0. 開門先讀：舊A4已撤銷

`AGENT_CHANNEL_W.md`內舊`[W-ACTIVATE]` A4：

- Owner從未轉發；
- W從未開始；
- 已由`[W-001]`正式撤銷；
- 只係歷史審計，唔再有permission。

現行設計：

`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`

Owner已批准完整書面規格；C已完成單一完整implementation plan。正式permission
只來自`AGENT_CHANNEL_W.md`嘅`[W-002]`one-shot WORK-ORDER＋最新`[W-004]`
Gateway addendum，並且要由Owner短指令轉發啟動。收到前：

```text
product/test/config/data diff = 0
test/build/server/browser/Gateway action = 0
```

---

## 1. 項目係咩

呢個係Owner個人使用嘅期貨策略研究平台：

```text
Terminal討論策略
→ strategy.v1
→ app驗證及回測
→ result.v1
→ Owner PromotionDecision
→ P6 app自家模擬交易
→ paper-review.v2
→ Terminal衍生新strategy.v1
→ 再走完整流程
```

技術：

- frontend：React 19＋TypeScript＋Vite；
- backend：Python＋FastAPI＋SQLite＋Parquet；
- engine：NautilusTrader＋project shared strategy／conservative execution core；
- data：Interactive Brokers NQ／YM／GC；
- app內零AI API，Terminal同app靠有schema嘅artifacts溝通。

---

## 2. P6永久產品邊界

```text
IB/IBKR Gateway = market data only
App = strategy decision＋simulated order/fill＋virtual account＋position＋PnL＋safety
```

任何情況：

- 唔向IB live account發單；
- 唔向IB paper account發單；
- 唔用IB account portfolio做app ledger authority；
- 唔寫「IB paper trading」；
- 正確說法係「IB行情驅動嘅app自家模擬成交」。

---

## 3. 接受基線

```text
accepted product baseline
7575e66ce668c910ab109eecc7bb9b34373914ee

X final accepted code
88b153d0d04e7a46d40a48cbc56787da4d570046
C [X-147] COMPONENT PASS

Y final accepted code
19ff5ff
C [245] COMPONENT PASS

old W activation commit
369ef838b8054084032a78ea4fa19da0fc882038
```

X／Y已永久退役；佢哋嘅channels、handover及work orders全部只係history。

---

## 4. 而家已有咩

已完成並經C收貨：

- immutable Owner `use` PromotionDecision；
- exact P6 eligible strategy；
- P6 strategy／contract／baseline selection；
- provisioned trader create/list/detail；
- Option A provisioning authorization及preflight components；
- immutable zero-ledger origin；
- review request/status；
- deterministic zero-runtime review artifact；
- strict frontend contracts；
- download／ZIP verification／Terminal opener；
- temp cross-layer producer→consumer evidence。

仍未完成：

- 真IBKR Gateway market-data session；
- local persistent runtime；
- strategy decision live processing；
- simulated order／fill／trade；
- position／PnL；
- pause／resume／permanent stop；
- disconnect／restart recovery；
- runtime safety；
- runtime chart；
- `paper-review.v2`；
- 真default P6 operational journey。

---

## 5. 新完整P6方向

Owner已批准：

- W一個agent一口氣完成整個P6；
- internal atomic commits可以，但唔分stage等C review；
- 最後只交一份`[W-FINAL] REPORT`；
- 真Gateway transport＋real-data deterministic replay hybrid；
- production multi-trader；
- default E2E用一個approved target；
- test-only long／short／no-signal／safety strategies；
- browser關閉時backend繼續；
- Windows／backend重開後Owner手動resume；
- timeframe-agnostic architecture，MVP role capabilities只enable所需1m／30m；
- runtime input／execution MVP係1m，chart display可揀1m／30m；
- immutable strategy timeframes唔由runtime覆蓋；strategy-0003仍係D／1H／5m；
- delayed行情只准清楚標示TEST-DELAYED；
- Telegram移除；
- cloud／Supabase／Tailscale／PWA Push押後；
- exact `data/paper/**` operational write scope。

全部細節只讀：

`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`

施工plan：

`docs/superpowers/plans/2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md`

---

## 6. IBKR Gateway資料

Owner使用IBKR Gateway (Simulated Trading)：

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
```

- Gateway `Read-Only API`要保持tick；
- `Master API client ID`留空；
- client ID 7由app連線時提交；
- 唔需要Owner將client ID寫入Gateway畫面；
- app唔讀／存IB credentials；
- `8876`唔係IB port。

而家仍係HOLD，收到Owner短指令前唔連線、唔關閉或控制Owner Gateway。

---

## 7. Default operational target

```text
strategy
strategy-0003

contract
NQ-202609-CME

baseline
nq-20260728-standard-365adf
```

正常true journey只用呢個target。四個test-only strategies只准存在OS-temp
replay harness，唔可寫normal catalog、PromotionDecision、default P6 DB或Owner UI。

---

## 8. Protected facts

截至X/Y transition accepted evidence：

```text
data/ files                 790
data/ bytes                 34,849,558
runs.sqlite3 bytes          4,157,440
runs.sqlite3 SHA            1e102c3c…b8f04012
promotion DB bytes          24,576
promotion DB rows           exact 1 immutable use
promotion DB SHA            5b340c32…9977ed79
default data/paper/         absent
Owner residue               ?? _to_delete/
```

新工作令會重新記exact full SHA及live process facts。永久：

- `_to_delete/`唔讀／改／stage／restore；
- unknown dirty file唔stash／checkout／reset／clean；
- protected data先before inventory，後after exact comparison。

---

## 9. Current文件讀法

### Active

- `docs/PROJECT_STATE.md`
- `docs/AGENT_W_HANDOVER.md`
- `AGENT_CHANNEL_W.md`
- `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`
- `docs/superpowers/plans/2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md`
- accepted source及tests

### Retired material

舊P6 specs／plans／visuals、X/Y work orders／handover／channels已從現行工作樹
刪除，Git history仍可恢復。W唔自行取回或閱讀；如current source/test明確需要
一個legacy contract，先喺`AGENT_CHANNEL_W.md`出`QUESTION`，由C精確提供。
歷史材料永遠唔係permission。

---

## 10. C同W點溝通

- 唯一正式channel：`AGENT_CHANNEL_W.md`；
- C寫`STATUS／WORK-ORDER／REVIEW`；
- W寫`QUESTION／REPORT`；
- Owner只會轉發一段短指令，長scope只喺file channel；
- 新訊息放channel「訊息紀錄」最頂；
- W最後先commit code/tests，再append及另commit`[W-FINAL] REPORT`；
- REPORT後EXPLICIT HOLD；
- C review必回correction、next work order或explicit hold，並更新overall progress。

---

## 11. One-shot mission stop conditions

新工作令生效後，W只可以因以下原因中途HOLD：

1. Owner要處理Gateway登入／API confirmation；
2. accepted baseline／protected facts唔一致；
3. 規格衝突會影響truth、安全或寫入；
4. 發現任何IB order possibility；
5. 必須超出Owner批准write scope。

一般bug、test failure、需要frontend/backend一齊修或實作較長，全部由W自行繼續，
唔係停工理由。

---

## 12. 現在你要做咩

```text
EXPLICIT HOLD
```

唔好開始舊A4，唔好由spec／plan自行開工。等Owner轉發短指令，然後按
`AGENT_CHANNEL_W.md`嘅`[W-002]`單一完整WORK-ORDER＋`[W-004]`Gateway addendum
一口氣完成整個P6。
