# P4 Backtest Precheck, Progress, and Queued-Cancel Design

日期：2026-07-27

狀態：**Owner 2026-07-27 已完成 written-spec review 並正式批准；現為 P4-A／P4-B
實作計劃與 C 驗收權威。**

範圍：P4 回測頁嘅三項開始前檢查、標準回測提交閘、每交易日進度、只取消
queued 單元，以及相應嘅前後端 seam。呢份規格分兩個 backend 批次：

1. **P4-A**：數據覆蓋、暖機、exact duplicate、提交／開跑前重驗；
2. **P4-B**：逐交易日進度、五狀態總數、queued-only cancel。

六份 `docs/ui/designs/*.html` 仍然係畫面權威；本文件只落實 P4
約束 #5–#14、#17 嘅 backend truth。**唔重畫 P4、唔開 P5／P6、唔開 IB
Gateway、唔執行真 DB migration。**

## 1. Owner 已拍板嘅產品語義

### 1.1 暖機係 run 前歷史，唔係 run 內偷睇

現行 engine 只可用 `range_start` 之前、已收市嘅原生 settlement Daily bars
做 compiled indicators 同 percentile calibration。選定回測範圍內嘅日子唔可以
倒灌返去做該次 run 嘅 pre-run calibration。

因此，暖機不足時正確下一步係：

- 回填更早嘅原生日線資料；或
- 將回測開始日**推後**，令原本較早嘅日子變成 run 前歷史。

「將開始日推早」會減少 pre-run 歷史，方向相反，唔准再出現喺 UI、文件或 API
reason。

目前 Trend engine 嘅 exact 最低門檻唔係單純「EMA90 = 90 日」：

- EMA90 要 ready；
- Daily slope 要再望返 5 個已 ready snapshots；
- 至少要有一個可供 calibration 嘅 sample。

所以現行兩個 Trend 變體嘅 exact boundary 都係 **95 個合資格、已收市、run 前
原生日線交易日**。18EMA／90EMA 只係 entry pullback 差異；兩者仍共用
EMA90 Daily regime，唔准因策略名有「18EMA」就錯報 18 日。

未來新增策略時，required days 必須由已解析 `StrategySpec` 加實際 engine
indicator dependencies 推算；唔准由 display name、frontend 常數或單一 YAML
欄位估。

### 1.2 P3 Owner data decision 真正影響 replay

每個交易日按 P3 exact facts 分類：

- **complete**：可用；
- **problem + Owner trust**：用現有 bars，風險已由 Owner 接受；P4 要清楚提示；
- **problem + Owner exclude**：整個 trading date 唔入 replay；
- **problem 未裁決／missing／native daily missing／unknown**：阻止該單元開始；
- 所有日子都被排除：阻止該單元開始。

Owner exclude 唔係只隱藏警告。該日 minute bars、native Daily input、進度日數同
PnL 全部唔可以消費；實際排除日期要以 deterministic、sorted、unique union 寫入
既有 `RunManifest.excluded_trading_dates`：

```text
configured roll blackout ∪ current Owner-excluded trading dates
```

舊 manifest／run／result bytes 唔改。新 run 嘅 `data_fingerprint` 只覆蓋真正 admitted
bars。

### 1.3 Duplicate 只認 exact standard run identity

只喺以下四項完全相同時算 duplicate：

```text
strategy_version
root symbol
normalized UTC range_start
normalized UTC range_end
```

規則：

- `validation_run === true` 永遠唔算 P4 duplicate；
- costs、slippage、initial capital 唔屬 duplicate identity；
- 四項任一改動就唔係同一 duplicate；
- index 未 migration、proof 不完整或有 relevant unindexed candidate 時係
  `unknown`，唔係「0 次」；
- Owner 可以對**該 exact identity**確認「照跑」，唔可以用一個永續 global flag
  放行之後所有組合；
- 表單 identity 改動後，frontend 必須清除舊確認；backend 亦要按 request 內 exact
  identity 重驗，唔信任 browser state。

