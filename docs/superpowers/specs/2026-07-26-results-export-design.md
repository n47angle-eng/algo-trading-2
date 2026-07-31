# 結果頁 `result.v1` 匯出設計

日期：2026-07-26  
狀態：Owner 已批准方案 A  
權威畫面：`docs/ui/designs/p5-results.html` 約束 #16

## 目的

補回「結果 → Terminal 討論改良 → 新策略版本 → 再回測」嘅迭代載體。結果匯出唔係拍板動作，亦唔會重新計算回測。

## 位置與互動

- 只出現喺單一回測詳情頁。
- 放喺四格圖、逐筆解釋、0 成交分析、漏斗及記分卡之後，三個決定掣之前。
- 匯出同「用得，去模擬盤／打回，返 Terminal 改／放棄呢個方向」完全獨立。
- 主掣文案：`匯出結果俾 Terminal`。
- 撳後開 Dialog，唔用 toast。Dialog 有 `下載結果包`、`複製 Terminal 開場白`、`關閉`。

## 輸出

每次只匯出當前一個 run：

```text
result-<run_id>.zip
├─ result.json
├─ trades/<run_id>.json
├─ equity/<run_id>.json
└─ events/<run_id>.json
```

- `result.json` 符合 `result.v1`。
- `result.json` 內所有 sidecar reference 都要喺 zip 內可解，唔准用本機絕對路徑。
- 0 成交仍然要產生完整合法包；`events` 要包含被截原因及最接近成交三次。
- 只打包既有不可變 run artifact；唔准為匯出重新跑、重算或改寫結果。

## Terminal 交接

複製出嚟嘅開場白要：

1. 帶準確結果包檔名。
2. 叫 Agent 先讀 `result.json`，需要追查時先讀 sidecar。
3. 要 Agent 同 Owner 討論策略理解或參數差異。
4. 如要修改，輸出一份由原策略版本衍生嘅新 `strategy.v1`；唔准覆蓋舊版本。
5. 新策略返策略工作台匯入、驗證、Owner 確認，再重新回測。

## 錯誤與邊界

- 未真正產生可下載 zip 前，唔准顯示成功。
- 失敗時 Dialog 保持打開，顯示完整且可複製嘅錯誤。
- 匯出不自動建立晉升／打回／放棄決定。
- 作任何決定亦不自動匯出。
- 批次匯出不屬本設計範圍。

## 閉環

```text
結果詳情
→ 匯出 result.v1 結果包
→ Terminal Agent 與 Owner 討論
→ 新 strategy.v1（保留 lineage）
→ 策略工作台匯入、驗證、確認
→ 數據檢查（有問題先去數據頁處理）
→ 重新回測
→ 返回結果頁
```
