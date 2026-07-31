# 回測結構化證據與零寫入預覽設計

日期：2026-07-26

狀態：Owner 已於 2026-07-26 確認書面規格；可進入 X Batch 3
對應後續工作：Agent X Batch 3（`docs/08` §4 第 2 組 #3–#5）

## 1. 目的

補足兩個已定稿頁面目前攞唔到嘅真資料：

1. P5 每筆成交嘅四段因果解釋；
2. P5 零成交時逐次 reject 證據；
3. P2 分頁②可以明確觸發、但完全唔寫 artifact 嘅試跑預覽。

呢批嘅核心原則係：

> 引擎已經作出判斷；今次係把判斷時見到嘅條件、實際值、門檻同先後次序
> 結構化記錄，唔係事後由 event log 猜返一段故事。

## 2. 權威依據

- `docs/ui/designs/p5-results.html`
  - #7：每筆交易回答入市、止損、離場、保守假設；
  - #9：零成交顯示最接近成交嘅三次同一句判斷；
  - #11：時刻／交易日規則；
  - #13：引擎供應結構化決策記錄；
  - #14：後端記錄逐次 reject；
  - #16：之後 `result.v1` 只打包既有不可變 artifact。
- `docs/ui/designs/p2-strategy-workbench.html`
  - #12：preview 唔自動跑、唔存 artifact、唔佔 run 編號；
  - #13：preview 要有漏斗＋系統解讀；
  - #14：日級同評估級分開比例尺；
  - #15：確認採用前零寫入。
- `docs/03-frontend-component-coverage-matrix.md`
  - `P2-Q07–P2-Q09`
  - `P5-D06–P5-D08`
  - `F-06`、`F-08`
- `docs/05-file-contract-schemas-draft.md` §2／§2.1
- `docs/08-ui-backend-mapping.md` §4 第 2 組

矩陣只係 coverage 工具；任何衝突以設計稿最後「實作約束」優先。

## 3. Owner 已拍板嘅保留策略

### 3.1 完整保存

每個新 run 必須保存全部結構化 reject evidence，唔可以：

- 只保存「最接近三次」；
- 達到某個數量後靜靜截斷；
- 只保留原因 count；
- 用 sample 取代完整逐次資料；
- 因為 validation run 或零成交而省略。

每個 completed run 嘅 evidence 係不可變 run artifact 一部分。既有 16 個歷史
run 唔重跑、唔回填、唔重算；佢哋要誠實顯示 evidence unavailable。

### 3.2 另外產生摘要

同一份完整 evidence 可以額外導出：

- 總評估數；
- 各 layer 到達數；
- 各 rejection condition count；
- 有成交／零成交；
- 最深到達 layer；
- evidence availability／completeness。

摘要係完整 evidence 嘅 deterministic projection，唔係另一份可以自行漂移嘅
真相。測試要由完整 records 重算摘要並核對完全一致。

### 3.3 今批唔冒認「最接近三次」

P5 最終固定顯示三次，但「不同單位嘅差距點樣比較」未有已批准算法。今批：

- 保存足夠欄位，令下一批可以排序；
- 可以按 timestamp／sequence 提供 deterministic records；
- 可以提供「到達最深 layer」候選集合；
- **唔准把任意三筆標成 `closest_three` 或 `near_miss_rank`**；
- 唔准把 points、percent、bar count 等異質數值直接相減後排序。

P5 live 接線前，C 會根據今批真 evidence 分布另作排名裁決。呢個延期唔影響
完整記錄、摘要或 dry-run。

### 3.4 現有規模證據

2026-07-26 實測現有 17 個 events files：

- 15,653 events；
- 8,849 `signal_rejected`；
- 合共 4.55 MB；
- 最大單 run：2,691 events／1,686 rejects／782,509 bytes。

加入 actual／required／unit 後會放大，但現時證據支持先保留完整 JSON；
唔需要為未出現嘅容量問題做有損截斷。

## 4. 資料邊界

### 4.1 Condition fact

所有 pass／fail 判斷使用同一種 typed fact。最少語義：

```text
condition_id       穩定機器識別，唔係 UI 句子
layer_id           daily／mid／entry／execution 等真實層
observed_at        有 offset 嘅 UTC instant
status             passed／failed／not_evaluated
actual             當刻真值；number／string／bool／null
operator           判斷操作，例如 gte／lte／eq／in／crosses
required           策略當次鎖定門檻或集合
unit               points／percent／ticks／bars／price／boolean／enum／none
source_sequences   對應既有 event sequence
```

規則：