### 1.4 P4 永遠提交 standard run

Owner UI 唔顯示 `validation_run`，亦唔提供 strategy parameter override。P4 normal
submit 必須係 `validation_run=false`。工程 validation run／strategy override 仍只供
CLI 或測試，唔係 P4 畫面功能。

每個「策略 × 合約」係獨立帳戶，各自用完整 initial capital，唔共同攤分。
每個新 run 要鎖住：

- `initial_capital`；
- 該 symbol 嘅 `commission_per_side`；
- breakout／stop／target／day-end 四種 slippage ticks；
- 系統唯讀 `target_requires_through`、conservative fill model、一分鐘精度；
- 現行 manifest 已有嘅 quantity、strategy binding、session 同 UTC range。

Owner 只可改設計稿 #17 允許嘅 initial capital、所選合約手續費、四種滑點。
strategy risk、daily loss limit、quantity policy、fill ordering 同 strategy parameters
都唔可以由 P4 request 覆蓋。

## 2. 已選架構

採用 **獨立 read-only composite precheck endpoint + 共用 truth service + submit／job
start 重驗**。

```text
P4 form
  │
  ├─ POST /api/v1/batches/precheck
  │    └─ BacktestAdmissionService.evaluate(matrix)
  │          ├─ P3 exact coverage facts
  │          ├─ strategy/session/warm-up truth
  │          └─ standard-run duplicate index
  │
  ├─ POST /api/v1/batches/submit
  │    └─ same evaluate(matrix) → pass/warn only先建 batch
  │
  └─ worker 將 queued → running 之前
       └─ same evaluate(one exact cell) → 再取得 RunAdmissionPlan
            └─ BacktestRunner consumes exact admitted/excluded date plan
```

呢個設計避免：

- frontend 自己用 bars、策略名或日期數計答案；
- precheck endpoint 同 submit 各自一套規則；
- 畫面檢查完後，數據 decision／duplicate truth 已變但仍照舊開跑；
- P3 full route 預設 ETH projection 被錯用於 RTH strategy；
- quality gate disabled 嘅 batch runner 繞過 P3 Owner decision。

### 2.1 已否決方案

1. **Frontend 聚合現有 endpoints**：會重造 session mapping、warm-up 同 aggregate
   規則；亦無法令 submit 原子地用同一真相。
2. **Submit 只信 precheck token／browser acknowledgement**：token 之後 truth
   可以改；job 等候期間亦可以新增 duplicate 或 data decision。
3. **Runner 完成後掃 partial artifacts 推算進度**：現行 immutable artifacts 只可
   成功完成後出版；中途掃檔會逼系統留下半寫 artifact。
4. **每 bar callback**：I/O 過密、UI 無額外價值；權威要求係每交易日。
5. **取消 running job**：會破壞 immutable replay 邊界。MVP 只取消未開始單元。

## 3. P4-A Composite Precheck Contract

### 3.1 Endpoint

```http
POST /api/v1/batches/precheck
Content-Type: application/json
```

request 使用同 submit 相同嘅 P4 standard-run envelope：

```json
{
  "strategy_versions": ["strategy-0003", "strategy-0004"],
  "symbols": ["NQ", "YM"],
  "range_start": "2026-05-01T00:00:00Z",
  "range_end": "2026-07-22T21:00:00Z",
  "execution_assumptions": {
    "initial_capital_usd": 100000,
    "commission_per_side_by_symbol": {
      "NQ": 2.5,
      "YM": 2.5
    },
    "slippage_ticks": {
      "breakout_entry": 1,
      "stop_exit": 2,
      "target_exit": 0,
      "day_end_exit": 1
    }
  },
  "duplicate_acknowledgements": []
}
```

validation：

