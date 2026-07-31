# P2 市場洞察可恢復歸檔設計

日期：2026-07-28

狀態：**Owner 2026-07-28 已批准完整書面規格；X backend工作令已隨`[X-075]`發出**

範圍：P2「市場洞察」分頁、`insight.v1` repository、歸檔及恢復

不屬本規格：P5、P6、IB Gateway、洞察統計驗證、策略 runtime linkage

## 1. Owner 決定

Owner 於 2026-07-28 批准：

1. 洞察不做永久刪除，採用**可恢復歸檔**。
2. 歸檔粒度是完整 composite identity：
   `origin + insight_id`，包括該 identity 的**全部版本**。
3. 活躍資料由：

   ```text
   data/insights/<origin>/<insight_id>/
   ```

   歸檔到：

   ```text
   data/insights/_deleted/<origin>/<insight_id>/<archive_id>/
   ```

4. 歸檔保存所有版本原始 bytes、SHA、版本清單及刪除時間。
5. 恢復不准合併或覆蓋現有 active identity。
6. MVP 不提供永久清除功能。

本批准是**產品語義及書面設計批准**，不是授權 executor
讀寫或歸檔現有真 `data/insights/`。

## 2. 現行事實

1. 洞察身份是 `origin + insight_id`；版本身份再加 `version`。
2. 現行 backend 將每個版本保存於：

   ```text
   data/insights/<origin>/<insight_id>/v<version>.yaml
   ```

3. 每個版本不可變；修改內容必須成為新版本。
4. stored record 保存 Terminal 原始 `source_text` 及其
   `content_sha256`，讀取時會重新驗證。
5. backend 現時只有 import、list、detail，刻意沒有 delete、
   restore 或 status mutation。
6. 現行 frontend 尚有 local-storage delete helper；它不是 backend
   真相，亦不可直接升格成正式刪除語義。
7. 洞察不會 runtime 連接策略或回測；但策略文件可以用
   `based_on_insights` 保存純 provenance，所以永久刪除仍會破壞
   事後查證能力。

## 3. 方案比較

### A. 可恢復、分區歸檔（採用）

完整 version chain 移出 active namespace，保存到 `_deleted/`，
需要時原 byte 恢復。

優點：

- 誤操作可逆；
- active list 清晰；
- provenance 仍可查；
- 不要求所有 reader 理解 tombstone；
- YAML 體積小，保存成本極低。

### B. Active 位置加 soft-delete marker（不採用）

資料留在原目錄，只靠 metadata 隱藏。

不採用原因：

- 每個 filesystem reader、Terminal agent 及未來工具都要理解 marker；
- 忽略 marker 就會把已刪洞察當 active；
- active namespace 不再代表單一真相。

### C. 永久刪除（不採用）

不採用原因：

- 誤刪不可逆；
- 省下的容量極少；
- 會斬斷版本及 provenance 證據；
- 與洞察「先記錄、後證實」的定位衝突。

## 4. 核心語義

### 4.1 歸檔單位

`DELETE` 的 target 是整個 `(origin, insight_id)`，不是單一版本。

例如 active identity 有 `v1`、`v2`、`v4`，一次歸檔必須保存三個
版本；版本有 gap 仍合法，不能重新編號或補造 `v3`。

UI 必須寫「歸檔」，不可聲稱永久刪除。歸檔前要明示：

```text
會歸檔此洞察的全部 N 個版本；之後可在「已歸檔」恢復。
```

### 4.2 Active 與 archived 真相

- 正常 list/detail 只讀 active namespace。
- `_deleted/` 永遠不出現在正常洞察列表。
- archived identity 只由 archive API 讀取。
- 同一 identity 在穩定狀態只可處於 active 或 archived 其中一邊。
- 系統若無法證明唯一狀態，必須 fail closed，不能猜測或顯示成功。

### 4.3 Identity 不重用

當 identity 處於 archived 狀態：

