# P5 Stage 1 Normal Read Seam Design

日期：2026-07-28

狀態：**Owner 已於 2026-07-28 完成 written-spec review並正式批准；Y-v5
Stage 1及C-v5驗收已完成。當時嘅工作令已從現行工作樹刪除，Git history仍可
精確恢復；本文件唔係新實作permission。**

範圍：P5 normal mode 嘅純讀接線——結果列表、單一結果主檔、trades、
events、narrative、D／1H／5m chart、exact identity、async isolation，同
P4→P5→P2 讀取鏈。Stage 1 完成並經 C REVIEW 後，先另開 Stage 2
export＋PromotionDecision/history。

六份 `docs/ui/designs/*.html` 仍然係畫面權威。本文件只落實現有 backend
contract 可以誠實供應嘅 normal data seam；唔重畫 P5、唔改 owner-review
fixture、唔開 P6／洞察刪除／IB Gateway，亦唔執行任何回測、migration 或真資料
寫入。

## 1. 目的與完成邊界

Stage 1 要令以下讀取鏈成立：

```text
P4 completed job exact run_id
  → P5 normal list exact run row
  → P5 normal detail exact same run_id
  → result.v1 main
  → trades / events / narrative / D・1H・5m charts（逐資源隔離）
  → exact strategy_version
  → P2 Library exact version
```

成功後，Owner 可以用 normal page 睇真 result main、真結構化交易／拒絕證據、
真 deterministic narrative 同 backend chart；唔再由 TypeScript assertion 或
fixture 冒充 wire truth。

但 Stage 1 **唔等於整個 P5／MVP 閉環完成**：

- normal export 未接；
- normal PromotionDecision／history 未接；
- P6 trader 未接；
- 洞察刪除仍等 Owner；
- 30m、volume、列表 compact bottleneck、backend system judgment 同 per-trade
  net-R 仍未有現行 wire truth，必須按 §8 誠實顯示，唔准估。

## 2. 權威依據與已證實現況

依據：

- `docs/ui/designs/p5-results.html`；
- `docs/03-frontend-component-coverage-matrix.md` P5-M01–M04、
  P5-D01–D15；
- `docs/02-mvp-closed-loop-user-journey.md` S6／S7；
- `docs/09-journey-closed-loop-audit.md` B3／B4；
- `docs/superpowers/specs/2026-07-26-run-evidence-and-preview-design.md`；
- `docs/08-ui-backend-mapping.md` §4–§5；
- X-v5 `[X-073]` REPORT、C-v5 `[X-074]` REVIEW；
- Y-v5 `[189]` REPORT、C-v5 `[190]` REVIEW。

現行 backend 已有：

```text
GET /api/v1/runs
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/trades
GET /api/v1/runs/{run_id}/events
GET /api/v1/runs/{run_id}/chart?tf=D|1H|5m
GET /api/v1/runs/{run_id}/narrative
```

已證實限制：

1. chart endpoint 只接受 `D | 1H | 5m`；`30m` 會 422；
2. chart payload 無 volume；
3. chart cache miss／stale 時，GET 可以 lazy-write derived
   `chart/*.json`；
4. list row 嘅 `funnel_status` 係 funnel document status（例如 `ok`），
   **唔係被截住嘅 layer**；
5. trades payload無 per-trade `net_r`，亦無計 net-R 所需嘅 canonical
   contract `point_value`；
6. backend 無 canonical「邏輯正常／定義有分歧」判斷；
7. genuine legacy evidence 同 known-empty evidence 已由 backend 分開。

2026-07-28 read-only inventory：

- 17 份 events files；
- 只有 `nq-20260728-standard-365adf` 有完整 structured
  `rejection_evidence`；
- 該 run 有 14 次 rejection；
- 14 次全部到達同一組 layers、全部只有一個 blocker；
- 因此前兩級結構排序打和，要用最新 evaluation sequence 作 tie-break。