1. strategy／symbol arrays 非空、值非空、大小寫 canonical、不可重複；
2. timestamps 必須有 timezone；normalize UTC 後 `start < end`；
3. initial capital finite 且 `> 0`；
4. 每個 selected symbol 必須 exact 有一個 finite、`>= 0` commission；
5. 不可有未選 symbol 嘅 commission entry；
6. 四個 slippage 值全部係 integer、`>= 0`；
7. unknown／extra fields fail 422，唔 silent ignore；
8. `validation_run`、strategy override、quantity、session override、
   `skip_nautilus_replay` 唔屬 P4 envelope，傳入即 422；
9. session 逐 strategy 由 confirmed `StrategySpec.universe.session` 解析；
10. symbol 必須屬該 strategy confirmed universe，否則該 cell `block`，唔偷偷
    drop matrix cell。

`duplicate_acknowledgements` 每項係完整 structured identity：

```json
{
  "strategy_version": "strategy-0003",
  "symbol": "NQ",
  "range_start": "2026-05-01T00:00:00Z",
  "range_end": "2026-07-22T21:00:00Z"
}
```

只接受 canonical exact match、不可重複、不可帶 matrix 以外 identity。呢個 list
只係「Owner 知道係 duplicate 仍照跑」嘅表示；backend 仍會重新查 index。

### 3.2 Response

有效 request 無論 pass、warn、block 或 unknown，都回 200 同完整 structured
document：

```json
{
  "schema": "backtest_precheck.v1",
  "checked_at": "2026-07-27T08:00:00Z",
  "overall_status": "warn",
  "can_submit": true,
  "unit_count": 4,
  "units": []
}
```

`overall_status` 只可係：

```text
pass | warn | block | unknown
```

aggregate precedence：

1. 任何 unit/check `unknown` → overall `unknown`、`can_submit=false`；
2. 否則任何 unit/check `block` → overall `block`、`can_submit=false`；
3. 否則任何 trusted data、excluded dates、warm-up shortage 或已確認 duplicate
   → overall `warn`、`can_submit=true`；
4. 否則 `pass`、`can_submit=true`。

每個 unit：

```json
{
  "strategy_version": "strategy-0003",
  "symbol": "NQ",
  "contract_id": "NQ-202609-CME",
  "session_name": "eth",
  "range_start": "2026-05-01T00:00:00Z",
  "range_end": "2026-07-22T21:00:00Z",
  "status": "warn",
  "reason_codes": [],
  "coverage": {},
  "warmup": {},
  "duplicate": {}
}
```

API 只回 stable reason codes 同 facts；人話由 Y 根據設計稿映射。backend 唔回
HTML、local path、traceback 或 UI sentence。

### 3.3 Coverage object

minimum contract：

```json
{
  "status": "pass",
  "requested_trading_date_count": 58,
  "admitted_trading_date_count": 57,
  "complete_trading_dates": [],
  "owner_trusted_problem_trading_dates": [],
  "owner_excluded_trading_dates": [],
  "roll_blackout_trading_dates": [],
  "excluded_trading_dates": [],
  "blocking_problem_trading_dates": [],
  "missing_native_daily_trading_dates": [],
  "reason_codes": []
}
```

規則：

- trading date 一律係 strategy session 嘅 exchange-local session-end label，唔用
  browser local date；
- 同一 contract physical market/native data 每個 request 只掃一次；
- 多個 strategy session projection 從同一 read result 計，唔重掃；
- complete 同 trusted problem 合成 admitted set；
- Owner excluded 同 roll blackout 合成 `excluded_trading_dates` union；兩個 source arrays
  保留 provenance，但 aggregate/reconciliation 只用 union，避免重疊日重複計；
- pending problem／missing minute／missing required native Daily／unmapped／corrupt
  令 unit block 或 unknown；
- counts 必須同 arrays reconciliation；
- `admitted + excluded union + blocking` 要覆蓋 exact requested candidate dates；三個
  aggregate sets 之間唔可有重疊；
- `admitted=0` 必須 block；
- trusted date 要 warning，唔可扮 complete；
- exclude 要 warning並列日子，唔可扮「冇資料」。

