# Agent W Channel — P6 模擬盤唯一 full-stack executor

## 使用規則

1. 本檔係W同C就P6模擬盤嘅唯一正式渠道。
2. C寫`WORK-ORDER／REVIEW／STATUS`；W寫`REPORT／QUESTION`。
3. 新訊息加喺「訊息紀錄」最頂；歷史不改。
4. W先commit產品／tests，再append及另commit REPORT；REPORT-only必須
   additions、0 deletions。
5. 每份REPORT後W進入EXPLICIT HOLD；C必回correction、next work order或hold。
6. `AGENT_CHANNEL_X.md`／`AGENT_CHANNEL.md`只係transition／歷史證據，W唔改。
7. 未有C最新正式工作令，唔准由spec／plan／roadmap自行開下一批。
8. IB market-data、default P6 write及app runtime各自要Owner exact批准；Owner已為
   最新`[W-002]`批准三者。**IB live／paper order永遠零permission。**
9. `_to_delete/`永遠唔讀／唔改／唔stage／唔restore。

## 狀態板

| 項 | 狀態 |
|---|---|
| 當前階段 | 🟡 **READY FOR OWNER FORWARD — `[W-002]`完整P6 one-shot；W未開始** |
| W角色 | P6 frontend＋backend＋contracts＋integration唯一full-stack feature owner |
| Accepted transition | C `[X-147]`／`[245]`已收貨；X/Y channels永久read-only |
| 目前權限 | Owner轉發短指令前仍係零實作；轉發後`[W-002]`一次過授權完整P6 |
| 下一交付 | W完成全部P6後一次性交`[W-FINAL] REPORT`；中途唔等C逐stage review |
| 禁止 | 未收到Owner短指令前零執行；開始後仍禁止IB order、scope外data、P3→P4、cloud／PWA／Telegram |

## 訊息紀錄（新喺上）

### [W-004] 2026-07-31 [C-v5→W] OWNER DECISION／WORK-ORDER ADDENDUM — **IBKR Gateway正式取代TWS；`[W-002]`其餘scope不變**