## 3. 已選架構

採用一個 P5-owned strict normal adapter：

```text
fetch Response
  → body: unknown
  → HTTP envelope
  → strict runtime parser
  → exact request/response identity guard
  → presentation mapping
  → React state（current generation only）
```

禁止：

- `response.json() as T` 直接進 ready；
- 用 filename、array index、strategy id 或「最近一個 run」重猜 identity；
- parser 改寫、trim、coerce 或補造 wire body；
- child resource 失敗拖垮已驗證 result main；
- owner-review mode 觸發任何 backend business request；
- normal mode fallback 去 owner fixture。

### 3.1 List loading

normal list 同時開始：

1. `GET /api/v1/runs`——主資源；
2. confirmed strategy catalog——只供 display label。

兩者**唔用 `Promise.all` 綁死**：

- run list valid → 可以 ready；
- strategy catalog valid → 用 name；
- catalog error／invalid → fallback exact `strategy_version`，run list仍 ready；
- run list error／invalid → list fatal error。

只顯示 runtime parser 已證實 `validation_run === false` 嘅 rows。server 已將
legacy absent classification投影成 boolean false；frontend唔自行用 truthiness。

### 3.2 Detail loading

每次 `mode` 或 route `runId` 改變：

1. 同步清除舊 detail及全部 child／interaction state；
2. abort上一代 requests；
3. generation加一；
4. owner-review mode只讀 fixture；
5. normal mode先讀並驗證 exact result main；
6. main valid且仍屬current generation先 mark read及 render主體；
7. 然後 trades、events、narrative、D／1H／5m chart並行、逐資源提交。

strategy label catalog可以同 main 並行開始，但只係非致命 label enrichment；
任何 label response都要通過同一 generation／mode guard。

## 4. HTTP transport contract

Stage 1 只新增／改用 GET transport。每個 request接受 `AbortSignal`，回傳以下其中
一種：

```text
ok + parsed body: unknown
HTTP error(status, path, complete response text)
invalid JSON(path)
aborted
network error(path, message)
```

規則：

1. 2xx body仍係 `unknown`，未 parse前唔係成功資料；
2. non-2xx保留 status、path同完整 response text；
3. invalid JSON唔變空 object；
4. 無自動 retry；一次 UI load每個 endpoint最多一個 request；
5. abort唔顯示成新 run錯誤，亦唔可以改 current state；
6. error object唔吞 backend 404／422／503 detail；
7. Stage 1 唔加 POST、blob、export或decision transport。

## 5. Runtime parsing共同規則

所有 P5 parser：

- input係 `unknown`；
- object必須係 plain JSON object；
- P5直接消費／排名嘅 contract node使用已列明 key set；extra／missing key
  fail closed；
- number必須係 finite native number，boolean唔當 number；
- integer、positive／non-negative boundary逐欄驗；
- timestamp必須係帶 timezone嘅有效 instant；
- `trading_date` 必須 exact `YYYY-MM-DD`，唔轉 browser date；
- count必須 exact等於 array length；
- machine identity非空、無首尾空白；
- expected route `runId`要同所有 response `run_id` exact相等；
- duplicate identity／ordinal／sequence fail closed；
- array order保留，parser唔 sort或 mutate caller body；
- `null`、zero、empty、absent、unavailable各有獨立語義，唔互換。

`result.v1`內只供重現性、Stage 1唔消費嘅 nested metadata（例如
calibration、data fingerprint內容、cost detail）仍要係JSON object／primitive，
但P5唔冒認自己做第二套backend main validator。P5對自己實際映射嘅
manifest identity/range/classification、metrics、scorecard、funnel及refs逐欄
strict；artifact完整性仍由現行backend read gate負責。

Parser error只令其所屬資源進 error；但 result list同result main係各頁主資源，
所以兩者 parser error係該頁 fatal。

### 5.1 `run_list.v1`