minimum stable reason codes：

```text
coverage_complete
coverage_owner_trusted
coverage_owner_excluded
coverage_roll_blackout
coverage_pending_problem
coverage_minute_missing
coverage_native_daily_missing
coverage_all_dates_excluded
coverage_unknown
```

### 3.4 Warm-up object

```json
{
  "status": "warn",
  "required_prior_trading_date_count": 95,
  "available_prior_trading_date_count": 64,
  "evaluable_trading_date_count": 0,
  "first_evaluable_trading_date": null,
  "suggested_range_start": "2026-06-15T22:00:00Z",
  "reason_codes": ["warmup_short"]
}
```

計法：

1. 由 exact strategy/engine dependency graph 得到 required count；
2. 只計 session close `<= range_start` 嘅已收市原生日線；
3. Owner-excluded 同 roll-blackout dates 唔計；
4. future bars、run 內 bars、未收完今日日線一律唔計；
5. 現行 Trend boundary 以 94／95 fixture 鎖死：94 short、95 sufficient；
6. `evaluable_trading_date_count` 係 engine 真正有 calibrated Daily gate 可評估嘅
   admitted run dates，唔係 `range days - 90` 嘅 UI 算術；
7. known short 係 `warn`，Owner仍可跑出可審計 zero-trade／under-warm result；
8. history/read/dependency 無法證實先係 `unknown` 並阻止；
9. `suggested_range_start` 只可係「將開始日推後」嘅最早 exact UTC boundary；
   如果本機連足夠較後歷史都冇，回 null，UI 只建議 backfill；
10. API 唔建議換策略；呢個係 Owner產品決定，唔係 data truth。

stable reason codes：

```text
warmup_sufficient
warmup_short
warmup_unknown
warmup_no_evaluable_dates
```

### 3.5 Duplicate object

```json
{
  "status": "block",
  "count_known": true,
  "exact_match_count": 1,
  "prior_run_ids": ["nq-20260723-standard-001"],
  "unindexed_candidate_count": 0,
  "acknowledged": false,
  "reason_codes": ["duplicate_exact_match"]
}
```

規則：

- `exact_match_count=0` 只可以喺 `count_known=true` 時聲稱；
- exact match、未 acknowledgement → block；
- exact match、exact acknowledgement → warn/can submit；
- acknowledgement identity已唔再係 current exact duplicate時視為 stale request：
  precheck列明 `duplicate_acknowledgement_stale`，submit回409；唔 silent保留或放行；
- unmigrated index、proof checksum failure、relevant unindexed candidate、cross-source
  collision → unknown；
- prior runs 排 stable order，validation rows 全部排除；
- 只用 verified run-reference index，唔逐個掃 result/manifests 做第二套查詢；
- precheck 不得自行 migration；
- 真 main DB 未經 Owner activation gate 前，正常 UI 可以如實顯示 duplicate
  `unknown`，但唔准假 0。

stable reason codes：

```text
duplicate_none
duplicate_exact_match
duplicate_acknowledged
duplicate_acknowledgement_stale
duplicate_index_unavailable
duplicate_identity_unproven
duplicate_catalog_integrity_error
```

## 4. P4-A Submit and Job-Start Revalidation

### 4.1 Submit

`POST /api/v1/batches/submit` 接受同 §3.1 exact envelope。收到後：

1. 重新執行同一 `BacktestAdmissionService.evaluate()`；
2. `block`／`unknown` 時回 409；
3. 409 body 帶同一份 `backtest_precheck.v1` facts，frontend 可直接刷新畫面；
4. pass／warn 先展開 exact strategy × symbol matrix；
5. 每個 cell 各自保存完整 execution assumptions snapshot；
6. batch JSON 成功 atomic write 後先可見於 in-memory queue；
7. persistence 失敗時零 batch、零 job、零 run/result artifact；
8. request arrays 有 duplicate 時 422，唔 silent deduplicate；
9. response 係 §6 定義嘅 `batch_job.v2`。

