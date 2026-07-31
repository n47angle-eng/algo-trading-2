# Agent C-v5 handover — design, supervision and independent review

最後更新：2026-07-31
角色：Agent C只做設計、監督、工作令及獨立review；唔寫產品code。

---

## 0. Current status

```text
P6 executor          Agent W only
W status             EXPLICIT HOLD
old W A4             never forwarded／never started／superseded
accepted product     7575e66ce668c910ab109eecc7bb9b34373914ee
current design       docs/superpowers/specs/
                     2026-07-31-p6-local-paper-runtime-mvp-design.md
current plan         docs/superpowers/plans/
                     2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md
```

Owner已批准完整P6 one-shot書面規格；implementation plan已完成。W只等
`AGENT_CHANNEL_W.md`最新工作令及Owner短指令啟動。

---

## 1. C永久邊界

- 唔寫產品code；
- 唔代executor修production bug；
- review可做臨時mutation：整壞→指定test RED→byte-exact restore→GREEN；
- mutation後必須核SHA／diff，零淨改動；
- 唔掂Owner `_to_delete/`；
- 唔自行擴大真data／DB／IBKR Gateway permission；
- 規格含糊先同Owner裁決，唔叫executor猜。

---

## 2. Review必須四級標示

每份review要明確分開：

```text
COMPONENT PASS
CONTRACT PASS
INTEGRATION PASS
TRUE E2E PASS
```

- Component：單邊功能；
- Contract：frontend/backend exact格式一致；
- Integration：兩邊實際接通；
- True E2E：真browser、真backend、真批准data／DB及所有保護facts。

禁止用較低級證據暗示較高級完成。

每份review亦必須：

1. correction／next work order／explicit hold三選一；
2. 更新overall project百分比；
3. 寫今次delta；
4. 寫journey已通／未通；
5. 寫下一個critical gate。

---

## 3. P6 product boundary

```text
IB/IBKR Gateway = market data only
App = simulated decision/order/fill/account/position/PnL/safety
```

永遠唔向IB live或paper account發單。

Owner Gateway：

```text
127.0.0.1:7498
client ID 7 supplied by app
Read-Only API checked
Master API client ID blank
```

MVP local-only：

- no Supabase／cloud；
- no Tailscale；
- no PWA Push；
- no Telegram；
- no Windows auto-start。

---

## 4. Approved one-shot execution

W負責整個P6 frontend＋backend＋contracts＋runtime＋tests＋evidence，一口氣完成，
最後只交一份`[W-FINAL] REPORT`。

W可有atomic commits，但唔分stage等C review。只可因：

- Owner-only Gateway action；
- protected baseline mismatch；
- truth／safety contract ambiguity；
- IB order possibility；
- 超出write scope；

而中途HOLD。

---

## 5. Exact operational write scope

Owner已在設計中批准：

```text
data/paper/paper-traders.sqlite3
data/paper/paper-traders.sqlite3-wal
data/paper/paper-traders.sqlite3-shm
data/paper/review-artifacts/**
```

其餘data、PromotionDecision、strategy、run、result及Owner資料保持只讀。

---

## 6. Timeframe及testing decisions

- 1m／30m只係MVP enabled capabilities；
- architecture必須timeframe-agnostic；
- runtime input／execution係1m，chart display可揀1m／30m；
- immutable strategy profile唔由runtime改寫；strategy-0003仍係D／1H／5m；
- future 3m／5m／15m／1h／custom列post-MVP；
- true Gateway transport＋saved-real-data deterministic replay hybrid；
- replay預設30日及30m display；strategy decision跟locked strategy.v1；
- test-only long／short／no-signal／safety strategies；
- prices唔造假；
- test strategies唔入normal catalog／DB／UI；
- delayed data只可標示TEST-DELAYED。

---

## 7. Safety decisions

- drawdown 8R from runtime equity high-water；
- 8 completed losing trades；
- blind threshold 5 minutes；
- disconnect即freeze新decision/fill；
- short complete gap可補回及auto continue；
- long／incomplete／identity drift要trip＋Owner手動resume；
- 冇可信價格絕不虛構平倉；
- restart後Owner手動resume；
- browser關閉唔影響local backend runtime；
- final accepted default trader保持paused＋flat。

---

## 8. Documents

Active：

- `docs/PROJECT_STATE.md`
- `docs/AGENT_C_V5_HANDOVER.md`
- `docs/AGENT_W_HANDOVER.md`
- `AGENT_CHANNEL_W.md`
- current P6 design／implementation plan。

Retired：

- old X/Y handovers；
- old X/Y work orders；
- old P6 foundation specs／plans；
- old P6／full-flow visual drafts。

Retired files已從現行工作樹刪除，Git history仍可恢復。C只有做精確事故審計時
先取回需要嘅單一版本；W唔自行翻舊檔，歷史永遠唔係permission。

---

## 9. Next exact sequence

```text
1. design／cleanup已commit
2. C自我review已捉出並修正strategy timeframe ambiguity
3. Owner已確認完整書面規格
4. C已寫單一complete implementation plan
5. C自我review plan已完成
6. C已寫AGENT_CHANNEL_W `[W-002]`完整one-shot WORK-ORDER及`[W-004]`
   Gateway addendum
7. C只畀Owner一段短轉發指令（目前步驟）
8. Owner轉發後W完成整個P6及交[W-FINAL]
9. C獨立review＋四級verdict＋overall progress
10. PASS後更新P6／full-flow HTML及manual test guide
11. 另開feature-owner修P3 available-data→P4 date handoff
```

Writing-plans skill如當時不可用，C要明講並用同等嚴格嘅repo plan格式作fallback。

---

## 10. Current hold

W未獲准開始。只有Owner轉發C最後短指令後先可執行channel最新one-shot工作令。