Top-level exact：

```text
schema, count, runs
```

`schema === "run_list.v1"`；`count === runs.length`。每個 row exact驗：

```text
run_id
strategy_version
contract_id
session_name
range_start
range_end
validation_run
trade_count
net_r
net_pnl
win_rate
profit_factor
max_drawdown_pnl
max_drawdown_r
expectancy_r
scorecard_statuses
funnel_status
funnel_fills
has_scorecard
has_funnel
result_file
```

nullable fields只接受其明列 primitive或`null`；`validation_run`、
`has_scorecard`、`has_funnel`必須 boolean；counts non-negative integer或
`null`；metrics finite number或`null`。`scorecard_statuses`每項 exact
`dim/status`，兩欄只接受 string或`null`。`run_id`必須全表唯一。

### 5.2 `result.v1`

支援現行已證實三個 main profile：

1. legacy compact：無 `funnel`、無 `decision_evidence_complete`；
2. enriched legacy：有 `funnel`、無 `decision_evidence_complete`；
3. current complete：有 `funnel`且
   `decision_evidence_complete === true`。

三種 profile都必須：

- `schema === "result.v1"`；
- top-level只可係該profile定義嘅
  `schema/run/metrics/scorecard/warnings/owner_action/trades_ref/
  equity_curve_ref/events_ref`，加profile允許欄；
- `run` exact有 `run_id/strategy_version/manifest/engine`；
- `run.run_id === route runId`；
- `manifest.schema === "run_manifest.v1"`；
- `manifest.run_id === run.run_id`；
- `manifest.strategy_version === run.strategy_version`；
- contract、session、UTC range、refs、warnings、scorecard同所用 metrics
  primitive type valid；
- compact 8 metrics或enriched 26 metrics只接受現行已證實其中一個 closed set；
- scorecard items exact `dim/status/detail`；
- funnel存在時驗 `funnel.v1`、counts、reason count mapping、units及notes；
- direct URL若 `manifest.validation_run === true`，normal P5拒絕顯示，講明係
  工程驗證結果；
- legacy absent `validation_run`可按backend list classification讀取，但唔補 key。

`owner_action`唔係 Stage 1 decision truth，唔映射成normal action/history。

### 5.3 `trades.v1`

只接受兩個明確 profile。

Current complete：

```text
schema, run_id, trades, decision_evidence_complete
```

- `schema === "trades.v1"`；
- exact run identity；
- `decision_evidence_complete === true`；
- 不可同時有 `decision_evidence_availability`；
- 每個 trade驗 execution fields、tags同完整 `decision_evidence`；
- `trade_id`同 evidence `trade_id` exact；
- evidence ordinal positive、unique、由1連續；
- `trades.length`同 main `metrics.trade_count` exact；
- entry、stop、exit、conservative assumptions四段齊全；
- condition facts保持 actual／required native primitive type；
- blocker／source sequence／candidate identity reconciliation成立。

每個complete trade嘅execution key set exact：

```text
trade_id, contract_id, direction, signal_kind, quantity,
signal_timestamp, entry_timestamp, entry_ts_init,
exit_timestamp, exit_ts_init,
entry_reference, entry_price, stop_price, target_price, exit_price,
exit_reason, gross_points, gross_pnl, total_commission, net_pnl,
entry_slippage_ticks, exit_slippage_ticks, tags, decision_evidence
```

`tags`使用現行exact key set：

```text
signal_kind, daily_regime, regime_strength, entry_session, entry_local_time,
entry_layers, inside_count, multiple_inside, has_sweep_bonus,
lmr_step1_leg_atr, lmr_step2_leg_atr, atr_expansion_ratio,
mfe_r, mae_r, gap_through_target,
volatility_owner_view, volatility_system_daily_atr_percentile,
volatility_system_range_ratio, volatility_actual_daily_range
```