P4 browser submit 永遠 standard。既有 engineering validation path 可以保留，但唔
可以經呢份 P4 standard envelope 混入，亦唔可以出現喺 Owner UI。

### 4.2 Job-start

worker 取得 queued cell 後，喺同一 queue lock 下只做狀態 claiming；真正 runner
開始前必須：

1. 對該 exact cell 重新 evaluate；
2. 取得 immutable in-memory `RunAdmissionPlan`：
   - resolved strategy/spec/binding/session；
   - canonical UTC range；
   - admitted／excluded trading dates；
   - exact execution assumptions；
   - duplicate acknowledgement truth；
3. block／unknown 時只將該 cell 標 failed，runner call count = 0；
4. 其他 queued cells 繼續；
5. pass／warn 時 runner 只消費 plan admitted dates；
6. manifest `excluded_trading_dates` 寫入 plan 排除 union；
7. manifest costs 同 capital exact 等於該 cell snapshot；
8. 任何 runner failure 出版 0 個 partial immutable artifact。

三個時點——預檢、submit、job-start——必須共用同一 core。只改壞其中一路嘅
mutation 必須令指定測試變紅。

## 5. P4-B Day-Level Progress

### 5.1 Engine observer boundary

`BacktestRunner` 加 optional、default `None` 嘅 read-only day observer。非 batch
caller 唔傳 observer 時，現有 final run/result/trades/equity/events bytes 同 hashes
必須 exact 不變。

callback 每個**真正 admitted、已完整處理並完成 day-end**嘅 trading date 只 call
一次，包括 0 trade day；排除日同 blocking 日永遠唔 call。callback 時點係：

```text
該日最後一個 admitted minute 已處理
→ strategy.end_day
→ execution.end_session
→ observer(snapshot)
→ 下一交易日
```

snapshot：

```json
{
  "current_trading_date": "2026-05-08",
  "processed_trading_date_count": 12,
  "total_trading_date_count": 57,
  "trade_count": 3,
  "realized_net_pnl_usd": 420.5,
  "realized_net_r": 1.37,
  "reported_at": "2026-07-27T08:05:00Z"
}
```

語義：

- date 係 exchange trading-date label，唔 timezone-shift；
- counts 係 cumulative admitted dates；
- trade/PnL/R 只計已 closed trades，open position unrealized PnL 唔顯示；
- `realized_net_r` 必須重用 final metrics 嘅 exact per-trade net-R 定義：
  `net_pnl / (abs(entry_price - stop_price) × point_value × quantity)`；
- 最後一個 snapshot 嘅 trade count／USD／R 要同 final `result.v1.metrics` exact
  相等；
- callback/persistence failure fail該 job、零 partial immutable artifact；唔可以
  靜靜繼續但留假進度。

### 5.2 Operational persistence

queue 收到 snapshot 後，在 lock 內更新該 job，atomic replace batch operational JSON。
呢份 JSON 可變，唔係 immutable research artifact。

queued job：

- progress fields 全部 null；
- trade/PnL 唔扮 0。

running 但第一日未完成：

- processed = 0；
- total 已知；
- trade_count = 0；
- realized USD/R = 0；
- current trading date = null。

completed：

- 最後 progress facts保留；
- result path/run id 可去 P5。

failed：

- 保留最後一個成功 day snapshot；
- 另有 `error_summary` 同 `error_full`。

browser reload 重新 GET 同一 batch 時，要見返 persisted progress/cancel states。MVP
唔用 WebSocket/SSE；normal page polling 已足夠。**呢項只承諾 browser reload 同
同一 server lifetime。** server process crash 後嘅 stale-running detection、
artifact reconciliation同自動 resume 係另一份 crash-recovery設計，唔屬今批；
P4-B REPORT要把呢點列為 known limit，唔准聲稱已有 process-restart recovery。

### 5.3 Error fields

