# Composite Sketch Identity Design

日期：2026-07-26

狀態：**Owner 已拍板方案 A**

範圍：`sketch.v1` 儲存／讀取、`strategy.v1`／`insight.v1` 草圖溯源；不新增頁面

## 1. 決定

一張草圖嘅 canonical identity 係：

```text
(origin, sketch_id)
```

其中：

- `origin` 只可以係 `workshop` 或 `journal-app`；
- `sketch_id` 必須係 `sketch-YYYYMMDD-NN`；
- `YYYYMMDD` 必須係真實日曆日；
- `NN` 正好兩位；
- identity 欄位前後空白一律 invalid，唔靜靜 normalize。

原因：兩個獨立 app 都會由每日 `01` 開始編號，所以可以同時產生 `sketch-20260726-01`。只用 `sketch_id` 會拒絕第二個合法包，亦會令策略回鏈唔知要讀邊張草圖。

## 2. 儲存同 API

持久化路徑：

```text
data/sketches/<origin>/<sketch-id>/
  meta.yaml
  INSTRUCTIONS.md
  chart-D.png
  chart-1H.png
  chart-30m.png
  chart-5m.png
```

ZIP 內部 root 仍然只係 `<sketch-id>/`；`origin` 已存在 `meta.yaml`，importer 由已驗證 metadata 決定外層儲存位置。唔准信 request path 或 client 額外傳入另一個 origin。

API：

```text
POST /api/v1/sketches
GET  /api/v1/sketches
GET  /api/v1/sketches/{origin}/{sketch_id}
GET  /api/v1/sketches/{origin}/{sketch_id}/images/{filename}
```

規則：

- list item、detail identity、所有 image URL 都必須帶 `origin`；
- 同一 `(origin, sketch_id)` 再 import → 409，而且原 bytes 不變；
- 同一 `sketch_id`、不同 `origin` → 兩包都接受、分開讀取；
- 唔保留只用 `sketch_id` 嘅 ambiguous detail alias。批 1 API 尚未收貨，未有正式 consumer，應該喺接前端前移除歧義；
- flat ZIP 或 `<sketch-id>/` 單一 root ZIP 都繼續接受；其他 ZIP 安全規則不變。

目前真 `data/sketches/` 不存在，所以冇 production package 要搬；唔建立 migration、alias、兼容副本或猜 origin。

## 3. Lineage 合約

### `strategy.v1`

新文件 `meta` 必須同時有：

```yaml
based_on_sketch: sketch-20260726-01
based_on_sketch_origin: workshop
```

### `insight.v1`

新文件必須同時有：

```yaml
based_on_sketch: sketch-20260726-01
based_on_sketch_origin: workshop
```

兩個欄位係一個不可拆開嘅 composite reference：

- 任一缺席、`null`、空字串、非法 enum、非法 ID → references layer invalid；
- 本機搵唔到該 `(origin, sketch_id)` → 仍然 valid；UI 只需誠實顯示本機缺失；
- importer／validator 唔准以本機存在性代替格式或 lineage 完整性；
- terminal 指令書必須預填兩欄並標「唔准改」。

StrategyVersion 儲存摘要同既有 strategy API 可以 additive 增加 `based_on_sketch_origin`。既有欄位、方法、狀態碼同型別不可變。

## 4. Legacy 邊界

`strategy-0001`／`strategy-0002` 同既有 run／result：

- 原 bytes、hash、`source_text` 全部不改；
- list／read 繼續可用；
- 任何重新驗證原 YAML 嘅新 run／rerun 仍然 invalid；
- 不補假 origin、不由 `based_on_sketch` 或本機檔案猜 origin、不開 legacy validator；
- 舊 StrategyVersion API 可 additive 顯示 `based_on_sketch_origin: null`，但 null 唔會令舊版本取得新 run 資格。

若 Owner 要重用舊邏輯，仍然係真實新草圖 → terminal 產生同時帶兩個 lineage 欄位嘅新策略版本。

## 5. Validator 補強

同一個 canonical identity parser 要供 sketch package 同 strategy references layer 使用，避免兩邊規則漂移。

除 composite identity 外，批 1 correction 一併修：

1. `created`／`charts[].range` 只收 YAML date 或精確 `YYYY-MM-DD`；int、float、bool、datetime／timestamp 全拒絕；
2. `indicators_shown` 只收 `docs/05` 定義嘅 canonical grammar：
   - `ema|sma|atr|rsi` ＋正整數；
   - `vwap|volume|macd`；
   - `bb<正整數>_<正數參數>`；
   - 其他文字放 `indicators_other`；
3. P2 時間框架仍可由 Owner 修改及重複；檔名唔可以反過來鎖死 timeframe。

## 6. Error Handling

驗證錯誤保持：

```text
<path>: <message> — <fix>
```

Composite identity 錯誤要分清：

- origin 缺失／invalid；
- sketch id 格式／日期／流水號 invalid；
- package folder 同 metadata id 不一致；
- 同一 composite identity 已存在；
- 本機缺少 composite reference（只係 UI availability，唔係 validator error）。

任何 validation／寫入錯誤都唔准留下 target 或 `.tmp`；成功發布前不可被 list／read。

## 7. Contract Tests

必須直接證明：

1. `workshop/sketch-20260726-01` 同 `journal-app/sketch-20260726-01` 可同時 import、list、detail、讀圖，而且 bytes 各自正確；
2. 同一 pair 第二次 import 409、原 bytes 不變；
3. detail／image URL 缺 origin 無 ambiguous alias；
4. strategy lineage 缺任一欄、invalid origin、invalid id 都紅；完整 pair 即使本機缺包仍綠；
5. 三位流水號、假日曆日、前後空白、數字日期／range 全紅；
6. `madeup999` 唔可以進入 `indicators_shown`；
7. 五個 legacy strategy operation 嘅 status、必需 key、primitive type 同重要 nested shape 保持；boolean→string mutation 必紅；
8. ZIP traversal、mixed root、case-insensitive duplicate、missing／extra、non-UTF8、假 PNG、錯 Content-Type、寫入中途失敗全部 fail-closed；
9. 真 legacy 兩檔 bytes／SHA-256 前後相同，舊 result 可讀，新 run 拒絕；
10. 移除 origin store key、移除 lineage origin check、放鬆 canonical parser、把 legacy boolean 變 string，指定測試各自紅。

## 8. 前後端分工

Agent X 本批：

- composite store／API；
- strategy lineage model／validator／stored summary；
- schema、legacy、ZIP、mutation tests。

Agent X 本批不做：

- `insight.v1` repository／import API；
- 前端 template、UI 或 API client；
- derive／delete、批 2 或之後功能。

Agent Y 之後另批：

- P2 產生嘅 `strategy.v1`／`insight.v1` 指令書預填 origin；
- repo path／開場白改用 composite 路徑；
- P2 sketch read client 用 origin-aware URL；
- UI 正常只顯示人話來源，唔新增設定頁。

在 Y 接線前，X 嘅 normal API 要 fail-closed；唔准為兼容未更新前端保留 ambiguous id-only lookup。
