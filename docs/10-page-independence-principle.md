# 頁面獨立原則（Page Independence）

日期：2026-07-31 | 狀態：Owner 已確認方向，工程落地中

## 一句話

**每一頁係獨立入口；資料真相喺 database／backend；頁面之間唔靠硬傳、唔靠硬鎖。**

## 做乜改

舊模型把研究流程寫成**頁面硬閘**：

```text
策略確認 → 回測 → 結果「用得」→ 先至可以上模擬盤
```

問題：

- 上一頁冇「傳」成功，下一頁好似壞咗
- 模擬盤被 PromotionDecision 綁死，唔能直接用 DB 入面已有策略
- 用戶感受係「流程鎖」，而唔係「呢度冇可用資料」

新模型：

```text
每個頁面 → 自己向 backend 查 available 資料
有資料 → 用
冇資料 → soft empty（講清楚缺咩、去邊度補），唔整頁失敗
```

## 硬規則

1. **DB／API 係唯一真相**  
   每頁讀寫都經 backend。URL query、localStorage、上一頁 React state 只可以做**方便 prefill**，唔可以做硬依賴。

2. **冇資料 = empty state，唔係交接失敗**  
   例如回測頁冇已確認策略 → 顯示「未有已確認策略，暫時冇得揀」＋連去策略工作台。  
   **唔會**因為「上一頁冇傳」而 500／整頁鎖死。

3. **跨頁連結係建議，唔係通行證**  
   - 數據頁 → 回測：handoff query 只係預填日期／合約  
   - 結果頁「用得」：仍可寫入 PromotionDecision 做**歷史意圖**  
   - 模擬盤：**唔需要**先有 `use` 決定；直接從已確認策略（及可用 baseline）揀

4. **真技術依賴仍然保留**  
   Soft empty 唔等於發明假資料。  
   - 回測仍要 confirmed strategy  
   - 模擬盤建立交易員仍要**真實存在**嘅 verified baseline run（來自 results DB）  
   - 缺 baseline → 顯示「呢個策略暫時冇可用對照基準」，唔係「你未過晉升閘」

## 每頁資料來源（預期）

| 頁面 | 自己讀 | 冇資料時 |
|------|--------|----------|
| 策略工作台 | strategies / sketches store | 空庫提示；可匯入 |
| 數據 | coverage / download API | 顯示未覆蓋；唔擋入頁 |
| 回測 | `GET /strategies?status=confirmed` + data coverage | 空策略 empty；可入頁 |
| 結果 | runs catalog + 可選 promotion 歷史 | 空 runs empty |
| 模擬盤 | confirmed strategies + paper contracts/baselines/traders | 空策略 → 去策略頁；空 baseline → 講明缺回測結果 |

## 明確廢除

- ~~模擬盤必須先有 PromotionDecision `use`~~  
- ~~頁面 A 必須成功 handoff 先准打開頁面 B~~  
- ~~前端用 localStorage 當跨頁真理~~

## 仍然保留

- Artifact 溯源（strategy_id、content_sha256、run_id、baseline SHA）  
- PromotionDecision 作 Owner 判斷紀錄（可選、可查、唔擋路）  
- URL prefill 作 UX 加速（可忽略、可重覆查 DB 覆寫）

## 工程對照

- Backend：`require_paper_eligibility` 唔再檢查 `use` 決定  
- Frontend 模擬盤：策略列表來源改為 **已確認策略**（`/api/v1/strategies?status=confirmed`）  
- 空狀態文案指向「去邊度產生資料」，而唔係「先完成上一頁批准」