`error_summary` 係 stable、人話可映射摘要；`error_full` 係 exception type + 完整
cause chain 嘅純文字。`error_full`：

- 不含 HTML；
- 不含 UI prefix；
- 不含 traceback file paths／line numbers；
- 不吞 inner cause；
- frontend「複製全部錯誤」exact 複製呢個值。

一個失敗唔影響其他 cell。soft chart sidecar warning 仍係 warning，唔假裝 job
failed。

## 6. Batch Job v2 Contract

新增 status：

```text
job: queued | running | completed | failed | cancelled
batch: queued | running | completed | failed | cancelled | partial
```

`GET /api/v1/batches/jobs` 回 `batch_job_list.v2`；
submit、detail、cancel 回 `batch_job.v2`。每個 job minimum additive fields：

```json
{
  "status": "running",
  "progress": {
    "current_trading_date": "2026-05-08",
    "processed_trading_date_count": 12,
    "total_trading_date_count": 57,
    "trade_count": 3,
    "realized_net_pnl_usd": 420.5,
    "realized_net_r": 1.37,
    "reported_at": "2026-07-27T08:05:00Z"
  },
  "error_summary": null,
  "error_full": null,
  "cancelled_at": null,
  "assumptions": {
    "initial_capital_usd": 100000,
    "commission_per_side": 2.5,
    "slippage_ticks": {
      "breakout_entry": 1,
      "stop_exit": 2,
      "target_exit": 0,
      "day_end_exit": 1
    },
    "target_requires_through": false,
    "fill_model": "conservative",
    "bar_precision": "1m"
  }
}
```

summary 必須有五個 exact counts：

```json
{
  "total": 4,
  "queued": 2,
  "running": 1,
  "completed": 1,
  "failed": 0,
  "cancelled": 0
}
```

永遠滿足：

```text
queued + running + completed + failed + cancelled == total
```

batch aggregate：

- 全 queued → queued；
- 有 queued/running → running；
- 全 completed → completed；
- 全 failed → failed；
- 全 cancelled → cancelled；
- terminal states混合 → partial。

舊 persisted `batch_job.v1` 要 read-compatible，GET 時 normalize 做 v2 response；
唔改寫舊 JSON bytes。未知舊 status fail-closed，唔當 queued 重新執行。

ETA：

- 未有足夠 completed-day samples 時 null；
- 有足夠 samples 先可由實測平均推估；
- 唔准用固定假分鐘；
- ETA 只係 operational hint，唔入 immutable result。

## 7. Queued-Only Cancel

### 7.1 Endpoint

```http
POST /api/v1/batches/jobs/{batch_id}/cancel-queued
```

無 request body；回最新 `batch_job.v2`。

同 worker claim job 使用**同一把 lock**：

- cancel 先攞 lock：所有 queued → cancelled，worker永遠唔執行佢哋；
- worker 先將一個 queued → running：該 running 保留，其餘 queued → cancelled；
- completed／failed／cancelled 不變；
- running 永遠不變。

endpoint idempotent：

- 第一次取消 N 個；
- 再 call 回 200 同現況，唔改 running/terminal facts；
- queued=0 時掣可 disabled，但 endpoint本身仍安全。

每個 cancelled job：

- `status=cancelled`；
- `finished_at`／`cancelled_at` 寫 canonical UTC；
- progress 保持 null；
- runner、run store、result exporter call count = 0。

unknown batch 404；operational JSON atomic write 失敗時，內存狀態要 rollback，唔可
回成功。

## 8. Error and HTTP Rules

| 情況 | HTTP／結果 |
|---|---|
| malformed shape、extra field、naive time、invalid numeric | 422 |
| valid precheck，語義 block／unknown | 200 structured `backtest_precheck.v1` |
| submit recheck block／unknown | 409，body 帶同一 precheck facts |
| unknown batch id | 404 |
| endpoint-wide truth service／persistence integrity failure | 503、零新 write |
| one queued cell job-start recheck／runner failure | 該 cell failed；其他繼續 |