`decision_evidence` exact有
`trade_id/ordinal/entry/stop/exit/conservative_assumptions`；四個nested segment
再按 `docs/superpowers/specs/2026-07-26-run-evidence-and-preview-design.md`
§4.1–§4.2嘅現行durable field set逐欄驗，唔接納event-log heuristic。

Legacy unavailable：

```text
schema, run_id, trades,
decision_evidence_complete=false,
decision_evidence_availability="unavailable"
```

legacy trades就算有 execution rows，都唔可以由 events或chart事後估四段因果；
UI顯示「歷史 run 無結構化逐筆因果證據」。

### 5.4 `events.v1`

Current complete exact：

```text
schema, run_id, events, evidence_complete,
rejection_evidence, evidence_summary
```

必須：

- `schema === "events.v1"`；
- exact run identity；
- `evidence_complete === true`；
- 不可有 `evidence_availability`；
- `evidence_summary.availability === "available"`且`complete === true`；
- rejection count、layer counts、blocking counts、deepest layer、trade count
  同完整 records重算一致；
- records按 `(evaluation_sequence, evidence_id)` ascending；
- sequence／evidence id唯一；
- 每個 blocker exact指向同record內一個 `status="failed"` fact；
- `not_evaluated` fact無 source sequence；
- passed／failed fact至少一個 source sequence；
- `ts_init >= timestamp`；
- summary `trade_count`同 main `trade_count` exact。

`events[]`每項 exact key set：

```text
sequence, timestamp, ts_init, phase, machine, event_type,
from_state, to_state, direction, price, details
```

sequence positive、unique、ascending；timestamps valid且`ts_init >= timestamp`；
nullable state／price保持null；`details`只接受plain JSON object。呢個驗證令
`evidence_summary.evaluation_count`可以同exact `signal_created`＋
`signal_rejected` event count重算，唔只信另一個counter。

每個 `rejection_evidence` exact key set：

```text
evidence_id, timestamp, ts_init, trading_date, direction,
evaluation_sequence, reached_layers, condition_facts,
blocking_condition_ids, context, source_event_sequences
```

`condition_facts`每項 exact
`condition_id/layer_id/observed_at/status/actual/operator/required/unit/
source_sequences`；`context` exact
`candidate_signal_kinds/inside_count/entry_pullback_state/
mid_pullback_state/daily_regime`。

Legacy unavailable exact：

```text
schema, run_id, events,
evidence_complete=false,
evidence_availability="unavailable"
```

呢個狀態唔等於 `rejection_evidence=[]`。Current complete加空
`rejection_evidence`先係「證據完整而真係零次拒絕」。

### 5.5 `chart_series.v1`

每個 D／1H／5m response exact驗：

```text
schema, run_id, timeframe, contract_id, session_name,
data_fingerprint, lookback_days, visible_start, visible_end,
candles, ema18, ema50, ema90, markers, levels,
source, sidecar_relpath, cache
```

identity：

```text
response.run_id === route runId
response.timeframe === requested tf
response.contract_id === validated main manifest contract_id
```

`cache`係normal HTTP `GET /api/v1/runs/{run_id}/chart`每個200 response嘅
**required、non-null JSON string**，closed enum exact只接受：

```text
memory
sidecar
write
write_stale_fingerprint
miss_no_write
```

missing、`null`、blank、非string或其他token全部invalid。Backend內部
`get_chart_series(..., use_cache=False)`另可產生`bypass`，但registered HTTP
route無傳呢個override，normal wire path不可到達；所以P5 parser必須拒絕
`bypass`，唔准當future-compatible value放行。

以上五個合法值都代表chart body已成功計算／取得；`cache`只係operational
provenance，唔改變candles／identity truth。尤其`miss_no_write`代表derived
sidecar寫入失敗但HTTP仍有完整200 chart，唔可以單憑呢個token將該格判成
unavailable。Stage 1無需向Owner顯示cache狀態，但parser必須驗證並可保留作
diagnostic。

