# P6 local app-owned paper runtime MVP implementation plan

日期：2026-07-31

狀態：**Owner 已批准完整書面規格。呢份 plan 係施工 authority；Agent W 仍只可
喺 Owner 轉發 `AGENT_CHANNEL_W.md` 最新 one-shot WORK-ORDER 後開始。**

Authoritative design：

`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`

執行者：**Agent W，一個人負責完整 frontend＋backend＋runtime＋integration。**

> 本環境冇提供 `writing-plans` skill；本文件按 repo 既有 implementation-plan
> 格式作同等嚴格 fallback。Design 管「要做成咩」；本 plan 凍結「點拆、邊啲
> public contracts、點驗、幾時先算完成」。

---

## 0. 最終結果及證據級別

W要一次過交付：

```text
IBKR Gateway (Simulated Trading) read-only market data
→ app-owned persistent paper runtime
→ shared strategy＋conservative execution semantics
→ simulated decisions/orders/fills/trades
→ virtual account／position／PnL
→ lifecycle／disconnect／8R／8-loss safety
→ runtime chart／timeline
→ immutable paper-review.v2
→ true registered backend＋browser＋default DB journey
```

最高候選 verdict：

```text
TRUE E2E PASS — P6 local app-owned paper runtime
```

四級要逐級分開：

```text
COMPONENT PASS
CONTRACT PASS
INTEGRATION PASS
TRUE E2E PASS
```

Replay只證strategy／execution／safety；真Gateway只證transport／provider truth；兩組
證據加埋真browser及default DB先可候選TRUE E2E。

---

## 1. 開工 gate

### 1.1 Required ancestors

W必須先確認：

```text
7575e66ce668c910ab109eecc7bb9b34373914ee  accepted product baseline
88b153d0d04e7a46d40a48cbc56787da4d570046  X final accepted code
19ff5ff                                          Y final accepted code
6091dce6cae8c1577bb4924fbd778d1cd6e8e088  design／cleanup baseline
```

另要確認Owner短指令所列嘅最新activation commit係HEAD ancestor。

任何required commit唔係ancestor：append `QUESTION`，HOLD，零修改。

### 1.2 Worktree

預期：

```text
tracked diff  0
staged diff   0
allowed only ?? _to_delete/
```

`_to_delete/`永遠唔讀、唔改、唔stage、唔restore、唔clean。任何其他未知dirty
file先append `QUESTION`，唔stash／checkout／reset。

### 1.3 Protected BEFORE

開第一個真write handle或Gateway session前，記exact：

- `data/` recursive file count＋total bytes；
- `data/backtests/runs.sqlite3` bytes＋full SHA-256；
- `data/backtests/promotion-decisions.sqlite3` bytes＋full SHA-256＋exact row；
- `data/strategies/**` inventory＋SHA；
- approved baseline result四個source members bytes＋SHA；
- `data/paper/`、DB、`-wal`、`-shm`、review root presence；
- 5173／8000／8876／7498 listeners；
- Owner IBKR Gateway，以及任何unexpected TWS／IBC／backend／Vite／browser
  owned process facts；
- tracked／staged status。

已知transition值只係預期，開工實測先係authority。

### 1.4 IBKR Gateway