任何 fail-closed path：

- 唔建立假 known-zero；
- 唔出版 partial run/result/sidecar；
- 唔執行 migration／download／IB call；
- 唔將 exception path／traceback 洩漏俾 UI。

## 9. Performance

用 Owner 現有三個合約 snapshot：

- complete composite precheck fresh process **≤ 15.0 秒**；
- 同一 request 每個 selected contract minute/native physical source最多掃一次；
- 多策略／多 session只重算 projection，唔重掃 physical data；
- duplicate 只查 verified index，唔掃 16 個 result main／manifest JSON；
- 禁止未證明 full invalidation 嘅 cache；
- precheck、submit同 job-start core可以共享一次 request 內 read，但不可跨變更回
  stale truth。

REPORT 要列三次 fresh-process wall-clock；三次都過上限。做唔到就停低出
`QUESTION`，唔改 truth、唔加 stale cache。

## 10. Verification and Mutations

### 10.1 P4-A required tests

1. Trend exact 94 short／95 sufficient boundary；
2. 18EMA strategy仍由 Daily regime推得 95，唔係18；
3. range內 future Daily 完全唔影響 pre-run warm-up；
4. 將 start 推早唔會增加 available prior count；推後到 exact boundary先 sufficient；
5. ETH／RTH 同一 physical read、唔同 exact trading-date projection；
6. DST、overnight session、host timezone切換後日期結果相同；
7. complete、trusted、excluded、pending、native missing、all excluded 各自語義；
8. trusted日真正入 replay；excluded日 minute/native/progress全部唔入；
9. manifest excluded union sorted/unique；舊 manifest仍可讀；
10. standard exact duplicate match；validation run排除；
11. strategy/symbol/range任一 drift 清 duplicate identity；
12. acknowledgement只放行 exact identity；
13. unmigrated／unproven index → unknown、零 write；
14. precheck POST係 read-only；writer/download/migration/IB call = 0；
15. submit同 job-start各自重新驗；truth drift fail該 exact範圍；
16. one cell failure不阻其他 cell；
17. assumptions每個 cell full capital，不除以 unit count；
18. per-symbol commission同四種 slippage exact入 manifest；
19. standard request帶 validation/strategy override/quantity/session override 422；
20. submit persistence failure零 visible batch。

### 10.2 P4-B required tests

1. observer `None` 前後 final result/trades/equity/events hashes exact；
2. observer每 admitted trading date一次，0-trade day亦有；
3. excluded/blackout date callback = 0；
4. processed/total monotonic且 final exact；
5. progress trade count、net USD、net R同 final metrics exact；
6. running first-day-before-close係 0/null contract；
7. observer/persistence failure零 partial immutable artifact；
8. queued cancel、running保留、terminal不變；
9. cancel/worker race兩個 deterministic order；
10. cancel idempotent；
11. reload保留 cancelled同最後 progress；
12. 五狀態 conservation每一步成立；
13. all-cancelled batch = cancelled；mixed terminal = partial；
14. failed `error_full`有 cause chain、冇 HTML/path/line number；
15. one failed job唔阻之後 queued job；
16. v1 operational JSON read-only normalize到 v2，原 bytes不變。

### 10.3 Required mutations

至少實跑並證明指定測試 RED：

1. warm-up hardcode 90；
2. warm-up誤計 range內 future bars；
3. trusted problem當 block；
4. excluded day仍送入 runner；
5. duplicate lookup包括 validation run；
6. unmigrated index當 known zero；
7. submit跳過 shared precheck；
8. job-start跳過 recheck；
9. observer喺 day-end 前報；
10. progress R用 gross PnL；
11. cancel將 running 改 cancelled；
12. summary漏 cancelled；
13. mixed terminal錯報 completed；
14. assumptions將 USD 100,000 除以 matrix count。

全部 mutation 還原後再跑 full suite。

### 10.4 Regression／protected scope

