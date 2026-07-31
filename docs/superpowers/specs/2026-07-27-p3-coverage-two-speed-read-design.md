# P3 Coverage Two-Speed Read Design

日期：2026-07-27

狀態：**Owner 2026-07-27 已完成 written-spec review並正式批准；現為 X 實作／C 驗收權威**

範圍：`GET /api/v1/data/coverage` 嘅 P2 catalog read path、P3 full coverage
read path，以及兩者嘅前後端 seam。**不改六份權威設計稿、不改 P3 facts
語義、不開 P4／P6／IB／migration。**

## 1. 問題與實證

X P3-A `9acfa04`／`[X-039]` 已正確供應：

- exchange trading-date 完整／問題日；
- Owner trust／exclude overlay；
- 連續完整最長一段；
- minute candidate range 內嘅原生日線覆蓋；
- nested `known | unavailable` fail-closed 狀態。

C 獨立重跑 focused 54、full 545、ruff、mypy、session mapping probes，以上
correctness 全部通過。但同一 endpoint 同時係 P2 instrument catalog source，
而 full projection 每次同步全掃 current canonical data：

| Probe | 實測 |
|---|---:|
| X 真 API full scan | 54.728 秒 |
| C 真 API full scan | 103.935 秒 |
| C NQ minute read（156,912 bars） | 15.208 秒 |
| C NQ trading-day compute | 15.831 秒 |
| P3-A 前既有 legacy timestamp scan（三合約） | 7.005 秒 |

P2 `SketchTab`、`QuantifyTab`、`InsightTab` 目前各自讀同一 coverage endpoint；
分頁 mount 會重新請求。P3 full scan 因此會令純 identity catalog 等候約
一至兩分鐘，甚至在切頁時重複掃描。

呢個唔係「P3 頁慢少少」：佢令 P3 新 projection 反過來拖垮 P2 已存在嘅
instrument 閉環，違反 P3-A 工作令已拍板原則：

> 新資料壞咗或計算成本高，唔應拖垮 P2 contract identity catalog。

## 2. 已選方案

採用 **同一路由、兩個明確 view**：

```text
GET /api/v1/data/coverage?view=catalog
  → P2 快速 configured-contract identity
  → 不計 P3 trading/native projection

GET /api/v1/data/coverage
GET /api/v1/data/coverage?view=full
  → P3 完整 exact coverage facts
  → 兩者 response 必須相同
```

### 2.1 點解選呢個方案

- P2 唔再為完全唔消費嘅 P3 facts 付出全掃成本；
- P3 仍保留同一 canonical endpoint，唔新增第二個 top-level resource；
- 無 query 嘅既有 P3／legacy consumer 行為不變；
- view 係 caller 明確選擇，唔用 hidden heuristics 猜 caller；
- full path 仍要優化，唔會用「P2 已快」掩飾 P3 本身一至兩分鐘 latency。

### 2.2 已否決方案

1. **只優化 full path、P2 繼續讀 full**：資料量再增長時仍然耦合；P2
   identity availability 會受 P3 projection 影響。
2. **只加 process cache**：第一次 request 仍然慢；若未證明所有 minute、
   native-daily partition 同 blacklist exact invalidation，會回陳舊 facts。
3. **另開 `/instruments` endpoint**：功能可行，但增加一個重複 catalog
   resource；MVP 用同一路由明確 view 已足夠。

## 3. Exact API Contract

### 3.1 Query

`view` 只接受：

- `catalog`
- `full`

省略 `view` exact 等於 `view=full`。其他值由 FastAPI validation 回 422；
唔靜靜 fallback。

### 3.2 `view=catalog`

Top-level 仍係：

```json
{
  "schema": "data_coverage.v1",
  "count": 3,
  "contracts": []
}
```

每個 contract row exact 保留 P3-A 前已存在嘅欄位：

```text
symbol
contract_id
display_name
asset_class
currency
sessions_available
partition_count
bar_count
first_timestamp
last_timestamp
roll_blackout_dates
owner_excluded_dates
quality
```