Owner已批准現行 **IBKR Gateway (Simulated Trading)** 作P6唯一真行情transport。
本addendum取代`[W-002]`及舊channel內所有「TWS」連線字眼：

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
provider=IBKR Gateway (Simulated Trading)
API=Read-Only
Master API client ID=blank
```

Client ID 7由app連線時提交，Owner唔需要喺Gateway畫面填。Gateway只供market
data；app擁有strategy decision、simulated order/fill、virtual account、
positions、PnL及safety。**任何IB live／paper order、cancel、modify或account
portfolio authority仍然零permission。**

W要使用Owner已開、已登入嘅Gateway，唔讀credentials，唔關閉或控制Owner
Gateway；只可清理自己建立嘅client session。真transport證據、preflight、
disconnect recovery、cleanup及REPORT凡寫「TWS」者全部改以Gateway為準。

`[W-002]`其餘產品scope、one-shot施工、write boundary、tests、mutation、
TRUE E2E evidence及`[W-FINAL] REPORT`要求完全不變。

### [W-003] 2026-07-31 [C-v5→W] STATUS／DOCUMENT CLEANUP ADDENDUM — **退役文件已從現行工作樹刪除；`[W-002]`產品scope不變**

Owner明確要求唔保留會令future agent混淆嘅舊文件。現行工作樹已刪除：

- `docs/archived/`全部退役資料；
- `AGENT_CHANNEL_X.md`；
- `AGENT_CHANNEL.md`。

Git history仍保存原bytes。W唔自行取回或閱讀；如current source/test明確需要
legacy contract，先喺本channel出`QUESTION`，由C按exact commit/path提供。

本清理**不改**`[W-002]`產品scope、permission、required evidence或one-shot
交付方式。Owner短指令須列出包含本清理嘅最新activation commit；收到前仍HOLD。

### [W-002] 2026-07-31 [C-v5→W] WORK-ORDER — **完整P6 local app-owned paper runtime；one-shot全棧交付**

## 1. 生效方式

本工作令已寫好，但只喺Owner將C提供嘅短指令（包含最新activation commit）轉發
俾W後生效。未收到短指令：保持EXPLICIT HOLD，零執行。

收到後：

```text
W一個agent
→ frontend＋backend＋runtime＋contracts＋tests＋integration
→ 一口氣完成
→ 最後先交[W-FINAL] REPORT
→ EXPLICIT HOLD等C獨立review
```

唔分A4／A5／A6，唔逐stage等C。

## 2. Required ancestors及開工facts

逐個跑`git merge-base --is-ancestor`，exit必須0：

```text
7575e66ce668c910ab109eecc7bb9b34373914ee  accepted product baseline
88b153d0d04e7a46d40a48cbc56787da4d570046  X final accepted code
19ff5ff                                          Y final accepted code
6091dce6cae8c1577bb4924fbd778d1cd6e8e088  design／cleanup baseline
01e86fe582ddd5c413872a695c1d9c104ca5cf26  final design correction＋implementation plan
```

再核Owner短指令所列activation commit係HEAD ancestor。開工預期：

```text
tracked diff 0
staged diff  0
allowed only ?? _to_delete/
```

任何祖先唔成立或有其他dirty/staged檔：append `QUESTION`，HOLD。唔stash／reset／
checkout／clean；`_to_delete/`唔讀／改／stage／restore。

## 3. 必讀次序

由零context完整讀，唔跳段：

1. `CLAUDE.md`；
2. `docs/AGENT_W_HANDOVER.md`；
3. `docs/PROJECT_STATE.md`；
4. 本channel使用規則、狀態板及最新`[W-002]`；
5. `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`；
6. `docs/superpowers/plans/2026-07-31-p6-local-paper-runtime-mvp-implementation-plan.md`；
7. `docs/00-design-baseline-conclusions.md`；
8. `docs/01-parameter-coverage-matrix.md`；
9. `docs/02-mvp-closed-loop-user-journey.md`；
10. `docs/03-frontend-component-coverage-matrix.md` P6及cross-page sections；
11. `docs/05-file-contract-schemas-draft.md` relevant strategy/result/paper sections；
12. `docs/08-ui-backend-mapping.md` P6；
13. `docs/09-journey-closed-loop-audit.md` P6；
14. current P6 backend/frontend source及direct tests；
15. shared strategy／MTF／execution source及tests；
16. current IB historical adapter／contracts／calendar source及tests。

`docs/archived/**`、`AGENT_CHANNEL_X.md`、`AGENT_CHANNEL.md`只係history；除非current
source test明確引用一個exact legacy contract，否則唔讀，絕不當permission。

## 4. 唯一產品邊界

```text
TWS/IB = market data only
App = strategy decision＋simulated order/fill＋virtual account＋position＋PnL＋safety
```

任何IB live／paper order、cancel/modify、account portfolio authority都係禁止。
產品文案只可講「IB行情驅動嘅app自家模擬成交」。

TWS：

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
TWS Simulated Trading
Read-Only API checked
Master API client ID blank
```

Client ID 7由app提交，Owner唔需要喺TWS填。唔讀credentials，唔關Owner TWS。

## 5. 完整scope

必須完成plan全份，包括：

- typed role-aware timeframe capability；
- strategy-0003 immutable D／1H／5m profile；
- 1m input/execution＋1m／30m display；
- store v4、append-only evidence、lease、request recovery；
- multi-trader market-data bus及independent state；
- TWS read-only adapter＋truthful mode；
- warmup／activation boundary；
- shared strategy／ConservativeExecution runtime；
- simulated decisions/orders/fills/trades；
- virtual account／position／PnL；
- lifecycle及manual restart recovery；
- disconnect／gap rules；
- 8R／8-loss／5-minute safety；
- runtime preflight／commands／snapshot／timeline／chart APIs；
- strict TS consumers、polling/race guards及runtime UI；
- deterministic saved-real-data replay four paths；
- `paper-review.v2` exact 15-member ZIP＋opener；
- registered cross-layer contract；
- true TWS transport；
- true browser＋default DB operational journey。

Telegram product code／UI/runtime prerequisite要移除；只可保留明確命名、只讀、
有test嘅legacy v1 parser。唔加PWA、Push、Tailscale、Supabase、cloud、Windows
auto-start或P3→P4日期handoff。

## 6. Timeframe防錯

MVP：

```text
market_input=1m
execution=1m
chart_display=1m or 30m
strategy profile from strategy.v1
strategy-0003=D／1H／5m
```

30m係display選項；30日係replay窗口。禁止將strategy decision改成30m。
Frontend由backend capability讀，唔維護第二份永久allowlist。

## 7. True write authorization

Owner已批准本工作令exact寫：

```text
data/paper/paper-traders.sqlite3
data/paper/paper-traders.sqlite3-wal
data/paper/paper-traders.sqlite3-shm
data/paper/review-artifacts/**
```

批准final gate建立exact一個default trader及其runtime/review evidence。其餘
`data/**`、PromotionDecision、strategies、runs、results、baseline source全部
read-only。Test/replay只用OS-temp。

開真write前做plan §1.3 protected BEFORE；收工做AFTER。Expected delta以外exact 0。

## 8. Default target及final state

```text
strategy  strategy-0003
contract  NQ-202609-CME
baseline  nq-20260728-standard-365adf
display   30m
```

Final：

```text
default trader paused
pending intent 0
open position 0
W-owned backend/Vite/browser/TWS client/temp 0
Owner TWS untouched
```

真TWS target唔需要自然產生trade；transport由真TWS證，fills/safety由真IB
saved-data replay證。唔長時間等自然signal，唔造price。

## 9. One-shot執行及stop conditions

W唔開其他coding agents。可按plan做atomic commits，但唔提交interim REPORT要求
C收貨。一般bug、test failure、cross-layer mismatch自行繼續。

只可中途HOLD：

1. Owner要處理TWS登入／API confirmation；
2. accepted baseline／protected facts不一致；
3. current design／plan有truth、安全或write矛盾；
4. 發現任何IB order possibility；
5. 必須超出§7 write scope。

非P6且唔阻MVP嘅bug只記post-MVP backlog，唔修。

## 10. Tests／mutation／資源

完整跟plan §§13–18：

- TDD；
- focused backend/frontend；
- strict ASGI producer→production TS consumer；
- saved-real-data replay；
- true TWS；
- true browser；
- backend full exact一次；
- frontend full exact一次；
- ruff／mypy／typecheck／lint／build／diff-check；
- plan列出20類mutation逐項RED→byte-exact restore→GREEN。

Backend full、frontend full、replay、browser、TWS heavy lane串行。只終止自己啟動
process；temp/profile/artifact用完清理。安全政策拒絕刪除就記exact path/bytes，
唔危險強刪。

## 11. Evidence級別

REPORT要分開：

```text
COMPONENT
CONTRACT
INTEGRATION
TRUE E2E
```

Mock、ASGI、replay、真TWS、真browser、default DB各自講清，唔互相冒充。
Delayed行情只可wire=`test_delayed`、UI=`TEST-DELAYED`，唔聲稱real-time entitlement。

## 12. 最終REPORT

產品／tests先commit。完成全部scope後，將`[W-FINAL] REPORT`加喺本channel
「訊息紀錄」最頂，再另commit；REPORT-only channel numstat additions／0 deletions。

必須逐項交plan §19全部facts，尤其：

- ancestors／atomic commits／exact file scope；
- four-level verdict；
- commands／counts／durations；
- mutation RED／restore SHA／GREEN；
- TWS host／port／client／mode／callback；
- IB order／cancel／account-order exact zero；
- replay四路；
- browser request counts；
- v4 DB tables／rows／events；
- v2 ZIP 15 members／bytes／SHA；
- default expected delta；
- protected BEFORE／AFTER；
- ports／processes／temp cleanup；
- 所有不利事實及未證；
- EXPLICIT HOLD等C獨立review。

### 生效前最後一句

未收到Owner短指令：HOLD。

收到Owner短指令並確認activation commit祖先：**立即開始完整P6，唔再等X、Y或C
逐stage指示。**

### [W-001] 2026-07-31 [C-v5→W] STATUS — **舊A4撤銷；完整P6 one-shot設計等待書面確認**

Owner確認舊`[W-ACTIVATE]`從未轉發、W從未開始，所以該A4工作令由本訊息正式撤銷。
下面A4全文只係歷史審計，唔再有permission。

現行設計候選：

`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`

Owner已逐段批准，但仍要完成書面規格review。W保持EXPLICIT HOLD，唔讀／改產品、
唔跑test/server/browser、唔接TWS、唔寫default P6，直至C喺本channel加入一張
取代A4/A5/A6嘅單一完整WORK-ORDER。

### [W-ACTIVATE] 2026-07-31 [C-v5→W] WORK-ORDER — **A4 Option A全棧cross-layer contract gate；立即開始**

## 1. Activation facts

```text
activation docs baseline
8d0ee7faea3824fa1c20eca5489f7936e3dc759d

accepted product baseline
7575e66ce668c910ab109eecc7bb9b34373914ee

C reviews
[X-147] accepted — A2 backend COMPONENT PASS
[245]   accepted — A3 frontend COMPONENT PASS

expected tracked／staged diff
0

allowed Owner residue
?? _to_delete/
```

開工先逐項以`git merge-base --is-ancestor`及status核實。任何required SHA唔係
ancestor，或者除`_to_delete/`外有未知dirty/staged檔：出`QUESTION`＋HOLD，
唔stage／restore／stash／checkout／clean。

## 2. 必讀及ownership

由零context開始，完整依`docs/AGENT_W_HANDOVER.md` §2次序讀全部文件，再讀：

1. 本channel使用規則、狀態板及本工作令；
2. X `[X-146]`＋C `[X-147]`；
3. Y `[244]`＋C `[245]`；
4. 現行P6 backend/frontend source、registered routes、dependency injection及
   directly relevant tests。

四份退役X/Y handover／briefing已移到`docs/archived/`，唔係必讀或authority。
由今批開始，P6任何backend或frontend blocker都由W喺後續C correction order
自行修，唔再交返X/Y；但**本A4係零tracked-change驗證批**，發現差異先REPORT。

## 3. 唯一目標及最高候選verdict

用**registered FastAPI真producer response bytes**，經
**production TypeScript client／strict parser／ZIP verifier**原樣消費，核實
Option A provision-only完整wire。唔准抄payload做第二份fixture authority，
唔准用Python model對Python model或TS fixture對TS parser冒充跨層。

最高候選只係：

```text
CONTRACT PASS — P6 Option A provision-only producer ↔ production consumer
INTEGRATION／TRUE E2E — NOT VERIFIED
```

本批冇TCP listener、Vite、browser、default DB或IB，所以即使全部通過都唔可
聲稱INTEGRATION／TRUE E2E。

## 4. Exact route matrix

逐條記method、path、query/body exact keys、status、headers、raw body bytes、
frontend入口、parsed identity及side effects：

```text
POST /api/v1/paper/provisioning-readiness
POST /api/v1/paper/traders
GET  /api/v1/paper/trader-requests/{request_id}
GET  /api/v1/paper/traders
GET  /api/v1/paper/traders/{trader_id}
GET  /api/v1/paper/traders/{trader_id}/ledger-origin
POST /api/v1/paper/traders/{trader_id}/review-snapshots
GET  /api/v1/paper/review-requests/{request_id}
GET  /api/v1/paper/review-snapshots/{snapshot_id}/download
GET  /api/v1/paper/review-snapshots/{snapshot_id}/terminal-opener
```

Production consumer request必須證明：

- mounted strategy／contract／baseline exact identity；
- create body exact `{schema, request_id, selection}`，零internal authorization欄；
- review body exact現行public schema；
- each explicit owner action request exact 1、UUID exact 1、auto retry 0；
- stale／cross-identity response 0 adopt、0 Blob、0 clipboard claim。

## 5. Required happy-path evidence

只用OS-temp v3 paper store／artifact root；default runs／PromotionDecision／
strategy/result可read-only引用，唔複製後改寫成另一個truth。

最少證明：

1. Atomic provisioning preflight以canonical public`schema`回200；baseline ready；
   external IB/calendar/Telegram可truthful blocked／unknown；authorization armed；
   `can_provision` exact iff規則成立。
2. First create exact一個201；同request UUID＋payload SHA＋selection replay exact
   一個200，同一trader/account，零第二record。
3. Request lookup、list、detail identity exact一致；persisted readiness snapshot
   可以`overall=blocked`，但必須等於四check derived truth。
4. Ledger origin exact zero-trade profile：initial capital/balances、HWM、events、
   positions/orders/safety/readiness全部同persisted creation truth reconcile。
5. Review create exact一次；如producer係202先按explicit status GET查到ready，
   唔auto retry POST；request/snapshot/trader/run identities全程同一。
6. Download response status/content type/`Content-Disposition`同CORS exposure可由
   production consumer讀到；whole bytes/SHA及ready claim exact。
7. ZIP exact十個ordered STORE members；1980-01-01 timestamp、mode 0600、
   local/central extra及comments exact zero；逐member path/bytes/SHA exact。
   **按現行批准邊界唔新增frontend CRC32 authority。**
8. Terminal opener係backend persisted原文；text／UTF-8 bytes／SHA同ready claim
   及download member exact，frontend零template expansion。

## 6. Required fail-closed／error evidence

至少逐一用registered route真response餵production consumer：

```text
503 external_readiness_invalid
503 provisioning_not_authorized
409 provisioning_selection_mismatch
409 provisioning_request_conflict
422 schema_version-only／unknown-or-extra request shape
unknown status／unknown code／wrong content-type
stale request_id／snapshot_id／trader_id／selection
download or opener bytes／SHA／header／member drift
```

每項記：

```text
exact HTTP status＋raw envelope
frontend known-human-copy或unknown fail-closed分類
POST／GET count
temp DB／artifact delta
Blob／createObjectURL／click／clipboard count
retry count
```

Known failure唔准顯示raw engineering message；unknown唔准假成功。所有失敗路徑
必須zero unintended visible write。

## 7. Harness及資源邊界

批准：

- `httpx.ASGITransport`或同等**in-process registered FastAPI** request；
- OS-temp獨立DB／artifact／evidence root；
- 將backend raw status／headers／body bytes交俾Node runner；
- bundle/import現行production TS modules後原樣消費；可用temp runner，
  唔准copy parser／validator；
- 串行focused preflight；frontend最多2 workers；**唔跑backend/full或frontend/full**。

禁止：

- tracked product/test/doc/config/package/lockfile變更；
- localhost TCP server、Vite、browser或真clipboard；
- default `data/paper/`、default review artifact、真decision或其他真寫入；
- 重跑migration、activation smoke、batch、backtest、真export；
- IB/TWS/IBC/Telegram、runtime、order/fill/trade/position/PnL；
- 用environment flag、frontend token或fake-ready繞過server-owned authorization；
- 讀／改／stage／restore／刪除`_to_delete/`；
- 終止非自己啟動process。

Temp runner/evidence收工前按exact path清理；若OS／安全政策拒絕，唔強刪，
列path、bytes、原因。

## 8. Protected BEFORE／AFTER

兩次snapshot都要列：

```text
data/ file count＋total bytes
runs.sqlite3 bytes＋SHA
promotion-decisions.sqlite3 bytes＋SHA＋exact immutable row count
default data/paper/及DB/-wal/-shm／review-artifacts presence
tracked／staged diff
5173／8000／8876／7498 listeners
IB Gateway／TWS／IBC／Telegram processes
owned temp root／process residue
```

Expected baseline係790 files／34,849,558 bytes、runs
`1E102C3C…B8F04012`、PromotionDecision `5B340C32…9977ED79`、
default P6 absent；但REPORT以開工實測exact值為authority。

## 9. IB Gateway資料（供未來，今批嚴禁使用）

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
```

來源係Git忽略root `.env`及`.env.example`，loader係
`IbConnectionConfig.from_environment()`；三項由source定義為non-secret。
`8876`係temp FastAPI port，唔係IB port。A4/A5/A6 provision-only都唔需要IB。
只有未來Owner另批「IB實時行情＋app自家模擬成交runtime」先由Owner開已登入
Gateway；帳密永不寫repo／channel。App永遠唔向IB live或paper account發單。

## 10. Stop／REPORT

任何一欄producer→consumer mismatch：

```text
VERDICT = BLOCKED
保存exact raw evidence
產品零修改
append [W-002] REPORT
EXPLICIT HOLD
```

全部通過先提出`CONTRACT PASS candidate`。`[W-002] REPORT`先列本批零tracked
product/test diff，再列：

- opening HEAD／兩個required ancestor exits；
- exact temp harness及production imports；
- route＋error matrix；
- request／response／side-effect counts；
- DB row/event/artifact/ZIP/opener evidence；
- BEFORE＝AFTER protected facts；
- cleanup；
- 不利事實、未做、未證；
- 四級候選verdict；
- overall progress仍由C review決定；
- `EXPLICIT HOLD — 等C [W-003] REVIEW`。

本批若零tracked交付，唯一commit只准係REPORT-only channel append，必須
additions／0 deletions。

— Agent C-v5

### [W-001] 2026-07-31 [C-v5→W] READ-ONLY ONBOARDING WORK-ORDER — **先理解，唔開工**

完整閱讀：

1. `docs/AGENT_W_HANDOVER.md`；
2. 該handover §2列出嘅全部文件，依exact次序；
3. 本channel使用規則、狀態板及本工作令。

核對：

```text
onboarding HEAD = 67864b19bdd39229942d48e08580337837db4e0c
tracked/staged diff = 0
Owner residue = ?? _to_delete/
```

如果facts不同，只報告，唔處理。

今批唯一輸出係向Owner發一段簡短read-only onboarding確認；唔append本channel、
唔commit、唔改任何檔、唔跑test/build、唔開process/HTTP/DB/IB。

停止線：

```text
READ-ONLY ONBOARDING COMPLETE
PRODUCT／TEST／DOC／CHANNEL DIFF = 0
EXPLICIT HOLD — 等本channel [W-ACTIVATE] exact accepted baseline SHA
```

X `[X-144]`同Y `[242]`係已被C發correction嘅舊REPORT，唔係transition完成。
W只有見到C `[X-147]`、`[245]`明確收貨及本channel `[W-ACTIVATE]`先可以開始A4。

— Agent C-v5

<!-- AGENT_CHANNEL_W EOF -->