- backend focused + full `pytest`；
- `ruff check src tests`；
- `mypy src`；
- Y seam到時另跑 web focused/full、typecheck、lint、build；
- `data/strategies/strategy-0001/0002` bytes + SHA-256 不變；
- 15+1 historical DB rows、16 result mains同既有 artifacts不變；
- runs/trades immutable triggers不變；
- 六份權威 design未經 Owner written-spec review唔改；
- X唔掂 `apps/web/`；Y唔掂 `src/`、Python tests、migration、真 data。

## 11. Delivery Order

### 11.1 X P4-A

只做：

- composite precheck models／route；
- shared admission service；
- strategy-session warm-up truth；
- P3 coverage/Owner decision integration；
- duplicate index integration；
- standard execution assumptions snapshot；
- submit + job-start shared revalidation；
- Owner-excluded replay/manifests；
- tests、mutations、read-only performance。

唔做：

- progress observer；
- cancel；
- frontend；
- true DB migration；
- IB／download；
- P5／P6。

### 11.2 X P4-B

P4-A 經 C REVIEW 先做：

- optional engine day observer；
- `batch_job.v2` progress/status/error contract；
- operational atomic persistence；
- queued-only cancel；
- v1 read compatibility；
- tests/mutations。

### 11.3 Y-v3 Normal P4 Seam

X P4-A同P4-B都經 C REVIEW 先另出 Y 工作令。Y只可以：

- normal mode消費 precheck；
- structured reason code映射現有 P4人話；
- submit exact standard envelope；
- poll v2 progress；
- 接 queued-only cancel；
- 保持 Owner-review fixture完全隔離；
- 保持六份權威稿 layout。

Y唔計 coverage/warm-up/duplicate truth，唔 fallback fixture，唔重畫 P4。

## 12. Activation Gate

P4 code review完成後，真 duplicate index仍有獨立 Owner gate：

1. backup `data/backtests/runs.sqlite3`；
2. 記錄 runs/trades rows、四個 immutable triggers、DB SHA/facts；
3. 只執行既有 explicit migration command；
4. migration後 immutable facts exact；
5. 真 precheck由 unknown轉 known；
6. 跑一次 standard P4 → P5 smoke；
7. 再核對 result.v1同 historical artifacts。

未到呢一步唔使開 IB Gateway。migration同 IB係兩件事；P4歷史回測使用本機
canonical data。

## 13. Written-Spec Approval 後文件同步

Owner批呢份 written spec後、X開工前，C要：

1. 修正 `p4-backtest.html` 暖機例子，將錯誤「start推早」改為
   「backfill更早資料／start推後」；
2. 保留 layout，更新約束 #6文字為 engine-derived exact warm-up；
3. 重新產生 matching `p4-backtest.png`；
4. project同 Desktop各放一套 matching HTML／PNG；
5. 更新 `docs/03-frontend-component-coverage-matrix.md`、`docs/08-ui-backend-mapping.md`、
   `docs/PROJECT_STATE.md`同 X/Y briefing現況；
6. 移除或歸檔任何仍叫 executor用 90-day UI estimate、將 start推早或由 frontend
   自計 precheck嘅 active instruction；
7. 再出 X P4-A正式工作令。

呢次同步只修 truth/copy同實作狀態，**唔改已批准 layout**。

## 14. Self-Review Checklist

- 無 TBD／TODO／未裁決欄位；
- warm-up方向同 no-lookahead engine一致；
- current Trend exact boundary 94/95清楚；
- P3 trust/exclude真正落到 runner，唔只係 UI badge；
- duplicate unknown唔會變假 0；
- acknowledgement綁 exact identity；
- precheck／submit／job-start同一 core；
- standard run同 engineering validation run分開；
- assumptions每個 cell完整資金同 immutable snapshot；
- observer唔改 final artifacts；
- cancel只影響 queued，race可線性化；
- 五狀態守恆；
- migration、IB、P5/P6同 frontend全部分批；
- 六份設計稿等 Owner written-spec review後先同步。