規則：

1. `trading_day_coverage` 同 `native_daily_coverage` **省略**；caller 已明確要求
   catalog view，唔准用 `unavailable` 或假 `0` 扮做計過。
2. configured contract 就算暫時零 market partitions 仍要出現。
3. identity/class/currency/session 繼續只來自 `config/contracts.yaml`。
4. legacy field 名、primitive type、排序同語義不變。
5. corrupt contract config 沿用 whole-endpoint 503；唔回 partial catalog。
6. market partition metadata 壞時沿用既有 legacy field 行為，但唔可以因此
   呼叫 full coverage helper、native daily store 或 quality recomputation。

### 3.3 Default／`view=full`

Response exact 等於 P3-A `9acfa04` 已交 contract：

- 所有 catalog fields；
- `trading_day_coverage.v1`；
- `native_daily_coverage.v1`。

以下全部凍結：

- trading-date 係 exchange-local session-end label；
- candidate range、complete/problem、warning/error/info 語義；
- Owner overlay intersection；
- longest segment；
- native session-start reverse mapping；
- known empty vs unavailable；
- stable error codes；
- arrays/count reconciliation；
- 一個 nested projection 壞唔拖低舊 identity。

效能修正唔准改任何一項 truth 或用估算近似。

## 4. Backend Architecture

### 4.1 Catalog path

Catalog path 只做：

1. 讀 configured contract registry；
2. 讀 owner／latest quality 等 legacy catalog metadata；
3. 由 Parquet footer／row-group statistics 供應 legacy partition count、bar count、
   first／last timestamp；如果個別舊檔冇可信 statistics，先局部讀該檔
   `ts_event` column；
4. serialize legacy catalog row。

Catalog path 明確禁止：

- `CanonicalStore.read(contract_id)` 全 materialization；
- `build_contract_coverage_facts()`；
- `compute_trading_day_coverage()`；
- `DataQualityChecker.check()`；
- 讀 `data/market-daily/`；
- cache、snapshot、report、DB 或任何 artifact write。

### 4.2 Full path

Full path 保留 exact P3 facts，但每個 contract 每個 physical partition
唔准重複做「legacy timestamp scan」再做「full CanonicalStore scan」。

實作可以用 PyArrow columnar scan、分區 scanner 或其他等價方式，但必須：

1. 同一讀取結果同時供應 legacy fields 及 P3 projection；
2. exact 驗證 timestamp、OHLC、volume、contract identity、source 同現行
   checker 所需資料；
3. coverage classification 與 `9acfa04` reference implementation
   **整份 document 相同**；
4. corrupt／unmapped input 保持 nested unavailable，唔 silently drop；
5. 唔用 row count、min/max 或 bar range 估 complete day；
6. 唔加未證明 invalidation 嘅 cache。

可以保留 slow reference implementation 只供 differential tests；production
path 唔應把 658k bars 全部轉成 Pydantic objects兩次。

### 4.3 Frontend seam（後續 Y 工作令）

P2 normal mode：

- 全部 instrument catalog consumer 明確請求 `view=catalog`；
- 同一 `StrategiesPage` session 由共用 provider／request cache 供應一次 catalog，
  分頁切換唔重新打同一 request；
- loading／error／invalid 仍 fail-closed；
- owner-review fixture 唔 call backend。

P3 normal mode：

- 明確請求 `view=full`；
- nested status 仍逐個讀，唔 fallback 去 catalog bar count；
- P3 frontend 另按權威稿 #1–#14 出批次，本文唔授權 Y 提前開工。

## 5. Performance Acceptance

用 Owner 現有真資料 snapshot：

```text
GC minute 364,601
NQ minute 156,912
YM minute 136,222
native daily 458 + 336 + 211
```

驗收上限：

| Read path | Fresh Python/server process 首次 request |
|---|---:|
| `view=catalog` | **≤ 2.0 秒** |
| default／`view=full` | **≤ 15.0 秒** |