- 不准 import 同一 `(origin, insight_id)` 的任何版本；
- 不准用新內容重用舊 version number；
- Owner 必須先恢復整條 identity，之後才可正常新增下一版本。

### 4.4 不設 reference gate

洞察仍遵守 P2 約束 #22/#23：

- 不 runtime 連策略；
- 不直接連回測；
- execution layer 永不查 insight repository。

因此歸檔不需要查 standard run 或阻止策略執行。既有
`based_on_insights` 只屬 provenance；可恢復 archive 已負責保存證據。

## 5. Archive layout 與 manifest

每次歸檔產生一個不可猜測、全域唯一的 `archive_id`。同一 identity
最多只可有一個尚未恢復的 archive entry；若掃描到多於一個，repository
狀態不可證明，必須回 503。`archive_id` 格式為：

```text
delete-<32 lowercase UUID4 hexadecimal characters; hyphens removed>
```

例：

```text
delete-71d334b04f83468e90edff24fa511c9f
```

目錄：

```text
data/insights/_deleted/
└── workshop/
    └── insight-007/
        └── delete-71d334b04f83468e90edff24fa511c9f/
            ├── _archive.yaml
            ├── v1.yaml
            └── v2.yaml
```

版本檔必須與 active source file **byte-exact**。不准 parse 後重新
serialize。

`_archive.yaml` exact schema：

```yaml
schema: insight_delete_archive.v1
archive_id: delete-71d334b04f83468e90edff24fa511c9f
origin: workshop
insight_id: insight-007
deleted_at: 2026-07-28T12:34:56.123456Z
version_count: 2
versions:
  - version: 1
    filename: v1.yaml
    file_sha256: <sha256 of exact stored-file bytes>
    source_content_sha256: <stored source_text content_sha256>
  - version: 2
    filename: v2.yaml
    file_sha256: <sha256 of exact stored-file bytes>
    source_content_sha256: <stored source_text content_sha256>
```

規則：

1. `deleted_at` 是 canonical UTC timestamp，exact 格式為
   `YYYY-MM-DDTHH:MM:SS.ffffffZ`（六位 fractional seconds）。
2. `versions` 依 numeric version 升序。
3. `version_count == len(versions)`。
4. `filename` 只可為 canonical `v<n>.yaml` basename。
5. `file_sha256` 驗完整 stored-file bytes。
6. `source_content_sha256` 必須等於該 stored record 原有 digest，
   並重新對 embedded `source_text` 驗證。
7. manifest identity、path identity、每個 stored record identity
   必須全部 exact match。
8. 任何 target、manifest 或 version collision 都不准覆蓋。

## 6. Backend API contract

### 6.1 歸檔

```http
DELETE /api/v1/insights/{origin}/{insight_id}
```

成功：HTTP 200，top-level exact keys：

```json
{
  "schema": "insight_archive.v1",
  "origin": "workshop",
  "insight_id": "insight-007",
  "archive_id": "delete-71d334b04f83468e90edff24fa511c9f",
  "deleted_at": "2026-07-28T12:34:56.123456Z",
  "version_count": 2,
  "archived_to": "data/insights/_deleted/workshop/insight-007/delete-71d334b04f83468e90edff24fa511c9f"
}
```

`archived_to` 只可為 repo-relative POSIX path；不可洩漏 absolute
path、drive letter、反斜線或 `..`。

### 6.2 已歸檔列表

```http
GET /api/v1/insights/archives
```

成功：HTTP 200：

```json
{
  "schema": "insight_archive_list.v1",
  "count": 1,
  "archives": [
    {
      "archive_id": "delete-71d334b04f83468e90edff24fa511c9f",
      "origin": "workshop",
      "insight_id": "insight-007",
      "deleted_at": "2026-07-28T12:34:56.123456Z",
      "version_count": 2
    }
  ]
}
```

