# P2 Primary Instrument Closed-Loop Design

日期：2026-07-27

狀態：**Owner 已批准**

範圍：P2 草圖、量化確認、市場洞察；共用合約 catalog；`sketch.v1`、`strategy.v1`、`insight.v1`；P4 選擇範圍及 run snapshot 交接。**不新增獨立頁面。**

權威畫面：`docs/ui/designs/p2-strategy-workbench.html` 實作約束 #28–#35。批准時使用嘅完整三態畫面附件：`docs/ui/design-discussions/p2-instrument-closed-loop-v2.html`／`.png`。

## 1. Owner 決定

1. MVP 每個草圖包只對應一個 `primary_instrument`。
2. Instrument 由 Owner 明確選擇；App、Terminal AI、檔名、標題或圖片內容都唔准代為推斷。
3. `asset_class`、顯示名稱、幣別同可用交易時段全部由 canonical contract catalog 帶入；Owner 唔自由輸入。
4. Terminal 可以建議同一 asset class 內嘅 strategy universe 擴展，但每個新增 instrument 必須提供非空 `expansion_rationale`。
5. P2 返程只可以接受或退回**整份** `strategy.v1`。唔設 instrument checkbox，App 唔會另存「approved instruments」，亦唔會靜靜改寫 Terminal 宣告嘅 `universe.contracts`。
6. P4 之後先由已確認 universe 同可用資料嘅交集，選擇今次回測邊啲 instrument。每個 strategy × instrument run 各自以完整 USD 100,000 起步。
7. 同 asset class 只代表可以形成一個待驗證假設，唔代表策略已證明有效；成效只由 P5 證據判斷。
8. 市場洞察支線同樣保留 instrument 同 asset class，但仍然只係筆記，唔會自動連策略或回測。

## 2. Canonical Contract Catalog

唯一 catalog source 係 `config/contracts.yaml`。每個 root/product symbol 至少提供：

```yaml
NQ:
  display_name: E-mini Nasdaq-100
  asset_class: equity_index_futures
  currency: USD
  sessions:
    - rth
    - eth
```

MVP 控制詞彙同初始 mapping：

| Symbol | `display_name` | `asset_class` |
|---|---|---|
| `NQ` | `E-mini Nasdaq-100` | `equity_index_futures` |
| `YM` | `E-mini Dow` | `equity_index_futures` |
| `GC` | `Gold` | `commodity_futures` |

規則：

- 未知 symbol、未知 asset class、catalog 自相矛盾、缺 display name／currency／sessions 全部 fail closed；
- backend model、Nautilus adapter、validation 同 API response 共用同一 catalog 值，唔准另有 hard-coded symbol → asset class mapping；
- `/api/v1/data/coverage` 每個 configured contract row additive 回傳 `display_name`、`asset_class`、`currency`、`sessions_available`。就算暫時冇下載數據，configured symbol 仍然要出現，俾 P2 選擇；
- frontend 唔准自己硬寫 NQ／YM／GC 分類或人話名。

## 3. `sketch.v1`

新草圖 metadata 必填：

```yaml
schema: sketch.v1
origin: workshop
sketch_id: sketch-20260727-01
instrument: NQ
asset_class: equity_index_futures
```

`instrument` 係 root/product symbol，唔係到期月份。`asset_class` 必須同 catalog 對應；App 匯出時寫入兩欄，importer 亦重新用 catalog 驗證。

### 編輯及不可變規則

- 未上載任何圖片：Owner 可以改 instrument；asset class 隨 catalog 更新；
- 一旦任何圖片已附加：instrument 同 asset class 鎖定，防止 NQ 圖被靜靜改標成 GC；
- 一旦匯出：整份草圖不可變；要改 instrument 或任何內容，只可複製成新 sketch id；
- browser ZIP 同 repo package 必須 byte-for-byte 使用同一份 validated metadata source；
- screenshot 本身無法證明真係顯示 NQ。MVP 嘅誠實保證係綁定 Owner 明確選擇並鎖定，唔做 OCR、唔假裝從圖片驗證。

### Legacy

- 未匯出 frontend draft 缺 instrument/class：顯示「未完成」，Owner 補揀之前匯出 disabled；唔推斷；
- 已匯出 package 缺新欄位：只讀 legacy；唔原地補欄，需複製成新 sketch id；
- 現有 strategy-0001／0002 原 bytes 不改；新 run 重新驗證時仍 invalid；歷史 run/result 不受影響。

## 4. `strategy.v1`

新策略必填：

```yaml
universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts:
    - NQ
    - YM
  expansion_rationale:
    YM: 同屬股指趨勢結構；價差以 ticks／R 正規化，仍待回測
  session: eth
```

`expansion_rationale` 永遠必填：primary-only 策略寫 `{}`。其 keys 必須**剛好等於** `contracts - {primary_instrument}`，值必須係非空人話，唔准缺項、唔准多項。

Validation 必須同時證明：

1. `primary_instrument` 存在於 `contracts`；
2. 所有 symbol 都存在於 canonical catalog；
3. 文件宣告 `asset_class` 同 primary 嘅 catalog 分類 exact match；
4. 所有 universe members 同 primary 屬同一 asset class；
5. 所有 members 都支援文件宣告嘅 `session`；
6. MVP 所有 members 都以 USD 計價；其他幣別因未有 FX conversion 而 fail closed；
7. expansion rationale keys 同內容完整；
8. 若本機有 lineage 指向嘅 sketch，strategy primary/class 必須同 sketch exact match；
9. 若 composite lineage 完整但本機冇該 sketch，references 仍可 valid；UI 要顯示「本機缺原草圖，無法左右對照」，然後以 strategy 自包含欄位＋catalog 完成其餘 validation，唔假裝搵到草圖。