candles、EMA points、markers、levels逐欄驗 finite number、Unix-second time、
position/token/text。candles同每條EMA各自strictly increasing且time unique；
markers按backend order保留並拒絕duplicate `(time,text)`；levels按backend
order保留。OHLC必須 `low <= open/close <= high`。單一 timeframe invalid只令
該格 unavailable。

`30m`唔發 request；預先建立 honest unavailable pane。payload無 volume時
`volume`保持 absent，唔建立零 volume array。

### 5.6 `narrative.v1`

Top-level exact：

```text
schema, run_id, count, steps, note
```

- schema exact；
- run identity exact；
- count exact；
- step exact `time/time_label/layer/tone/text`；
- `time`只接受 valid instant或`null`；
- strings非空；
- narrative只係 deterministic event-log補充，**唔可以代替**
  `decision_evidence`四段因果。

### 5.7 Optional strategy label catalog

Catalog只投影 `strategy_id → name`：

- `schema === "strategy_version_list.v1"`，count、versions array要runtime
  valid；
- count exact；
- 每個被採用 row嘅 `strategy_id`同`name`必須非空且identity唯一；
- invalid、duplicate、HTTP error全部捨棄整個label projection；
- fallback顯示 exact `strategy_version`；
- catalog結果永遠唔可以改 run identity、classification或ready/error狀態。

## 6. Presentation mapping

### 6.1 List

- 保留 backend row order；
- 只顯示 exact standard rows；
- zero同unknown分開；
- **唔准再把 `funnel_status="ok"`顯示成「截住：ok」**；
- 現行 compact list無 bottleneck layer，Stage 1 exact copy係：
  `0 成交 · 點入去睇阻擋條件`；
- 有交易顯示 backend metrics；null顯「未提供」；
- unread key綁 exact live run id。

因此 Stage 1 修正錯誤陳述，但未聲稱已滿足 P5-M03「列表直接寫被邊層截住」。
要喺列表補 exact layer，將來要由 compact list contract additive供應；本批唔做
N+1 events fetch，避免列表隨run數量放大I/O同RAM。

### 6.2 Main detail／funnel

- main validated後即可顯示 header、summary、scorecard、warnings同funnel；
- catalog失敗只令策略名fallback exact id；
- `daily_trend_days`保持日級；
- `evaluations_passing_*`保持評估級；
- `daily_total`現行不存在，顯「未提供」，唔用range日數或evaluation數代替；
- `funnel.status`係狀態，唔係判斷或bottleneck；
- main fatal時 child requests唔開始。

### 6.3 Trades

完整 evidence按 ordinal映射：

- direction → long／short；
- entry／exit UTC instant → browser local display，同時保留 exact UTC作chart focus；
- `net_pnl` → per-trade USD；
- entry condition facts按 `daily/mid/entry/execution`分組；
- 每個 fact顯 condition id、status、actual、operator、required、unit；
- stop顯 reference type／reference price／offset ticks／final stop；
- exit顯 selected candidate、其餘同bar candidates、resolution code；
- conservative assumptions逐項顯 code、applied、effects。

已知 condition id可以有人話label；未知 id必須顯示raw machine id＋exact facts，
唔當passed、唔隱藏。

現行 wire無 per-trade canonical net-R。`net_pnl`、entry、stop、quantity不足以
喺frontend計 net-R，因為缺 contract point value；所以 per-trade R顯「未提供」，
唔用 gross-points ratio冒充。run-level `metrics.net_r`照常顯示。

### 6.4 Owner 已選嘅「最接近三次」算法

只對 `evidence_complete=true`嘅 rejection records排序。canonical layer depth：

```text
daily = 0
mid = 1
entry = 2
execution = 3
```

Comparator依次：