- `actual` 同 `required` primitive type 要保持真實，唔全部轉 string；
- number 必須 finite；
- unknown 同 zero 分開；
- `unit` 必須明示，唔靠 condition name 猜；
- `passed`／`failed` 必須至少一個 source sequence；`not_evaluated` 可以係空；
- runtime原本短路後冇執行嘅條件要記 `not_evaluated`，唔准為填證據而
  事後重跑並扮成當刻有評估；
- Owner-facing人話由 Y 映射，X 唔把廣東話句子當唯一資料；
- condition id 可以 additive 擴充，consumer 對未知 id 要 fail-closed顯示
  raw structured fact，唔可以當 passed。

### 4.2 Per-trade decision evidence

每筆新成交以 exact `trade_id`／ordinal 綁住：

```text
entry
  signal kind
  signal timestamp／entry timestamp
  逐層 condition facts
  真 entry reference／fill price

stop
  reference type
  reference price
  offset ticks
  final stop price
  計算時使用嘅真 condition facts

exit
  actual exit reason
  trigger timestamp／price
  所有同 bar 競爭嘅 exit candidates
  邊個按保守規則先採用

conservative assumptions
  assumption code
  有冇實際套用
  影響邊個 price／reason／ordering
  source event sequences
```

四段資料必須喺引擎作決定嗰刻取得；唔准由完成後嘅 chart bars 用 heuristic
估返。

### 4.3 Per-occurrence rejection evidence

每次 signal evaluation 被拒絕至少保存：

```text
evidence_id
timestamp
ts_init
exchange trading_date
direction
evaluation sequence
reached layers（有序）
condition facts（pass 同 fail 都保留）
blocking condition ids（至少一個）
候選 signal kind／母棒等當刻上下文（如適用）
source event sequences
```

規則：

- `trading_date` 係純 `YYYY-MM-DD` exchange label，永不轉時區；
- `timestamp`／`ts_init` 係 UTC instant；
- 同一 evaluation 只產生一個 rejection evidence record；
- 同一決策點實際評估過而同時失敗嘅 blocking conditions要全部記；
- runtime因第一個blocker短路而冇評估嘅下游條件明列`not_evaluated`，唔重跑；
- evidence id 必須喺同一 immutable run 入面穩定且唯一；
- 記錄 pass facts 係必要，因為「走到第幾層」唔可以只靠 fail reason 猜。

## 5. Artifact 方案

採用現有不可變 sidecar，唔另建第二套 Owner-facing真相：

### 5.1 `trades.v1`

每個新 trade record additive 增加 structured `decision_evidence`。既有欄位、
primitive type、route shape不變。

### 5.2 `events.v1`

新 run 嘅 sidecar additive 增加：

```text
rejection_evidence[]
evidence_summary
evidence_complete: true
```

既有 `events[]` 原封保留，因為圖表／narrative／scorecard仍有 consumer。
新欄唔取代 event log。

舊 sidecar冇新欄時：

```text
evidence_complete: false（read layer導出）
rejection_evidence unavailable
```

read API唔准把舊 run 空陣列解讀成「真係零次 reject」。

### 5.3 寫入一致性

新 run 嘅 result main、trades、equity、events、SQLite immutable record同
Batch 2衍生索引必須保持現有成功邊界：

- evidence serialization失敗 → run不可半完成；
- 不可留下 main file指向缺失／partial sidecar；
- 不可留下 DB有run但evidence artifact未完成；
- retry唔可覆蓋已存在 immutable run；
- 真歷史 artifact byte-for-byte不變。

Batch 3唔執行真 main DB migration，亦唔改 Batch 2 proof contract。

## 6. Read interface

保持所有既有 route同success shape兼容，只做 additive供應。

### 6.1 Trades

既有：

```text
GET /api/v1/runs/{run_id}/trades
```

新 run每筆 trade additive帶 `decision_evidence`。舊 run明示 unavailable；
唔用 event narrative冒充。

### 6.2 Events／rejections

既有：

```text
GET /api/v1/runs/{run_id}/events
```

`events.v1` response additive帶完整 `rejection_evidence`、`evidence_summary`
同 `evidence_complete`。今批唔另加分頁端點，亦唔喺 read layer截斷：

- existing `events[]`、route、primitive types原封不動；
- records按 `evaluation_sequence`再`evidence_id`排序；
- summary count必須等於完整records；
- unknown／old evidence同known empty分開；
- response size由REPORT實測最大fixture；日後真係超出本地使用需要先另批加
  pagination，唔預先建立第二套接口。

## 7. 零寫入 dry-run

### 7.1 觸發

P2 preview係一個明確 user action，唔自動跑。後端固定使用：

```text
POST /api/v1/backtests/preview
```

同步完成並回傳一個非持久化snapshot。request接受：