失敗時：

- 顯示精確欄位、人話原因同修法；
- 確認 disabled；
- import／confirm write count 都係 0；
- App 唔准刪走非法 member、改 primary、補 rationale 或另存一份改造後 universe。

成功時 Owner 只得兩個決定：

- 「確認整份策略」→ 原文＋validation snapshot 建立不可變新版本；
- 「唔同意，返 Terminal 修改整份策略」→ 零寫入。

## 5. `insight.v1`

新 insight 必填 top-level：

```yaml
instrument: NQ
asset_class: equity_index_futures
```

兩欄必須同所引用 sketch 及 catalog 一致。Insight 繼續係純記錄：

- 不自動變成策略；
- 不直接開回測；
- execution runtime 永不查 insight repository；
- 之後若 Owner 決定採用洞察，仍由 Terminal 產生自包含新 `strategy.v1`。

## 6. 跨頁資料流

```text
P2 草圖
  Owner 選 root symbol NQ
  → catalog 帶入 class/name/currency/session
  → 首圖鎖 instrument
  → 匯出不可變 sketch.v1
  → Terminal 回傳自包含 strategy.v1
  → P2 驗 primary/class/universe/session/currency/rationale
  → Owner 接受整份策略
  → 不可變 strategy version
  → P4 只可選 universe ∩ configured/available data
  → run manifest 鎖 exact expiry-specific contract（例 NQ-202609-CME）
  → P5 分市場證據及 Owner 決定
  → result.v1 返回 Terminal 或進 P6 模擬盤
```

Root symbol 與 executable contract 分層：

- sketch／strategy：`NQ`，表示產品／市場意圖；
- data coverage：configured product 同可用資料；
- run manifest：`NQ-202609-CME` 等 exact contract id，決策後不可變；
- result／paper evidence：沿用 run snapshot，唔由當前 catalog 重新猜。

## 7. Error States

最少要分開：

- catalog loading／5xx／invalid：選擇及匯出 fail closed；
- unknown symbol/class；
- sketch primary 與 strategy primary mismatch；
- primary 不在 universe；
- cross-asset-class member；
- unsupported session；
- non-USD member；
- expansion rationale missing／extra／blank；
- incomplete composite lineage；
- complete lineage 但 local sketch missing（警告，唔等於 invalid）；
- legacy unexported draft（可補揀）；
- legacy exported package（readonly，需 duplicate）。

## 8. 前後端閉環與責任

Agent X：

- 擴充 canonical catalog model/config/API；
- 後端 sketch/strategy schema 同 validator；
- local sketch 對照、session/currency/class/rationale fail-closed；
- exact run contract snapshot 保持現有不可變原則；
- legacy 不改 bytes；validation 失敗零寫入；
- 不修改 `apps/web/` 或權威設計。

Agent Y：

- P2 草圖選擇／鎖定／legacy 狀態；
- 由 API catalog 顯示，唔硬寫 mapping；
- ZIP/repo export template 同 instructions；
- Terminal 返程唯讀 universe 對照、整份接受／退回；
- insight instrument/class；
- Owner-review 三態 fixture；
- 不修改 Python/backend 或權威設計。

共享 seam：

- `GET /api/v1/data/coverage` additive catalog fields；
- `sketch.v1`、`strategy.v1`、`insight.v1` 由本文件同 `docs/05` 定義；
- X、Y 各自做 contract tests；接線時用相同 fixtures；
- 任何一方遇到 schema 歧義，停止該分支並喺各自渠道提出，唔自行發明兼容欄位。

## 9. 驗收／Mutation

必須至少證明：

1. NQ sketch → NQ+YM strategy 合法，rationale 顯示，P4 可選子集；
2. NQ sketch → NQ+GC strategy fail closed，確認前零寫入；
3. strategy primary YM、sketch primary NQ fail closed；
4. primary 不在 contracts fail closed；
5. unknown symbol/class、unsupported session、non-USD、rationale 缺／多／空各自 fail closed；
6. primary-only universe 必須用空 mapping；
7. local sketch missing 只降級左右對照，唔繞過 self-contained strategy/catalog validation；
8. frontend 首圖後不能改 instrument；export 後只能 duplicate；
9. legacy draft 不推斷；legacy exported package bytes 不改；
10. frontend 移除 fail-closed gate、backend 移除 same-class check、把 catalog 改成 hard-coded fallback、把 rationale exact-key 改成 subset，指定測試各自變紅。

## 10. 自我審查

- **閉環完整**：Owner 意圖由 P2 root symbol 開始，去到 exact run contract、P5 證據、Terminal 迭代／P6，身份無中斷。
- **無暗中改策略**：P2 全文接受／退回；P4 只選本次 run subset，唔改 strategy version。
- **無雙重真相**：class/name/currency/session 由一份 catalog；strategy 自包含聲明仍由 validator 同 catalog 對照。
- **legacy 誠實**：不猜、不補、不重寫；歷史證據可讀，新 run 嚴格。
- **MVP 邊界清楚**：無 OCR、無跨 asset-class strategy、無 FX conversion、無 insight runtime linkage、無獨立設定頁。