Owner已開並登入 **IBKR Gateway (Simulated Trading)**：

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
Read-Only API=checked
Master API client ID=blank
```

Client ID 7由app提交，唔係Owner喺Gateway填。W唔讀／寫帳號、密碼、2FA，唔關閉
Owner Gateway。Gateway未ready係唯一可要求Owner處理嘅正常中途HOLD。

---

## 2. One-shot施工規則

1. W唔開其他coding agents。
2. 可做多個atomic internal commits，但唔交interim REPORT等C逐批review。
3. 一般bug、test failure、frontend/backend mismatch由W自行繼續修。
4. 最後先交一份`[W-FINAL] REPORT`，然後EXPLICIT HOLD。
5. 只可因以下情況中途HOLD：
   - Owner要完成Gateway登入／API confirmation；
   - protected baseline不一致；
   - design／plan真矛盾影響truth、安全或write；
   - 發現任何IB order possibility；
   - 必須超出批准write scope。
6. 非P6而又唔阻MVP嘅bug記入`docs/POST_MVP_BACKLOG.md`，唔順手修。
7. Heavy lanes串行：backend full、frontend full、replay、browser、true Gateway唔並行。
8. 開發期用focused tests；final backend／frontend full各最多一次，除非首次因
   test infrastructure而非產品失敗，REPORT要完整披露。

---

## 3. 唔准重做／唔准破壞

以下已收貨，先讀tests再擴：

- immutable PromotionDecision及eligible strategy；
- strategy／contract／baseline selection；
- provisioning authorization；
- request recovery；
- v3 zero-runtime paper store；
- ledger origin；
- zero-runtime `paper-review.v1`；
- frontend strict parser／race guards；
- ZIP verifier／download／Terminal opener；
- P5 result export及CORS exposure。

硬規則：

- `paper-review.v1` bytes、member order、SHA及opener保持可讀；
- v3 zero-runtime records不可改寫成「曾運行」；
- existing request IDs仍係idempotency authority；
- no frontend-known-zero fallback；
- no state-changing auto retry；
- no IB order／cancel／account-portfolio authority；
- no Telegram／PWA／Push／Tailscale／cloud；
- no P3→P4日期handoff；
- no strategy／run／result／PromotionDecision mutation。

---

## 4. Timeframe contract（自審修正版）

### 4.1 角色分開

```text
market_input_timeframe       MVP normal = 1m
execution_timeframe          MVP normal = 1m
chart_display_timeframe      MVP normal = 1m or 30m
strategy_timeframe_profile   immutable strategy.v1 authority
```

`strategy-0003`必須保持：

```text
bias  D
mid   1H
entry 5m
```

30m只係MVP display選項；30日只係replay測試窗口。禁止為加速而將策略entry由5m
改成30m。

### 4.2 單一capability authority

新增backend-owned typed registry。Normal profile：

```text
market_input enabled   ["1m"]
execution enabled      ["1m"]
chart_display enabled  ["1m", "30m"]
```

Frontend只食capability response。Tests可以temp注入5m去證明傳遞，但normal UI唔
因此顯示5m。

### 4.3 Identity

Runtime selection、store、topic、cursor、chart、snapshot、review全部要帶對應
timeframe。Strategy profile由backend讀locked strategy artifact後保存SHA及exact
trio，client不可提交override。

---

## 5. 建議file architecture及ownership

W可因現行source實況調整細檔名，但以下責任邊界不可合併返一個巨型檔：

```text
src/futures_research/paper/
  __init__.py
  timeframes.py          typed capability／TimeframeSpec／aggregation boundary
  models.py              domain enums／immutable events／safe numeric helpers
  store.py               v4 runtime store、lease、atomic append／projection
  market_data.py         adapter protocol、bus、journal／gap classifier
  ib_market_data.py      IBKR Gateway read-only implementation only
  strategy_runtime.py    shared strategy adapter、no-lookahead closed snapshots
  execution_runtime.py   shared ConservativeExecution adapter
  runtime.py             manager＋per-trader lifecycle／commands
  replay.py              OS-temp saved-real-data deterministic harness
  review_v2.py           cutoff freeze＋v2 package source

src/futures_research/api/
  paper_runtime.py       strict public Pydantic models／service
  routes_paper.py        registered routes
  deps.py                normal singleton dependencies
  main.py                lifespan／shutdown cleanup
  paper_traders.py       v1 legacy kept; v2 bridge only
  paper_review*.py       v1 kept; v2 dispatch only

tests/
  test_paper_timeframes.py
  test_paper_runtime_store.py
  test_paper_market_data.py
  test_paper_ib_market_data.py
  test_paper_strategy_runtime.py
  test_paper_execution_runtime.py
  test_paper_runtime.py
  test_paper_runtime_routes.py
  test_paper_runtime_replay.py
  test_paper_review_v2.py
  existing paper regression files

