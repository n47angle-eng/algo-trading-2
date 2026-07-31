# `result.v1` Enriched Metrics Shared Validator Correction — Design

日期：2026-07-28

狀態：**Owner 已於 2026-07-28 明確確認本書面規格；實作只按 `[X-065]` 窄工作令**

性質：既有 `result.v1` producer／檔案合約與不可逆出口之間的窄兼容修正

## 1. 背景與已證事實

真 Activation Gate 已建立一個 standard run：

```text
nq-20260728-standard-365adf
```

該 run 的 `result.v1`：

- 是 standard worker 正常產生的既有不可變 artifact；
- 先有 `RunMetrics` compact 8 欄，再由既有 scorecard enrichment 補成完整
  enriched 26 欄；
- 符合 `docs/05-file-contract-schemas-draft.md` 的 rich metrics／scorecard
  設計；
- 主檔與三個 sidecar 已由 C 獨立核對 identity、refs、bytes 與 SHA；
- 但 `GET /api/v1/runs/{run_id}/export` 回 503。

根因不是 artifact 壞，也不是 migration／run／sidecar 壞。根因是
`src/futures_research/api/result_main_validation.py` 目前只接受 compact
`RunMetrics` 的 exact 8 keys，拒絕 producer 正常加入的 18 個 enriched keys。

同一 shared validator 同時守住：

```text
GET  /api/v1/runs/{run_id}/export
POST /api/v1/runs/{run_id}/promotion-decisions
```

所以修正必須保留兩個出口共用同一 boundary；不可只為 export 加例外。

## 2. 裁決

採用 **closed-set union**：

```text
metrics keys == compact 8
OR
metrics keys == enriched 26
```

除此以外全部拒絕。特別包括：

- compact 8 加任意第 9 個未知 key；
- enriched 26 加任意第 27 個未知 key；
- 只加入部分 enrichment；
- 完整 keys 但任何 nested shape、型別、有限數值或一致性不合格。

Validator 只讀 persisted bytes，驗證後原樣交給既有出口。它不刪欄、不補欄、
不排序、不轉型、不重算、不改寫 artifact。

## 3. 兩個唯一合法 top-level metrics shapes

### 3.1 Compact 8

沿用 `RunMetrics` 的 exact shape 與 strict validation：

```text
trade_count
gross_pnl
net_pnl
net_r
win_rate
profit_factor
expectancy_r
max_drawdown_pnl
```

Compact 形狀用於既有歷史／未 enrichment 的合法 artifact，行為不得退步。

### 3.2 Enriched 26

Enriched 形狀必須是 compact 8 加以下 exact 18 keys：

```text
max_drawdown_r
payoff_ratio
max_losing_streak
dd_duration_trades
calmar_r
param_count
trades_per_param
rule_count
skew
kurtosis
tail_ratio
var95_r
cvar95_r
psr
sharpe_per_trade
profit_concentration
cost_scenarios
period_cuts
```

不得接受其他 top-level metrics key。Validator 應先從 enriched document 抽出
compact 8，重用現有 strict `RunMetrics` validation；`ValidatedResultMain.metrics`
仍維持現有 `RunMetrics` 型別與下游行為，不改 public API。

## 4. Enriched scalar 規則

### 4.1 必須是非負 JSON integer，bool 不算 integer

```text
max_losing_streak
dd_duration_trades
```

兩者不得大於 `trade_count`。

### 4.2 必須是正 JSON integer，bool 不算 integer

```text
param_count
rule_count
```

### 4.3 必須是 finite JSON number 或 null，bool 不算 number

```text
max_drawdown_r
payoff_ratio
calmar_r
trades_per_param
skew
kurtosis
tail_ratio
var95_r
cvar95_r
psr
sharpe_per_trade
```

`NaN`、`Infinity`、`-Infinity` 一律拒絕；不得轉成 `null`、0 或字串。

`trade_count == 0` 時，`trades_per_param` 必須是 `null`；非零時必須是 finite
number。今批不新增超出既有 producer／文件合約的統計閾值判斷。