`archives` 依 `deleted_at` 新至舊排列；相同 timestamp 時，
`origin`、`insight_id`、`archive_id` 依字典序升序。

### 6.3 恢復

```http
POST /api/v1/insights/archives/{origin}/{insight_id}/{archive_id}/restore
```

request body：無。

成功：HTTP 200，top-level exact keys：

```json
{
  "schema": "insight_restore.v1",
  "origin": "workshop",
  "insight_id": "insight-007",
  "archive_id": "delete-71d334b04f83468e90edff24fa511c9f",
  "restored_at": "2026-07-28T12:40:00.000000Z",
  "version_count": 2,
  "restored_to": "data/insights/workshop/insight-007"
}
```

`restored_at` 使用與 `deleted_at` 相同的 canonical UTC exact 格式。

成功恢復後：

- 所有 active version bytes 必須與 archive 完全一致；
- archive entry 才可被消耗及移除；
- 正常 list/detail 再次顯示該 identity。

### 6.4 Errors

| 情況 | HTTP | 寫入 |
|---|---:|---|
| active identity 不存在 | 404 | 零寫入 |
| archive identity 不存在 | 404 | 零寫入 |
| path identity／格式非法 | 404 | 零寫入 |
| restore 時 active identity 已存在 | 409 | 零寫入；不合併、不覆蓋 |
| identity 已 archived 時重新 import | 409 | 零寫入；提示先恢復 |
| target collision／並發狀態衝突 | 409 | 不覆蓋 |
| archive、digest、manifest 或 storage 無法證明 | 503 | fail closed |

所有錯誤訊息要講清楚 active 與 archive 是否有改變；不能把 storage
failure 說成「找不到」。

## 7. Filesystem safety

### 7.1 Archive

歸檔成功前必須：

1. 驗證 active identity 的所有版本；
2. 讀取 exact bytes 並計算 file SHA；
3. 完整 materialize temp archive；
4. 驗證 temp manifest、file SHA、source SHA 及 identity；
5. no-overwrite publish archive；
6. archive 可證明完整後才移除 active；
7. 再驗證 active 已不存在、archive 完整，才回 HTTP 200。

任何 failure injection 不得造成：

- active 消失但 archive 不完整；
- 可見半份 archive；
- target 被覆蓋；
- API 回成功但 active 仍存在；
- identity 同時被正常 list 與 archive list 當作穩定成功狀態。

### 7.2 Restore

恢復成功前必須：

1. 驗證 archive manifest 及全部 exact bytes；
2. 證明 active target 不存在；
3. no-overwrite materialize active；
4. 驗證 active bytes、identity 及 version chain；
5. active 完整後才消耗 archive；
6. 再驗證 archive 已不存在、active 完整，才回 HTTP 200。

若任何 rollback 或 cleanup 狀態無法證明，回 503 並令相關 read
fail closed；不可猜一邊為真。

### 7.3 Concurrency

Import、archive、restore 必須共用同一 repository mutation boundary。
以下 race 必須只有一個合法勝方：

- archive vs archive；
- archive vs import next version；
- restore vs restore；
- restore vs import；
- list/detail 與 mutation 交錯。

任何結果都不得遺失版本、覆蓋 target 或產生不同內容的同一版本。

## 8. Frontend behavior

1. 正常洞察列的動作用字改為「歸檔」。
2. 點擊後進入約 5 秒 grace：
   - 顯示「待歸檔 · 復原」；
   - grace 期間零 API write；
   - 點「復原」即取消，之後不得發 request。
3. component unmount、頁面切換或 refresh 時，尚未到期的 grace
   **取消**，不在背景偷偷歸檔；重新載入後 active row 會再次出現。
4. timer 到期才發一次 DELETE；不自動 retry。
5. 只有 strict parse 合法 200 response 才可移除 active row。
6. 404、409、503、network error 或 malformed 200：
   - active row 回復；
   - 顯示完整、可行動的人話原因；
   - 重新讀 active 及 archive lists。