規則：

1. wall-clock 唔放入容易抖動嘅 unit test；REPORT 用同一部 Owner 機器真 API
   smoke 記錄 command、資料量、每次時間。
2. 至少跑三次 fresh-process smoke，逐次列數；三次都要過上限。
3. 如果 exact semantics 下做唔到上限，X 停低出 `QUESTION`，唔自行放寬、
   唔偷偷加 stale cache、唔改門檻。
4. C REVIEW 會獨立再量一次；X report 數字唔等於收貨。

## 6. Error Handling／Read-only

- `catalog` view 唔會因 native daily 壞檔而失效，因為根本唔讀 native daily。
- `full` view 保留 P3-A nested fail-closed：
  minute unavailable 令 native dependency unavailable；native 壞只降 native。
- raw exception、本機 path、Parquet內部訊息唔出 API。
- GET 前後 market／daily／quality／blacklist／strategy／run／result tree
  bytes＋SHA-256 inventory exact 相同。
- writer、ingestion、download、migration、IB adapter call count 全部 0。

## 7. Verification

### 7.1 Contract tests

1. 省略 view exact 等於 `view=full`；
2. `view=catalog` exact top-level／row keys；
3. unknown view 422；
4. catalog configured-empty contracts仍出現；
5. catalog spy 證明 full helper、minute full read、daily read、quality recompute
   全部零 call；
6. corrupt native daily 不影響 catalog；
7. full known／unavailable fixtures維持 P3-A exact response；
8. full path同 slow reference喺 missing minute、zero-bar day、duplicate、
   invalid price、OHLC envelope、negative volume、tick misalignment、ATR spike、
   warning、info-only、Owner trust/exclude、DST、unmapped minute/native上
   document-by-document 相同；
9. legacy consumer忽略 additive full fields仍正常；
10. catalog／full GET byte-read-only。

### 7.2 Mutation

至少實跑：

1. catalog path誤 call full helper；
2. default view改成 catalog；
3. catalog response混入兩個 full nested keys；
4. full path略過 warning或把 info當 blocking；
5. full path用 observed dates，漏中間 zero-bar weekday；
6. full path重新加入第二次 physical partition scan；
7. frontend P2拎走 `view=catalog`；
8. frontend分頁各自重新 fetch。

每項要指定測試 RED、還原後綠。

### 7.3 Regression

- backend focused＋full `pytest`；
- `ruff check src tests`；
- `mypy src`；
- frontend seam到時另跑 focused＋full web、typecheck、lint、build；
- protected-scope diff、channel append hygiene；
- legacy strategies hash、15+1 runs、16 result mains不變。

## 8. Scope／Sequencing

第一批只由 X 做 backend：

- route query validation；
- catalog fast path；
- full exact single-scan／columnar optimization；
- backend tests、mutations、真 read-only performance smoke。

X 唔准改：

- `apps/web/`；
- `docs/ui/designs/`；
- `config/contracts.yaml`；
- `data/` 真 artifacts；
- P4／P5／P6；
- IB、download、migration。

X correction 經 C 收貨後，先另出 Y 工作令接 `view=catalog`＋shared request。
兩個執行者唔同一批跨 scope 改對方檔案。

## 9. 自我審查

- **冇雙重真相**：full facts仍係同一 endpoint同一語義；catalog view只係 caller
  明確要求嘅 identity subset。
- **冇以效能換 correctness**：full response要同 slow reference逐 document相同。
- **冇 stale cache**：本批明確禁止未證明 invalidation嘅 cache。
- **P2／P3責任分開**：P2唔再支付P3計算；P3唔 fallback去P2 metadata。
- **Backward compatibility清楚**：省略view仍係full；只有明確catalog view省略nested facts。
- **錯誤狀態清楚**：invalid query 422；config壞503；nested input問題維持stable unavailable。
- **範圍可由一個X correction完成**：frontend seam係下一份Y工作令，唔混入X批次。
- **無 placeholder／TBD／未裁決實作語義**。