1. 最深 reached layer較深者先；
2. distinct reached layer數較多者先；
3. blocking condition數較少者先；
4. `evaluation_sequence`較大者先；
5. `timestamp`較新者先；
6. `evidence_id` descending作最後 deterministic tie-break。

取 `min(3, rejection_count)`。唔夠三次時顯實際數量，唔複製record湊三次。

嚴禁：

- 將 points、percent、ticks、bars、price、boolean、enum等異質
  actual/required差值相減；
- 由condition名稱估距離；
- 將最新等同最接近而唔先做前三級結構排序；
- 將全部records截斷或改寫成backend artifact。

如果將來出現未獲批准嘅新 layer id，完整events仍可標示為收到，但
top-three ranking要進「排序規則未支援此 layer」狀態；唔將未知layer排到任意
位置。

目前14項真records前三級全部打和，結果exact係：

```text
rejection_000014
rejection_000013
rejection_000012
```

每項顯：

- event `timestamp`本地時間＋UTC hint；
- reached layers；
- blocker數；
- 每個blocking fact嘅 exact actual/operator/required/unit；
- focus time用該record `timestamp`，唔用browser猜bar。

判斷文案固定誠實表達：

```text
系統證據顯示：以上紀錄被列明條件截住。
系統未提供「邏輯正常／定義有分歧」判斷，需 Owner 判斷。
```

frontend唔自動輸出「邏輯正常」或「定義有分歧」。

### 6.5 Charts

- 四格順序固定 D／1H／30m／5m；
- D／1H／5m各自 loading／ready／unavailable；
- 30m固定顯示 backend未提供；
- volume固定顯示 backend未提供；
- 唔用1H／5m重採樣造30m；
- 唔用OHLC或空array造volume；
- trades ready後用 exact entry/exit timestamps enrich既有5m markers及
  D／1H垂直線；
- near-misses ready後用選中records exact timestamp enrich reject marker／
  focus；
- child未ready或失敗唔影響其他格；
- `ChartGrid` renderer、CSS、theme、crosshair contract維持凍結。

### 6.6 Narrative

掛載既有 `NarrativePanel`，保留 loading／error／empty／ready四態。
位置係charts之後、causal／zero-trade evidence之前。只顯backend deterministic
steps；唔將 narrative句子重寫成四段 trade evidence。

## 7. Identity、race與同步 reset

detail identity key：

```text
mode + "\0" + routeRunId + "\0" + generation
```

任何 async commit前逐項驗：

```text
component仍mounted
generation仍current
mode仍相同
route runId仍相同
response run_id exact
timeframe（如適用）exact
main contract identity（如適用）exact
```

route／mode改變時同步 reset：

- detail、main error/load；
- trades/events/narrative/chart child states；
- focus time、highlight；
- expanded／active trade；
- reason、decisions、decision note；
- export open/busy/error/ready；
- 舊 Blob URL即時 revoke；
- read-mark pending state。

必要race：

1. A pending → route B → B ready → late A：A零影響；
2. owner-review pending state → normal：fixture零洩漏、normal有真fetch；
3. normal pending → owner-review：request abort、owner零business fetch；
4. unmount → late response：零state commit；
5. chart D fail、1H／5m success：只D unavailable；
6. events fail：main、trades、narrative、charts照常；
7. strategy catalog fail：exact id fallback，main照常。

`markRunRead`只可喺validated main、current identity成立後做；404、503、
invalid schema、identity mismatch全部唔標已讀。

## 8. Error isolation與誠實 unavailable

| 資源 | 失敗影響 | UI語義 |
|---|---|---|
| run list | list fatal | 完整error，唔render stale rows |
| strategy label catalog | nonfatal | exact strategy id fallback |
| result main | detail fatal | 清舊detail；child唔開始 |
| trades | child only | 因果證據 unavailable/error，唔由events估 |
| events | child only | near-miss/evidence unavailable/error，唔由funnel估日期 |
| narrative | child only | panel error；唔影響因果證據 |
| D／1H／5m chart | pane only | 該格 unavailable；其他格保留 |
| 30m | known unavailable | 零request、明示未提供 |
| volume | known unavailable | 零fake series、明示未提供 |

