# ADR-0002：隔離 Daytrade Bounded Context（唔混 P6 模擬盤）

- 狀態：Accepted
- 日期：2026-07-31
- 專案：Futures Research（`futures_research`）

## 背景

P6 模擬盤（`/api/v1/paper/*`、`data/paper/runtime.sqlite3`）係**策略晉升後**、可跨日、以 strategy.v1 FSM 為中心嘅 paper runtime。

日內 Daytrade 係另一套 lifecycle：完成 bar → 同一條 `run_session` 全日重播 → 只 append 新事件；RTH session 內 force_flat；正式回測預設 `1s_worst`。

若共用 P6 ledger／trades 表，語意、排名、覆盤同上線 gate 會混。

## 決定

1. **獨立 BC**：`src/futures_research/daytrade/`、`/api/v1/daytrade/*`、`/daytrade` UI、`data/daytrade/*`。
2. **禁止**日內 rows 寫入 `data/paper/**` 或 P6 trades 表。
3. **Live authority 永遠 Python**；加速器 `writes_authority=false`，永不寫 live ledger。
4. **Live 預設尺**：`1m_close`（`daytrade-live-1m-close-v1`）。
5. **正式回測／評核預設尺**：`1s_worst`（`daytrade-backtest-1s-worst-v1`）。
6. 缺 1s 資料時回測 **fail-closed**，禁止 silently 降級 1m 仲標 `1s_worst`。
7. 市場範圍：期貨 RTH 日內（NQ／YM／GC）；session 鐘用合約 `America/Chicago` RTH。
8. 可共用：IB 行情、contract registry、commission／tick 成本、前端 design token。

## 不變量

1. Session 以交易日 + RTH 為界；`force_flat` 後只可 reduce-only／已平。
2. 未能平倉 → `OVERNIGHT_BREACH`，停新倉，記 incident。
3. Live replay 只能 append 與已持久事件完全一致嘅 deterministic prefix；divergence fail-closed。
4. UI 禁止另計 P&L；數字同源 ledger events。
5. 未完成 bar 不可驅動成交。
6. `runner_enabled` 預設 0（fail-closed）；Owner 先開。

## 產品範圍（Owner 2026-07-31 更新）

- **可新增多個模擬交易員**；每位有獨立 ledger session／個人頁。
- **唔做 team 合併 P&L／排行榜**；入口係交易員名單 → 點入個人檔案。
- API：`GET/POST /traders`、`GET /traders/{id}`、`/live` `/positions` `/scorecard` `/activity`。
- UI：`/daytrade` 名單＋新增；`/daytrade/traders/:id` 個人檔案／即市／倉位／成績表／動態。

## 後果

- P6 路徑預設零行為變化。
- 多一份 storage／runner 係刻意隔離成本。
- 每位交易員獨立數字；無「隊伍總權益」。