## 5. Exact nested shapes

### 5.1 `profit_concentration`

必須是 object，且 exact keys 只有：

```text
top5_removed_net_r
top10_removed_net_r
```

兩值各自必須是 finite JSON number 或 `null`；bool 不算 number。

### 5.2 `cost_scenarios`

必須是 object，且 exact scenario keys 只有：

```text
x1
x1.5
x2
```

每個 scenario 必須是 object，且 exact keys 只有：

```text
multiplier
net_pnl
net_r
expectancy_r
trade_count
```

規則：

- `multiplier` 分別 exact 等於 1、1.5、2；
- `net_pnl`、`net_r` 是 finite JSON number；
- `expectancy_r` 在 top-level `trade_count == 0` 時必須是 `null`，否則必須
  是 finite JSON number；
- `trade_count` 是非負 JSON integer、bool 不算 integer，並 exact 等於
  top-level `trade_count`；
- `x1.net_pnl`、`x1.net_r`、`x1.expectancy_r` 必須與相應 top-level compact
  metrics 值一致。

### 5.3 `period_cuts`

必須是 object，且 exact keys 只有：

```text
by_year
by_month
```

兩者都是 object。動態 bucket key：

- `by_year`：exact `YYYY`，並是 `0001..9999` 的有效年份；
- `by_month`：exact `YYYY-MM`，月份只可 `01..12`。

每個 bucket 必須是 object，且 exact keys 只有：

```text
trade_count
net_r
net_pnl
expectancy_r
```

規則：

- `trade_count` 是正 JSON integer、bool 不算 integer；
- `net_r`、`net_pnl` 是 finite JSON number；
- `expectancy_r` 是 finite JSON number；
- `by_year` 所有 bucket 的 `trade_count` 總和 exact 等於 top-level
  `trade_count`；
- `by_month` 同樣 exact 等於 top-level `trade_count`；
- `trade_count == 0` 時，`by_year` 與 `by_month` 必須同時是空 object。

今批不以重新計算 trades sidecar 的方式驗證 period PnL／R；不可逆出口只讀既有
四份 artifact，亦不得為通過 gate 重算 scorecard。

## 6. 兩出口、錯誤與零寫入語義

所有 metrics 驗證仍在現有 shared `validate_result_main()` boundary 內完成。

任何 invalid compact／enriched main：

- export 回既有 503 JSON error，body 不以 ZIP `PK` 開始；
- 不建立 partial ZIP；
- PromotionDecision 回既有 503；
- 新 decision DB path 不得因 invalid request 而建立；
- 已存在 decision store row count／bytes 不變；
- result main、三個 sidecar、run DB、strategy files 全部 byte 不變。

任何 valid compact 8 或 valid enriched 26：

- 兩出口都通過同一 validator；
- export 的四個 ZIP member 是既有 source bytes 的 exact copy；
- PromotionDecision 的 identity 與 scorecard snapshot 行為不變；
- export 本身不得建立 decision，decision 亦不得改寫 export artifact。

## 7. 明確拒絕的替代方案

### A. 接受任意 superset

拒絕。這會重新打開 `forged_metric`／未知欄位通過不可逆出口的漏洞。

### B. 匯出時只保留 compact 8

拒絕。這是 normalization／資料遺失，亦令 ZIP 不再是 persisted immutable
artifact 的 exact copy。

### C. 改 producer 不再 enrichment

拒絕。Enriched metrics 與 11-row scorecard 是既有合法產品輸出及文件合約；
壓回 compact 形狀會倒退。

### D. 改寫真 artifact 為 compact 8

拒絕。Activation Gate 的真 run 已是不可變歷史事實；不可為遷就 reader 改寫。

### E. Export-only bypass

拒絕。Export 與 PromotionDecision 必須共用同一 exact main gate，否則兩個
不可逆出口對「同一份結果是否可信」會有兩套真相。

## 8. 實作範圍