known empty例子：

- complete trades＋`trades=[]`＋main trade_count 0；
- complete events＋`rejection_evidence=[]`。

unavailable例子：

- legacy `decision_evidence_complete=false`；
- legacy `evidence_complete=false`；
- 30m／volume現行contract缺失。

error例子：

- wrong schema/count/identity；
- 503 integrity failure；
- current complete bundle內部reconciliation失敗。

三者唔可以用同一句「冇資料」混埋。

## 9. Owner-review與跨頁保護

owner-review：

- backend business fetch = 0；
- backend／persistent business write = 0；
- fixture list/detail/chart/trades/near-miss/export/decision語義原封保留；
- local owner read/decision session唔同normal key混用；
- Stage 1 parser唔改 fixture shape、bytes或owner copy。

跨頁：

- P4 completed `job.run_id`原字串進P5 route；
- P5每個child用同一 exact route id；
- P5→P2只用validated `strategy_version`；
- P2只 exact-match version，missing唔fallback第一行；
- query/back history保留現有filter；
- P6 link／eligibility唔喺Stage 1啟用。

## 10. 實作檔案邊界

現行Y-v5 Stage 1工作令只准：

```text
apps/web/src/api/client.ts
apps/web/src/lib/results/normalContract.ts                 (new)
apps/web/src/lib/results/normalContract.test.ts            (new)
apps/web/src/lib/results/types.ts
apps/web/src/pages/ResultsPage.tsx
apps/web/src/pages/ResultsPage.test.tsx
apps/web/src/pages/RunDetailPage.tsx
apps/web/scripts/run-mutations-p5-normal-read.mjs          (new)
```

如果實作證明要新增一個純P5 mapping test file，Y先出 `QUESTION`，由C書面加scope。

預設凍結：

```text
apps/web/src/components/chart/ChartGrid.tsx
apps/web/src/components/chart/NarrativePanel.tsx
apps/web/src/components/results/**
apps/web/src/lib/results/fixtureStore.ts
apps/web/src/lib/results/timeIdentity.ts
apps/web/src/styles/**
src/**
tests/**
config/**
data/**
docs/ui/designs/**
```

掛載既有 `NarrativePanel`唔需要改component本身。所有人話mapping放
P5 normal adapter／page，唔污染backend machine facts。

## 11. Required tests

### 11.1 Parser／mapping

1. exact run list success；
2. wrong schema、extra/missing key、wrong count、duplicate run id；
3. missing／non-boolean `validation_run`；
4. null同zero metrics分開；
5. compact／enriched／current result profiles；
6. route/main/manifest run identity mismatch；
7. validation run direct URL fail closed；
8. current complete trades四段 evidence及main count reconciliation；
9. legacy trade evidence unavailable唔冒充empty；
10. complete events known-empty同legacy unavailable分開；
11. rejection summary重算、blocker→failed fact、sequence/order；
12. approved comparator所有tie級；
13. 現行14-record fixture exact揀 `000014/000013/000012`；
14. 異質numeric值改動唔影響rank；
15. unknown condition id顯raw facts；
16. unknown layer唔任意rank；
17. chart wrong run/timeframe/contract、invalid OHLC、nonfinite point；
18. chart五個external cache token全部接受；missing／null／blank／
    `bypass`／unknown token全部拒絕；
19. narrative wrong count/identity；
20. per-trade R顯未提供，唔用gross ratio；
21. `funnel_status=ok`唔顯「截住：ok」。

### 11.2 Mounted behavior