```text
schema: backtest_preview_request.v1
source_text: 未持久化 strategy.v1 原文
filename: optional display label
contract_id: exact contract
range_start／range_end: 有offset、normalize後start < end
assumptions:
  initial_capital_usd
  commission_per_side
  slippage_ticks
```

`source_text` 必須經同一 canonical validator；contract要喺策略universe；
assumptions用同standard run相同domain validation，唔准preview另開寬鬆值。

唔可以要求先 import strategy，否則違反 P2 #15。

### 7.2 執行

使用同 standard run相同：

- canonical strategy parser；
- market data／session semantics；
- strategy state machine；
- conservative execution；
- evidence builders；
- funnel semantics。

dry-run 唔可以有第二套較寬鬆引擎。相同 input facts下，preview evidence同
standard run evidence要 deterministic一致；差別只係 persistence。

### 7.3 零寫入鐵律

一個 preview request無論成功、validation失敗、engine失敗或client中斷，都要：

- 零 strategy import／confirm；
- 零 run id消耗；
- 零 batch／job record；
- 零 SQLite run／trade／lookup／date／proof rows；
- 零 result／trades／equity／events／chart files；
- 零 PromotionDecision；
- 零改 existing artifact bytes；
- 零 local migration／bootstrap side effect。

測試必須量度 before／after DB bytes或transaction writes、artifact inventory、
ID allocator及repository calls；唔接受「UI冇掣」作證明。

### 7.4 Response

preview response固定schema：

```text
backtest_preview.v1
```

至少供應：

- `schema`；
- `run_scope: dry_run`；
- request fingerprint；
- validation結果；
- funnel（daily單位同evaluation單位分開）；
- structured decision evidence（如有成交）；
- complete rejection evidence＋summary；
- `charts`：D／1H／30m／5m四格，使用現有chart series欄位語義，
  由今次in-memory run直接生成；
- `persisted: false`；
- 完整 warning／error。

response唔可以包含一個可喺P5打開嘅假 `run_id`，亦唔入回測歷史。

## 8. Error handling

- missing／invalid evidence欄位：新 run export fail-closed；
- old run冇 evidence：read成功，但明示 unavailable；
- condition unknown：保留 raw structured fact，consumer唔猜人話；
- non-finite numeric：拒絕生成 artifact；
- event sequence reference不存在：拒絕生成 artifact；
- duplicate evidence id／trade id mismatch：拒絕；
- preview validation failure：完整四層 report，零寫入；
- preview engine failure：完整原錯／診斷，零寫入；
- evidence read完整回傳artifact records；今批冇隱藏limit、silent truncation
  或partial success。

## 9. 驗收與突變

### 9.1 正向

1. 有成交 run每筆四段 evidence齊，actual／required／unit可重建設計稿例子。
2. 同 bar stop／target競爭，保存全部 candidates同保守採用結果。
3. 零成交 run保留每次 reject，摘要由records重算一致。
4. 同一 evaluation多個fail，全數保存。
5. old 16 runs byte hash不變，read明示 unavailable。
6. preview成功有funnel／evidence，但全系統零寫入。
7. preview同等input再做standard run，evidence facts deterministic一致。

### 9.2 負向

1. evidence serialization中途失敗，零partial run／sidecar／DB。
2. 缺unit、non-finite actual、invalid trading date、dangling event sequence全部失敗。
3. known empty同unknown unavailable不可混淆。
4. preview嘗試寫任何repository／allocator／filesystem即測試紅。
5. 既有trades／events consumer仍讀得到原欄位。

### 9.3 必做 mutation

至少故意改壞並證明指定測試紅：

1. reject只留前三筆；
2. summary count唔由完整records導出；
3. unknown舊run被當成known empty；
4. stop／target同bar只記最後採用者、漏競爭candidate；
5. preview偷寫一個run id／SQLite row／artifact；
6. trading date經時區轉換；
7. actual／required全部轉string；
8. evidence write failure仍發布result main。

全部mutation還原後跑focused＋full＋ruff＋mypy。

## 10. 範圍

Batch 3包含：

- typed condition facts；
- per-trade decision evidence；
- per-occurrence rejection evidence；
- complete retention；
- deterministic summary；
- additive read供應；
- zero-write dry-run。

Batch 3不包含：

- 「最接近三次」跨單位最終排名；
- Y P5 normal mode接線；
- `result.v1` zip packaging；
- PromotionDecision；
- P3／P4新供應；
- true main migration；
- P6。

## 11. 下一個閉環

```text
X Batch 3：完整 evidence＋summary＋dry-run
→ C REVIEW真 evidence分布
→ Owner＋C拍板「最接近三次」排名
→ Y接P5 normal mode
→ X/Y完成result.v1＋PromotionDecision
→ 真P4→P5→Terminal／P6資格閉環
→ 最後先開P6
```