書面規格獲 Owner 過目後，正式工作令最多授權：

```text
src/futures_research/api/result_main_validation.py
tests/test_result_export.py
tests/test_promotion_decisions.py
```

如 executor 證明需要一個窄 shared test helper，必須先在 QUESTION 交代；不得
自行擴大 production scope。

禁止修改／stage：

```text
src/futures_research/backtest/scorecard.py
src/futures_research/api/batch_queue.py
src/futures_research/api/results_catalog.py
apps/web/
docs/ui/designs/
config/
data/
任何 run／result／strategy／decision artifact 或 DB
```

亦禁止：

- 改 endpoint、request／response shape 或 status semantics；
- 開 IB Gateway；
- 跑新 backtest／另建 smoke run；
- 做 PromotionDecision 產品動作；
- 開 P6、paper trader、洞察刪除或盤前計劃範圍。

## 9. Tests 與 mutation

至少要有以下兩出口 regression：

1. valid compact 8：export／decision 照舊成功；
2. valid enriched 26：用 producer-realistic zero-trade 及 nonzero fixture，
   export／decision 成功；
3. compact 加一個未知 key：兩出口 503、零寫入；
4. enriched 加一個未知 key：兩出口 503、零寫入；
5. 只得部分 enrichment：兩出口 503、零寫入；
6. enriched scalar wrong type／bool／non-finite：兩出口 503、零寫入；
7. 每個 nested object 各測 missing key、extra key、wrong type；
8. scenario multiplier／trade_count／x1 baseline mismatch：兩出口 503、零寫入；
9. period key 格式、bucket shape、count sum mismatch：兩出口 503、零寫入；
10. 現有 strict main matrix、scorecard matrix、identity／strict JSON tests全部
    保留，不可放鬆或刪除。

至少逐組實跑以下 mutation，指定新測試必須 RED，還原後再綠：

1. 把 union 退回 compact-only；
2. 把 exact top-level gate 放寬成 superset；
3. 允許 partial enrichment；
4. 拿走 nested exact-shape／finite-number gate；
5. 拿走 scenario／period cross-field count gate。

最終驗證：

```text
pytest -q tests/test_result_export.py tests/test_promotion_decisions.py
pytest -q
ruff check src tests
mypy src
```

## 10. Activation Gate 完成方式

Code correction 經 C 獨立 REVIEW 通過後，X 才可在既有 Activation Gate 上做
最後的 read-only export 重試：

```text
GET /api/v1/runs/nq-20260728-standard-365adf/export
```

不得重跑 batch、不得重寫 artifact。驗收要證明：

- 回應是 200 `application/zip`；
- ZIP exact 四個 members；
- 每個 member bytes／SHA exact 等於現有四份 source；
- main 原 15 rows、audit 原 1 row、兩個 legacy strategy、四個 immutable
  triggers、pre-smoke protected baseline 全部不變；
- 真 smoke run／batch facts 仍與原報告 exact 一致；
- 除 Owner 原先批准的四類 activation 寫入外，零額外持久寫入。四類即：
  pre-migration backup、main DB derived-index migration、一個新 smoke strategy、
  一個 smoke batch／standard run 及其既有流程產生的 artifacts；
- listeners 仍是 0，IB Gateway 沒有開，PromotionDecision／P6 沒有動作。

X 完成後才可交新的 Activation Gate REPORT；C 會重新獨立核對事前／事後
facts、15+1 逐行 SHA、ZIP source exactness、smoke truth 與寫入邊界。

## 11. 交付節點

1. 本規格先交 Owner 過目；
2. Owner 明確確認書面規格後，C 才另發窄 correction work order；
3. X code commit → REPORT → 停低；
4. C 獨立 REVIEW 必須附 correction／next work order 或 explicit hold；
5. Code REVIEW PASS 後，X 才可做 §10 的 read-only export 重試與最終
   Activation Gate REPORT；
6. C 再做最終 Activation Gate REVIEW。

在第 2 步之前，X 維持 `[X-064]` explicit hold。