1. list run success＋catalog success；
2. list success＋catalog 404／503／invalid → exact id fallback；
3. list invalid → fatal、零stale rows；
4. normal main valid後children獨立loading／ready；
5. trades/events/narrative各自失敗隔離；
6. D／1H／5m一格失敗隔離；
7. 30m同volume明示unavailable、request count 0；
8. A→B late response；
9. normal↔owner mode switch；
10. unmount abort；
11. mark read只喺main identity verified後；
12. near-miss click用exact timestamp跳圖；
13. trade click同exact ordinal／timestamp同步；
14. P4 exact run route同P5→P2 exact strategy link；
15. owner-review全journey fetch 0、persistent business write 0；
16. owner fixture DOM/copy/decision/export regression不變。

### 11.3 Mutations

mutation runner逐項單獨改、指定test先RED、byte-exact restore、再GREEN：

1. 移除run list schema check；
2. 移除count reconciliation；
3. 將 `validation_run === false`放寬成truthiness；
4. 移除detail expected run guard；
5. 移除chart timeframe guard；
6. 移除generation guard；
7. 將legacy unavailable當known empty；
8. comparator跳過layer depth；
9. comparator用actual-required numeric distance；
10. catalog failure重新拖垮run list；
11. `markRunRead`移到main驗證前；
12. 30m由1H資料複製；
13. 移除chart `cache` required closed-enum guard。

## 12. 驗證負荷與真資料保護

Y實作驗證：

- parser＋mounted focused tests；
- P4／P5 protected tests；
- full web suite一次，最多2 workers；
- typecheck、lint、production build；
- mutation逐項串行，唔並行開多個Vitest；
- `git diff --check -- apps/web`。

本批測試全部mock API；禁止：

- 開backend真server；
- call真chart GET；
- 跑歷史engine／batch／run；
- 真export；
- PromotionDecision；
- migration；
- IB Gateway／TWS／IBC。

真 browser smoke要等Y REPORT＋C source/test review後，由C另出窄書面令並取得
Owner授權。原因係真chart GET可能lazy-write derived cache。該smoke要有
`data/` files／bytes／protected SHA、DB、listeners事前事後facts；唔可以喺
Stage 1實作批自行順手做。

## 13. 收貨標準與後續次序

Stage 1 PASS需要：

1. 所有normal body由unknown經runtime parser；
2. list/detail/child identity exact；
3. main fatal、children隔離；
4. route/mode/generation race無stale commit；
5. owner-review零backend business fetch/write；
6. known empty／legacy unavailable／error分開；
7. top-three按Owner方案A exact；
8. 30m、volume、system judgment、per-trade net-R、compact bottleneck全部
   誠實 unavailable，零造數；
9. required tests/mutations/verification PASS；
10. Y提交REPORT後進EXPLICIT HOLD，等C獨立REVIEW。

次序：

```text
Y按C Stage 1 exact work order做code/tests/REPORT
  → C獨立REVIEW
  → 如PASS，C先設計／發Stage 2 export＋decision/history
```

X保持support hold；Stage 1無backend-first blocker。若Y用source證據發現現行
response同本spec矛盾，停低出 `QUESTION`，C先決定係parser修正定X窄
correction，唔自行改backend或放寬truth。

P6、洞察刪除仍等Owner另批。IB Gateway未授權，唔開。

## 14. 自我審查

- 無 TBD／TODO／待定 comparator；
- Owner方案A已寫成完整deterministic order；
- 異質單位無被比較；
- current 14-record結果已列 exact；
- system judgment無偽造；
- list `funnel_status=ok`無冒充bottleneck；
- per-trade net-R無用gross ratio冒充；
- 30m／volume無造數；
- result main先行、children隔離；
- abort＋generation＋exact identity三層race guard齊；
- owner-review isolation齊；
- genuine legacy同known-empty分開；
- Stage 1同irreversible Stage 2分開；
- 真chart lazy-write另設授權閘；
- P6／洞察刪除／IB全部保持hold；
- written-spec review已完成；產品工作只認C-v5書面工作令。