apps/web/src/lib/paper/
  runtimeTypes.ts
  runtimeContract.ts
  runtimeContract.test.ts
  runtimeState.ts
  runtimeState.test.ts
  runtimeChart.ts
  runtimeChart.test.ts

apps/web/src/components/paper/
  PaperTraderOverview.tsx
  PaperTraderDetail.tsx
  PaperRuntimeChart.tsx
  PaperRuntimeControls.tsx
  PaperReviewV2Panel.tsx

apps/web/src/
  api/client.ts
  pages/PaperPage.tsx
  pages/PaperPage.test.tsx
  styles/paper.css
```

唔准把所有新runtime邏輯再塞入目前約3,000行`PaperPage.tsx`或2,000行
`paper_traders.py`。Refactor只限P6，保持現有public behavior及tests。

---

## 6. Store v4

### 6.1 Version boundary

Normal runtime store：

```text
PRAGMA user_version = 4
```

- default `data/paper/` absent時直接建立v4；
- v1–v3只做明確legacy read／test compatibility；
- v3 record不可silent upgrade或加入假runtime events；
- unknown／partial／schema drift fail closed；
- normal runtime writer只接受v4。

### 6.2 Immutable origin

新v2 trader creation要原子保存：

- existing strategy／contract／baseline/account/safeguards；
- `market_input_timeframe`；
- `execution_timeframe`；
- `chart_display_timeframe`；
- strategy timeframe profile＋strategy content SHA；
- selection fingerprint；
- initial lifecycle `provisioned`。

Create v2同v1要schema-discriminated；禁止alias接受`schema_version`。

V1 one-off provisioning authorization只保留legacy v1 compatibility。V2 normal
產品建立authority係：

```text
immutable PromotionDecision=use
＋ exact eligible selection
＋ server-owned integrity/readiness checks
＋ Owner喺UI明確確認建立
```

唔再要求Telegram、frontend secret、hidden test flag或每次由C注入in-memory permit。
呢個係production multi-trader可用性所需；final gate仍只建立exact一個default trader。

### 6.3 Append-only tables

至少需要：

```text
paper_market_inputs
paper_decisions
paper_intents
paper_orders
paper_fills
paper_trades
paper_position_events
paper_equity_points
paper_lifecycle_commands
paper_lifecycle_events
paper_safety_events
paper_safety_epochs
paper_processing_checkpoints
paper_runtime_leases
paper_runtime_command_results
paper_review_v2_requests／ready／failures
```

Current projection可有獨立tables，但所有projection要可由append-only evidence重建。
Update/delete trigger、FK、unique constraints及exact SQL integrity全部要有tests。

### 6.4 Atomic bar processing

每個closed input一個SQLite `BEGIN IMMEDIATE` transaction：

1. consume unique input；
2. strategy event／decision；
3. intent/order/fill/trade；
4. position；
5. cash/equity/PnL；
6. safety；
7. cursor/lifecycle version。

Duplicate同一input identity返回原result，零第二side effect。Crash mutation要證明
全部rollback。

### 6.5 Lease

- default store exact一個writer；
- lease有instance ID／PID／started／heartbeat；
- second writer fail closed；
- stale lease只可令startup進`recovery_required`，唔自動resume；
- read APIs唔取得writer lease。

---

## 7. Public wire contracts

所有models：

- exact keys／extra forbid；
- public field只接受`schema`，拒絕`schema_version`及雙key；
- lowercase closed enums；
- canonical UTC `Z`；
- canonical UUID4；
- safe integer；
- finite JSON number，拒絕NaN／Infinity／negative zero；
- unknown fail closed。

### 7.1 Capabilities

```text
GET /api/v1/paper/runtime-capabilities
200 paper_runtime_capabilities.v1
```

Exact top-level：

```text
schema
timeframes
market_modes
safety_defaults
lifecycle_states
as_of
```

`timeframes`按role返回ordered enabled values；strategy profile明講
`source=strategy.v1`及`client_override=false`。

### 7.2 Trader create v2

保留同一路徑：

```text
POST /api/v1/paper/traders
```

新request：

```json
{
  "schema": "paper_trader_create_request.v2",
  "request_id": "<uuid4>",
  "selection": {
    "strategy_id": "...",
    "content_sha256": "<sha256>",
    "contract_id": "...",
    "baseline_run_id": "...",
    "baseline_result_sha256": "<sha256>",
    "timeframes": {
      "market_input": "1m",
      "execution": "1m",
      "chart_display": "30m"
    }
  }
}
```

Response：

```text
paper_trader.v2
paper_trader_request_status.v2
paper_trader_list.v2
```

`paper_trader.v2`要返回backend-derived
`strategy_timeframe_profile={bias:D,mid:1H,entry:5m}`。V1 endpoints／parsers只供
legacy explicit schema，唔混成一個含optional欄嘅寬鬆model。

### 7.3 Runtime preflight

```text
POST /api/v1/paper/traders/{trader_id}/runtime-preflight
```

Request exact keys：

```text
schema=paper_runtime_preflight_request.v1
request_id
expected_lifecycle_version
selection_fingerprint
requested_market_mode        live | test_delayed
```

Response：

```text
paper_runtime_preflight.v1
```

要有`preflight_id`、trader/version/fingerprint/mode、ordered checks、overall、
checked_at、expires_at。Checks exact：

1. immutable selection；
2. baseline integrity；
3. role-aware timeframe compatibility；
4. Gateway handshake＋read-only market-data capability；
5. truthful provider mode；
6. exchange calendar/session；
7. v4 store integrity；
8. single-writer lease；
9. cursor/journal continuity；
10. lifecycle/safety eligibility。

Notification永遠唔係check。Preflight zero state-changing write；可以做bounded Gateway
capability probe，但唔訂閱長期feed。另要read-only核實strategy warmup所需嘅
native Daily及1m history足夠；不足要closed `warmup_insufficient`，唔用零值補。

### 7.4 Commands

```text
POST /api/v1/paper/traders/{trader_id}/runtime/start
POST /api/v1/paper/traders/{trader_id}/runtime/pause
POST /api/v1/paper/traders/{trader_id}/runtime/resume
POST /api/v1/paper/traders/{trader_id}/runtime/permanent-stop
```

Start／resume request：

```text
schema=paper_runtime_start_request.v1
request_id
expected_lifecycle_version
selection_fingerprint
preflight_id
```

Pause／stop request：

```text
schema=paper_runtime_control_request.v1
request_id
expected_lifecycle_version
selection_fingerprint
```

Accepted response係`paper_runtime_command.v1`，200 replay或202 newly accepted。
同request＋同payload返同一command；同request＋異payload
`request_id_conflict`；stale version零write；frontend永不自動重POST。

### 7.5 Read APIs

```text
GET /api/v1/paper/traders/{trader_id}/runtime
GET /api/v1/paper/traders/{trader_id}/timeline?after_cursor=&limit=
GET /api/v1/paper/traders/{trader_id}/chart?timeframe=&after_cursor=&limit=
```

Responses：

```text
paper_runtime_snapshot.v1
paper_runtime_timeline.v1
paper_runtime_chart.v1
```

每份帶trader ID、lifecycle version、selection fingerprint、data mode、
provider/session identity、timeframe、as-of及cursor。Timeline/chart bounded，
ordered，no duplicates；stale client cursor回closed error，唔silent reset。

`paper_runtime_snapshot.v1`最少包括：

- lifecycle＋reason；
- last trusted／received timestamps；
- stale／blind state；
- cash/equity/realized/unrealized PnL；
- pending intent；
- open position；
- safety current/limit/high-water；
- decision/trade counts；
- latest timeline/chart cursors。

### 7.6 Error envelope

Runtime用`paper_runtime_error.v1`，exact：

```text
schema
code
message
retryable
trader_id
request_id
lifecycle_version
details
```

`details`係code-specific strict discriminated object，唔係任意dict。Frontend known
code映人話；unknown只顯安全generic copy＋可複製診斷，唔顯raw stack。

---

## 8. Market data

### 8.1 Adapter protocol

Production adapter只暴露：

```text
connect
disconnect
probe
subscribe_bars
unsubscribe_bars
backfill_gap
```

禁止任何order/account方法。Import boundary及mutation test要令
`placeOrder`／`cancelOrder`／order callback path一出現就fail。

### 8.2 IBKR Gateway implementation

- exact `127.0.0.1:7498`／client 7由env讀；
- exact NQU6／CME contract resolve，同registry identity核對；
- request market data only；
- provider callback分類`live`／`test_delayed`；
- frozen／unknown唔可升格live；
- callbacks canonicalize成immutable 1m forming／closed updates；
- same callback duplicate exact once；
- disconnect bounded cleanup，唔關Owner Gateway。

### 8.3 Bus

每`contract × input timeframe × mode`exact一個upstream subscription。多trader收
immutable fan-out；一個trader fail唔影響其他；unsubscribe只喺最後subscriber離開。

### 8.4 Journal／gap

每個closed input保存provider session、contract、timeframe、mode、event／received
timestamp、OHLCV、SHA、source kind及monotonic identity。

Disconnect即：

- freeze新decision／fill；
- append hold；
- retain position／last trusted mark；
- reconnect＋backfill。

`<=5m`只有完整、同identity、連續bars先按原時間順序經同一core處理並標
`recovered_processing`。`>5m`／incomplete／identity drift進`tripped`，零假exit。

---

## 9. Strategy及simulated execution

### 9.1 Shared core

- 解析locked strategy-0003 source；
- 重用`TrendStrategy`；
- 重用`ConservativeExecution`；
- 抽共用adapter/helper，唔另寫簡化paper算法；
- decision只食closed strategy snapshots；
- execution只食closed 1m bars；
- forming bar永不入decision/fill；
- calibration／native Daily／D／1H／5m semantics同backtest一致。

Runtime如需incremental MTF builder，可以新增stateful wrapper，但golden test必須用
同一段canonical bars同backtest precomputation逐snapshot比對。

### 9.2 Warmup及activation boundary

- preflight只讀檢查protected canonical 1m／native Daily歷史及locked calibration；
- start transaction可以將所需source identity／SHA及warmup journal寫入
  `data/paper/**`，但永不改protected market data；
- warmup只建立indicator／regime／MTF context；
- strategy/execution paper side effects由exact `activated_at`之後第一根新closed
  entry/execution bar先開始；
- warmup期間禁止decision、intent、order、fill、trade及PnL；
- execution起點必須flat、pending zero；
- restart重建只讀已保存paper journal/checkpoint，唔用最新策略或未鎖資料；
- warmup與active bar邊界要有golden/no-lookahead tests。

### 9.3 Virtual account

App-owned：

- initial capital from locked baseline；
- quantity/cost policy from locked baseline；
- cash/equity；
- pending intent/order；
- single net position；
- realized/unrealized PnL；
- completed trades。

IB account/portfolio values exact zero reads and zero authority。

### 9.4 Safety

Defaults typed：

```text
max_drawdown_r=8
max_losing_streak=8
blind_minutes=5
```

8R用runtime equity high-water。Loss streak只計completed trades。Trip／pause／stop
立即禁新decision＋cancel pending intent；有trusted eligible price先經shared execution
平倉；冇價保持`flatten_pending`，禁止last/zero/fake fill。

### 9.5 Lifecycle

Closed：

```text
provisioned starting running pausing paused tripped stopping
recovery_required permanently_stopped
```

Browser關閉runtime繼續。Backend restart對非terminal active trader只設
`recovery_required`；Owner fresh preflight＋explicit resume先再行。Permanent stop
irreversible。

---

## 10. Deterministic replay

### 10.1 Inputs

- read-only使用已保存真IB NQ canonical market data；
- 30日bounded window；
- 1m input/execution；
- strategy profileD／1H／5m；
- 30m chart display；
- 全部store/artifact放OS-temp；
- test-only strategies／factories唔入normal product catalog。

### 10.2 Four paths

1. forced long／profit；
2. forced short／loss；
3. no signal／zero trade；
4. safety path。

「forced」只可改strategy decision fixture，唔可改price bars。每個path要重用production
runtime/store/execution，唔可另寫test simulator。

### 10.3 Required replay evidence

- multi-trader same feed，independent state；
- long及short exact fills/cost/PnL；
- zero-trade合法；
- duplicate／out-of-order；
- forming bar no decision；
- short complete gap recovery；
- long/incomplete gap trip；
- restart recovery_required；
- pause/resume/permanent stop；
- 8R及8-loss；
- no trusted price no fabricated exit；
- timeframe propagation及strategy profile preservation；
- review v2 cutoff／bytes／SHA。

---

## 11. `paper-review.v2`

### 11.1 Dispatch

- v1 builder／reader／frontend parser完全保留；
- runtime evidence走v2；
- schema判別先於內容parse；
- unknown schema fail closed；
- v1/v2唔共用含大量optional欄嘅寬鬆model。

### 11.2 Ordered ZIP profile

V2 exact ordered STORE members：

```text
1.  paper-review.json
2.  paper/runtime-state.json
3.  paper/market-inputs.json
4.  paper/decisions.json
5.  paper/orders.json
6.  paper/fills.json
7.  paper/trades.json
8.  paper/equity.json
9.  paper/events.json
10. divergence/expected-actual.json
11. baseline/result.json
12. baseline/trades/{run_id}.json
13. baseline/equity/{run_id}.json
14. baseline/events/{run_id}.json
15. terminal-opener.txt
```

ZIP requirements：

- STORE；
- timestamp 1980-01-01；
- mode 0600；
- local／central extra 0；
- member/archive comments 0；
- no duplicate／unsafe path；
- member bytes/SHA manifest；
- whole bytes/SHA persisted；
- baseline four source bytes exact；
- opener endpoint text/UTF-8 bytes/SHA exact等於member 15。

Frontend保持bounded central-directory authority；CRC32仍唔係frontend authority。

### 11.3 Cutoff

一個explicit request凍結：

- all append-only table high-water marks；
- provider/session/timeframe identities；
- lifecycle/safety/account/position projection；
- baseline members；
- strategy identity；
- persisted opener。

Builder只讀cutoff前committed evidence，唔重播／補算／平倉。Open position照實。

---

## 12. Frontend

### 12.1 Normal journey

```text
新增交易員
→ strategy／contract／baseline
→ chart display 1m or 30m（capability driven）
→ 確認並建立交易員
→ provisioned
→ runtime preflight
→ 明確開始模擬交易
→ detail polling
→ pause／resume／permanent stop
→ paper-review.v2 download／opener
```

建立同開始係兩個CTA。UI明講：

```text
IB行情驅動嘅app自家模擬成交
```

不得出現「IB paper trading」、Telegram、PWA、remote/cloud。

### 12.2 Truth

Card／detail持續顯示：

- lifecycle；
- locked identity；
- strategy D／1H／5m profile；
- input/execution/display timeframes；
- LIVE或TEST-DELAYED；
- market age／stale／blind；
- cash/equity/PnL；
- pending intent／position；
- safety current/limits；
- decisions/trades。

Unknown／blocked／stale唔畫綠；frontend唔補零或自己算PnL。

### 12.3 Polling/race

- bounded cursor polling；
- no WebSocket；
- listToken/detailToken/generation/source/mount/in-flight guards；
- state command in-flight鎖全部identity controls；
- old trader/tab response 0 adopt；
- unmount abort reads，但唔stop backend runtime；
- state-changing request exact one／UUID exact one／auto retry zero；
- unknown outcome只GET同request status/snapshot，唔再POST。

### 12.4 Chart

- lightweight-charts incremental `update()`；
- forming bar可覆蓋最後bar，closed後canonical finalize；
- 1m／30m切換由backend data；
- markers、position、stop/target、unrealized PnL；
- cursor gap／identity mismatch fail closed，唔全量silent reset。

### 12.5 Controls

- pause、resume、permanent stop有confirmation；
- permanent stop清楚不可逆；
- `flatten_pending`唔顯示已平；
- trip要顯示原因、證據及manual re-arm gate；
- final Owner journey留下paused＋flat。

---

## 13. TDD／atomic commit順序

W可自行微調commit邊界，但dependency次序保持：

### Commit 1 — contracts／timeframes

- capability registry；
- v2 selection/trader wire models；
- TS mirror parsers；
- strategy profile preservation tests；
- no normal write。

### Commit 2 — store v4

- schema/integrity/triggers；
- atomic v2 create；
- append-only runtime evidence；
- projections、lease、request results；
- crash/duplicate/race tests。

### Commit 3 — runtime core／replay

- bus、journal、incremental strategy adapter、execution、account、安全、lifecycle；
- saved-real-data four-path replay；
- no real Gateway/default write。

### Commit 4 — IBKR Gateway read-only adapter

- transport/probe/subscription/backfill/mode classification；
- fake transport tests；
- explicit zero order surface；
- only bounded real handshake during final gate。

### Commit 5 — registered API

- capabilities/preflight/commands/snapshot/timeline/chart；
- app lifespan/runtime manager；
- strict errors/idempotency；
- ASGI producer contract tests。

### Commit 6 — review v2

- cutoff freeze；
- 15-member ZIP；
- reader/download/opener；
- v1 regressions。

### Commit 7 — frontend runtime

- strict contracts/state machine/components；
- real client calls；
- chart/polling/controls/review v2；
- race and mutation tests。

### Commit 8 — cross-layer＋operational evidence

- registered backend→production consumer；
- true server/browser/Gateway/default DB；
- docs only where current contract maps must be synchronized；
- no product behavior invented during evidence run。

先寫RED test，再最少production implementation。每個commit `git diff --check`。

---

## 14. Mandatory tests

### 14.1 Backend focused

- strict models；
- store schema／SQL／trigger integrity；
- atomic append and replay；
- lease；
- timeframes；
- MTF golden equivalence；
- execution golden equivalence；
- PnL/safety；
- disconnect/recovery；
- runtime routes；
- v1/v2 review；
- IB fake transport。

### 14.2 Frontend focused

- every strict parser；
- known/unknown error copy；
- capabilities-driven timeframe；
- same-batch double submit；
- stale trader/tab/source/mount；
- polling cursor；
- command unknown recovery；
- chart incremental updates；
- TEST-DELAYED labels；
- v1/v2 review/download/opener；
- click/revoke cleanup。

### 14.3 Cross-layer

Registered FastAPI raw status/headers/body bytes餵production TS client/parser，至少：

- all happy routes；
- 404／409／422／503；
- unknown status/code/schema；
- stale identity；
- wrong content type；
- ZIP/header/member/SHA drift；
- exact request counts及zero unintended side effects。

### 14.4 Full/tooling

```text
python -m ruff check src tests
python -m mypy src
python -m pytest -q                    # final exact once
npm run typecheck
npm run lint
npm run build
npm run test -- --run                 # final exact once
git diff --check
```

用repo實際venv/node命令；REPORT記exact command、exit、count、duration。

---

## 15. Mandatory mutation probes

逐項：

```text
GREEN
→ exact production mutation
→ named test RED for intended reason
→ byte-exact restore
→ SHA exact
→ GREEN
```

最少：

1. timeframe role被寫死／30m覆蓋5m strategy entry；
2. forming bar進decision；
3. duplicate input產生double fill；
4. out-of-order仍處理；
5. fill同position唔同transaction；
6. second writer獲lease；
7. stale command仍write；
8. state-changing auto retry；
9. delayed被標LIVE；
10. short incomplete gap auto-resume；
11. missing trusted price造exit；
12. 8R漏trip；
13. 8-loss漏trip；
14. restart自動resume；
15. old response覆蓋new tab；
16. chart cursor silent reset；
17. review cutoff漏一張table；
18. ZIP member/order/SHA check被移除；
19. v1 parser被v2放寬；
20. order transport method可被調用。

任何mutation第一次唔RED要披露遮蔽原因並強化test，唔可以當PASS。

---

## 16. True IBKR Gateway gate

### 16.1 Before

- Owner Gateway已登入；
- Read-Only API tick；
- port 7498 listener存在；
- no W-owned backend/Vite/browser residue；
- default data facts recorded。

### 16.2 Evidence

- exact host/port/client 7；
- handshake；
- exact NQU6/CME identity；
- provider-reported data mode；
- at least one market-data callback；
- canonical journal row；
- browser health/chart reflects same identity；
- disconnect/reconnect cleanup；
- order/cancel/account-order calls exact zero。

若只得delayed：

- Owner explicit選TEST-DELAYED；
- UI/artifact全部label；
- 可通功能閉環；
- REPORT明講real-time entitlement未證。

W唔長時間等自然signal；transport證據到手即停。Trades/safety用replay證。

---

## 17. True browser＋default DB journey

Approved exact target：

```text
strategy strategy-0003
contract NQ-202609-CME
baseline nq-20260728-standard-365adf
chart display 30m
```

流程：

1. 真backend；
2. 真Vite；
3. clean temp browser profile；
4. eligible/list/select；
5. exact one create；
6. default v4 DB exact one trader/account；
7. runtime preflight；
8. exact one start；
9. true Gateway market update；
10. browser reload同一trader；
11. runtime snapshot/timeline/chart；
12. exact one pause；
13. flat/pending zero；
14. exact one v2 review；
15. real download＋ZIP verify；
16. persisted opener；
17. close owned browser/server/client。

Expected default writes只可：

```text
data/paper/paper-traders.sqlite3
data/paper/paper-traders.sqlite3-wal
data/paper/paper-traders.sqlite3-shm
data/paper/review-artifacts/**
```

Final：

```text
default trader paused
open position 0
pending intent 0
W-owned listeners/processes/profile/temp 0
Owner Gateway untouched
```

---

## 18. Cleanup及AFTER

W只終止自己啟動嘅process。Cleanup：

- Vite／FastAPI；
- app Gateway client session；
- owned Chromium profile；
- replay/temp DB/artifact/evidence；
- object URLs／browser context；
- no 5173／8000／8876 owned listener；
- 7498／Owner Gateway保持原樣。

重做BEFORE inventory。Expected delta：

```text
only approved data/paper/**
plus tracked P6 source/tests/docs commits
```

所有其他protected source/data bytes/SHA exact不變。

---

## 19. `[W-FINAL] REPORT`

先commit全部product/tests；再將REPORT append到`AGENT_CHANNEL_W.md`最頂並另commit。
REPORT-only channel diff additions／0 deletions。

必列：

- opening/final HEAD及required ancestor exits；
- atomic commits；
- exact file numstat；
- four-level verdict；
- tests/tooling exact commands/counts；
- all mutation RED→restore→GREEN；
- Gateway host/port/client/mode/callback；
- IB order/account-order exact zero；
- replay four paths；
- request counts；
- DB schema/table/row/event counts；
- v2 ZIP exact 15 members/bytes/SHA；
- browser journey；
- default expected delta；
- protected BEFORE/AFTER；
- ports/process/temp cleanup；
- every adverse fact；
- remaining unverified；
- EXPLICIT HOLD等C review。

---

## 20. 完成線

以下任何一項未通，W唔可交TRUE E2E candidate：

- strategy-0003 timeframe profile被改；
- frontend/backend contract未真咬合；
- true Gateway callback未證；
- provider mode不明；
- any IB order possibility；
- replay未覆蓋long/short/no-signal/safety；
- default DB journey未完成；
- final trader唔係paused＋flat；
- v2 artifact未驗exact；
- protected unexpected write；
- owned residue未清。

如果全部通過，W交`[W-FINAL]`並HOLD。C之後獨立review；W唔自行開始P3→P4、
PWA／Push、cloud、更多normal timeframes或其他post-MVP工作。
