# Futures Research Platform

個人用期貨算法交易研究平台：用 IB 市場數據、NautilusTrader 回測及自家模擬盤，完成以下循環：

```text
Terminal 傾策略 → strategy.v1 → 數據 → 回測 → 結果與 Owner 拍板
→ 模擬盤 → 偏離對照 → Terminal 改良 → 新策略版本
```

平台唔會提交真錢訂單，亦唔會直接呼叫 AI API；App 同 Terminal Agent 之間靠有 schema 嘅檔案交接。

## 新 Agent 由邊度開始

1. 先讀 [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)。
2. 前端只以 [`docs/ui/designs/`](docs/ui/designs/) 六份定稿及每份最後嘅「實作約束」表為準。
3. 前端 coverage／驗收逐行對 [`docs/03-frontend-component-coverage-matrix.md`](docs/03-frontend-component-coverage-matrix.md)；矩陣由六份稿倒推，唔可以反過來改設計。
4. 後端缺口睇 [`docs/08-ui-backend-mapping.md`](docs/08-ui-backend-mapping.md)。
5. 跨頁閉環同 artifact 接口睇 [`docs/09-journey-closed-loop-audit.md`](docs/09-journey-closed-loop-audit.md)。
6. 退役handover、work orders、channels同舊P6稿已從現行工作樹刪除；新Agent
   唔需要自行分辨歷史版本。事故審計由Agent C按需要從Git history精確取回。

## 目前狀態（2026-07-26）

- 六頁 MVP 設計已定稿：總覽、策略工作台、數據、回測、結果、模擬盤。
- 頁面及流程設計已形成完整閉環，唔需要新增獨立設定頁。
- 工程實作仍未完整閉環：結果匯出、晉升決定、模擬盤及偏離回流等仍待完成。
- MVP 明確唔做 A3 盤前計劃、市場狀態輸入或獨立設定頁。

詳細而且會持續更新嘅狀態，以 [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) 為準。

## Local development

```powershell
python -m uv sync --all-groups
python -m uv run pytest
python -m uv run ruff check .
python -m uv run uvicorn futures_research.api.main:app --reload
```

The FastAPI skeleton exposes `GET /health`. The React/Vite application is intentionally deferred
to the dedicated UI work order; `apps/web/` reserves its workspace.