7. 提供「已歸檔」區域，只顯示 archive list；每項可「恢復」。
8. Restore 只發一次；strict parse 合法 200 後才移回 active list。
9. frontend 不得以 localStorage tombstone 或舊 `deleteInsight()` helper
   冒充 backend 真相。
10. Owner-review fixture 及普通 live mode 必須保持 storage 隔離。

本設計刻意不沿用策略刪除「離頁＝確認」：洞察歸檔沒有 run
reference 壓力，而未送出的 destructive request 在 navigation/unload
期間不能可靠證明；安全、誠實的結果是取消 pending action。

## 9. Non-goals

本批不做：

- 永久 purge endpoint 或 UI；
- 單一 version 歸檔；
- archive merge；
- archived identity 改名；
- status transition backend；
- 洞察自動統計、supported 自動判斷；
- insight → strategy 按鈕；
- strategy/run reference lookup；
- P5、P6、IB 或真市場資料操作；
- 現有真 `data/insights/` migration。

## 10. 驗收與證據層級

### 10.1 Backend

至少驗：

1. 多版本 whole-identity archive；
2. active version file byte-exact archive；
3. manifest file/source SHA；
4. active list 不見 archived identity；
5. archive list ordering 及 exact schema；
6. restore byte-exact；
7. archived identity import 409；
8. restore active collision 409；
9. invalid origin/id/archive id 不能 path traversal；
10. corrupt manifest/file/digest fail closed；
11. archive publish、active remove、restore publish、archive consume
    failure injection；
12. 四組 mutation race；
13. target collision no-overwrite；
14. 無永久 purge/PATCH endpoint；
15. temp fixture root 以外零寫入。

### 10.2 Frontend

至少驗：

1. click → grace 期間零 request；
2. undo → 永遠零 request；
3. unmount/navigation → cancel；
4. expiry → DELETE exact 一次、零 retry；
5. exact response parser；
6. malformed 200 不當成功；
7. 404/409/503/network 恢復 row；
8. archive list 及 restore；
9. restore collision/error 保留 archive row；
10. composite identity exact；
11. owner-review storage isolation；
12. 舊 local delete helper 不再是 live truth。

### 10.3 REVIEW 標示

- X backend 單獨完成最多是 **COMPONENT PASS／CONTRACT PASS**。
- Y frontend mock contract 完成最多是 **CONTRACT PASS**。
- 真 frontend + backend server 接通才可叫 **INTEGRATION PASS**。
- 真 browser、真 backend、受控真 repository 及事前事後 SHA
  全通才可叫 **TRUE E2E PASS**。

本設計批准不等於 TRUE E2E 寫入授權。若要用現有真
`data/insights/` 做 archive/restore smoke，C 必須另列 exact target、
expected writes、before/after SHA 及 recovery，再由 Owner 明確批准。

## 11. 工作切分

書面規格已獲 Owner 最終確認；現行切分：

1. **X backend 批次**已隨`[X-075]`開始，只改 insight repository、route
   及 backend tests；全部使用 temp root，與 Y 現行 P5 frontend
   Stage 2 零檔案衝突。
2. **Y frontend 批次**須等現行 P5 Stage 2 REPORT 及 C REVIEW
   完成後另發工作令；不可中途換線。
3. X、Y 各自 REPORT 後由 C 獨立審查。
4. integration／true E2E 另立 gate；不自動開真資料或 IB。

## 12. 完成定義

只有同時滿足以下條件，洞察歸檔功能才算工程閉環：

1. ✅ Owner 已確認本書面規格；
2. backend archive/list/restore contract 通過；
3. frontend grace/undo/archive/restore live seam 通過；
4. frontend/backend 真接通；
5. 受控 repository before/after exact facts 通過；
6. 無永久刪除、無 identity reuse、無 overwrite；
7. C REVIEW 標明實際證據層級，不以 component test 冒充 true E2E。
