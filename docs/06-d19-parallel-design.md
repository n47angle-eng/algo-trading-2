# D19 多策略並行 — 詳細設計

日期：2026-07-24 | 作者：Agent C | 狀態：v1（覆 Owner 詳細化要求）
配套：基線 D19、journey 修訂（本文件 §4）

> **⚠️ 2026-07-26 修訂註**
>
> **三層共用架構（§1）同多實例模型仍然有效。** 兩處要對齊新設計：
>
> 1. **模擬盤 UI 由「部署列表」改成「交易員分頁」**——總覽分頁 ＋ 每個交易員自己一個分頁。UI 一律叫「**交易員**」唔叫「部署」；掣名要平實。2026-07-31 runtime修訂以`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`為準；舊P6 HTML已退役。
> 2. 🔴 **部署嗰刻除咗鎖策略版本，仲要鎖一個 baseline `run_id`**——「同回測比」係模擬盤存在嘅唯一理由，冇 baseline 就計唔出。**呢點本文原本冇講**，見 `docs/09-journey-closed-loop-audit.md` B5。
>
> 現行 `docs/03` 係由六份定稿倒推嘅前端 coverage／驗收工具；本文涉及嘅 P4／P6 畫面要用佢逐行驗，但設計仍以 p4／p6 最後約束表為準。後端供應同跨頁閉環分別睇 `docs/08`／`docs/09`。舊版矩陣已刪除。

## 1. 回測批量：三層共用架構

```
BatchCoordinator
 ├─ 第一層（每合約×範圍一次）：canonical 1m read + 原生日線 read
 ├─ 第二層（每合約×範圍×session 一次）：MTF 合成 + 編譯指標預計算 + regime 校準
 │    ← 最貴嘅層；snapshot immutable → 多策略共讀零風險
 └─ 第三層（每 run 一次）：策略 FSM replay + §14 成交模擬 + 持久化 + result.v1
```

- 提交單位：`BatchManifest`（新 artifact **A4b**）：`batch_id`、`run_ids[]`、策略版本集、合約集、共用範圍/資金/成本設定、created。每個 run 照舊有自己嘅不可變 `RunManifest`（加 optional `batch_id` 欄）——單 run 嘅可重現性同溯源完全唔變。
- 執行模式：P1＝單進程順序（共用第一二層已係主要慳位）；P2＝按合約分組多進程並行。
- 失敗隔離：一個 run 死唔拖累其他；batch 摘要記低邊個成邊個敗。

## 2. 模擬盤多實例：市場數據總線

```
IBKR Gateway read-only market data（每合約＋input timeframe一條upstream，與trader數無關）
   ↓
MarketDataBus（每合約＋input timeframe＋data mode一個topic；重連喺呢層做一次）
   ↓ 廣播
SharedLiveAggregator（typed TimeframeSpec；MVP enable 1m／30m，其他留post-MVP）
   ↓ 廣播 closed snapshots
DeploymentRuntime × N（每個：策略 FSM＋虛擬帳戶＋斷路器＋狀態持久化＋偏離記錄，
                        鑰匙＝deployment_id；D17 四件套逐部署獨立）
```

- 盲區／8R／8連敗逐trader計，`tripped`互不影響；Telegram已移除，安全控制唔依賴外部通知。
- 所有timeframe係typed config及identity；1m／30m只係MVP capability，唔係永久hard-code。
- **2026-07-26 修訂**：MVP 唔產生、讀取或共享盤前計劃文件；同合約交易員只共用市場數據聚合，策略各自按鎖定條件決定有冇訊號。
- 界線（D19 原文）：N 個平行獨立帳戶，唔係一個帳戶 N 策略。

## 3. UI 流程（逐頁）

### P4 回測設定
1. **策略**：多選 chips＋「＋加策略」掣 → 彈出選擇器（只列「已確認」版本，顯示 id/名/sketch 溯源）；
2. **合約**：NQ/YM/GC 多選 chips；
3. **組合預覽**：「將產生 N×M 個 run（估時 X）」；
4. 開始 → **batch 進度列表**：每 run 一行（策略×合約｜進度條｜狀態）；P1 總覽卡同步顯示。

### P5 結果（兩層結構——唔係 N 個分頁面）
1. **入口＝batch 對比總表**：每 run 一行（策略×合約｜淨利R｜勝率｜PF｜MaxDD｜記分卡狀態 chips）；
2. **點行 → 單 run 詳情**：現有全套（指標卡、權益曲線、記分卡、多 TF 四圖＋逐圖文字、判斷鏈、逐筆交易）；
3. **疊加模式**：剔 2–4 run → 權益曲線同圖疊加（線色由 token категorical 序列分配，須過 CVD 驗證）＋核心指標並排欄；
4. 單獨（非 batch）run 照舊直入詳情。

### P6 模擬盤
1. **入口＝交易員分頁**：總覽分頁＋每個交易員一個分頁；唔准做成部署列表撳入詳情；
2. **新增交易員**：只列已晉升策略版本；揀合約；Owner 親手揀 baseline run；每個交易員建立獨立帳戶，初始資金跟 baseline；
3. **單交易員分頁**：四 TF 圖＋逐圖 live 文字、判斷鏈、持倉、偏離記錄、安全網；
4. **總覽分頁**：卡格顯示每個交易員嘅狀態、持倉、今日R、累計R及偏離警告；
5. P1 總覽頁模擬盤卡升級：「3 個行緊 · 合計 +1.2R · 0 halted」；
6. **MVP 冇 A3／市場狀態輸入**；策略冇訊號就保持等候。

## 4. Journey 修訂

- **S4**：輸入升級為「策略集 × 合約集」→ 產出 A4b BatchManifest ＋ N×M 個 A4 RunManifest；黑名單 gate 逐 run 照舊。
- **S6**：新增「對比總表」步——先橫向比較，後深挖單 run；匯出 result.json 逐 run 照舊。
- **S7**：晉升決定**逐策略**做（一個 batch 可以晉升 0..N 個）。
- **S8**：可同時存在多個 A8 PaperDeployment；S9 覆盤可跨部署比較（同市同時＝最乾淨 A/B）。

## 5. Artifact／schema 增量

| 項 | 內容 |
|---|---|
| A4b BatchManifest | `batch.v1`：batch_id、run_ids、共用設定快照 |
| RunManifest | 加 optional `batch_id` |
| result.v1 | 不變（對比由多份 result 檔案組成，唔起新格式） |

## 6. 實作注意（俾執行者）

1. 第二層共用嘅前提係「策略集使用相同指標配置」——P1 成立（全部 18/50/90+ATR14）；將來策略文件指標唔同時，coordinator 按「指標配置指紋」分組共用，唔同組各自預計算；
2. 疊加權益圖嘅線色：token categorical 序列（新增 `--chart-series-1..4`），實作前跑 CVD 驗證器；
3. MarketDataBus 係模擬盤 WO 嘅地基件——同 D17 狀態持久化一齊做設計前提，唔係事後加。
