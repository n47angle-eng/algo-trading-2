# P2 Strategy Version Lifecycle Backend Design

日期：2026-07-27

狀態：**已批准實作**

Owner 決定：X 可以同 Y-v3 並行開工，但兩邊 scope 必須完全分離

## 1. 目的

補齊 P2 分頁③「版本庫」兩個仍然只得前端 provisional 行為、未有真後端
truth 嘅缺口：

1. Owner 只改已批准數值 leaf，服務端產生一個 immutable direct-child
   strategy version；
2. 冇 standard run 引用嘅版本，經前端約 5 秒 undo grace 後，由服務端
   真正歸檔刪除。

本設計只處理後端產品 truth。Y-v3 目前只接 P4 normal live seam；今批唔改
`apps/web/`，亦唔要求 Y 等 X。

## 2. 權威依據

- `docs/ui/designs/p2-strategy-workbench.html`
  - #16：列表冇直接改參數；
  - #17：只准改數值，structures／入市序列唯讀；
  - #18：生成新版本、原版不變、來源「Owner UI 微調」；
  - #19：standard run 引用阻止刪除，validation run 唔計；
  - #20：歸檔到 `data/strategies/_deleted/<id>.yaml`，保留 YAML 原文、
    刪除時戳、當時狀態；碰撞唔覆蓋；
  - #21：約 5 秒 undo 係前端延遲；後端只接收 grace 完成後嘅 final delete。
- `docs/03-frontend-component-coverage-matrix.md` P2-L01–P2-L06。
- `docs/05-file-contract-schemas-draft.md` strategy.v1。
- `docs/08-ui-backend-mapping.md` P2 分頁③。
- 當時`[X-002]`／`[X-003]`已批准嘅additive derive／delete方向；舊X channel
  已從現行工作樹刪除，需要事故審計時由C從Git history精確取回。

## 3. 公開 API

### 3.1 衍生版本

```http
POST /api/v1/strategies/{parent_strategy_id}/derive
Content-Type: application/json
```

request：

```json
{
  "patches": [
    {
      "path": "indicators.ema_fast.period",
      "value": 21
    }
  ]
}
```

request 只可以有 `patches`；每項只可以有 `path` 同 `value`。`path` 必須
exact、非空、無前後空白、不可重複，而且必須係 parent 詳情中
`parameters[].kind == "numeric"` 嘅 exact path。`value` 必須係 finite JSON
number；boolean、string、null、array、object、NaN、Infinity 全部拒絕。

成功：

```json
{
  "schema": "strategy_derive.v1",
  "parent_strategy_id": "strategy-0002",
  "changed_count": 1,
  "changed_paths": [
    "indicators.ema_fast.period"
  ],
  "deduplicated": false,
  "version": {
    "...": "完整既有 strategy_version.v1 shape"
  }
}
```

`changed_paths` canonical 按 path 排序。重試同一 parent＋同一 effective patch
可以回原有 byte-identical direct child，`deduplicated: true`，唔可以鑄造 twin。

### 3.2 最終刪除

```http
DELETE /api/v1/strategies/{strategy_id}
```

呢個 operation 代表前端 5 秒 grace 已經完結；backend 冇 undo／restore
endpoint。成功：

```json
{
  "schema": "strategy_delete.v1",
  "strategy_id": "strategy-0003",
  "deleted_at": "2026-07-27T12:34:56Z",
  "archived_status": "draft",
  "archived_to": "data/strategies/_deleted/strategy-0003.yaml"
}
```

`archived_to` 只回 repo-relative POSIX path，唔洩漏本機 absolute path。

## 4. Derive truth

1. 服務端自己讀 parent `source_text`，client 唔可以交整份 replacement YAML。
2. parent source 要先經現行 canonical parser；legacy／損壞 parent 唔可以
   derive，亦唔可以開第二套寬鬆 validator。
3. empty patch、no-op patch、unknown path、structural path、任何會改
   `meta`／lineage／universe／structures／entry sequence／identity 嘅 path
   全部 422、零寫入。
4. 新 YAML 除以下位置外，semantic mapping 必須同 parent exact：
   - request 指定嘅 approved numeric leaf；
   - 對應 provenance entry 改成
     `source: owner_explicit`、`note: Owner UI 微調`；
   - `meta.based_on` 強制等於 URL 嘅 direct parent id。
5. parent 本身已經有 grandparent 時，必須覆蓋成 direct parent；唔准保留
   grandparent，亦唔准相信 client。
6. 新版本一律係新 `draft`；原 parent JSON、`source_text`、status、digest、
   timestamps byte-for-byte 不變。
7. 新 source 仍要通過同一 canonical parser；唔准 bypass universe、
   composite sketch lineage 或 provenance gate。
8. 同步 derive 要分配 unique monotonic id，唔准 partial JSON、重用 id 或
   改寫既有檔案。

## 5. Delete truth

1. delete 前用已收貨 `RunReferenceCatalog.references_for_strategy()` 查
   exact strategy id。
2. 只要有一個 standard run，409，回 stable 人話摘要、exact count 同
   run ids；validation-only references 唔阻擋。
3. 只要 `count_known == false`、有 unindexed candidate、main 未 migration、
   proof／index integrity error或 lookup failure，一律 503 fail-closed，
   active strategy同archive bytes零改動。
4. 今批只按已批准 persisted run-reference truth；唔掃 manifest JSON、
   唔改 P4 queue，亦唔另加未批准嘅 queued-job gate。
5. archive 格式：

```yaml
schema: strategy_delete_archive.v1
strategy_id: strategy-0003
deleted_at: 2026-07-27T12:34:56Z
status: draft
content_sha256: "<64 lowercase hex>"
source_text: "<exact original YAML string>"
```

`source_text` load 後要同 active record 內原字串 byte-exact；
`content_sha256` 必須吻合。archive metadata唔可以冒充 strategy.v1 本體。
6. target 已存在＝409，唔准 overwrite／merge／自動改名。
7. 先安全 materialize archive，再移除 active JSON；任何 injected write、
   publish或remove failure，都唔可以令 active version消失或留下可見嘅
   半份 archive。
8. 成功後 list唔再列、detail／confirm／derive回404。
9. strategy id 永不重用：allocator 必須同時計 active JSON 同
   `_deleted/strategy-*.yaml` 嘅最高流水號。
10. delete 係 preservation operation，唔因 legacy source 缺新 composite
    lineage而拒絕歸檔；但 active record 至少要有 exact id、合法現況 status、
    non-empty source_text，同 digest parity。損壞 storage fail-closed。

## 6. HTTP/error boundary

| 情況 | 狀態 |
|---|---:|
| unknown／已刪 strategy id | 404 |
| malformed derive request／invalid patch／no-op／canonical validation fail | 422 |
| standard run 引用／archive target collision | 409 |
| reference unknown／migration required／integrity或storage publication failure | 503 |

錯誤只回 stable、可行動、人話 public detail；完整本機 path／trace只留 server
log。任何 failure 都要證明 zero visible write。

## 7. Compatibility／並行邊界

- 舊 `/validate`、`/import`、list、detail、`/confirm` route、request同
  response shape exact 不變。
- 今批只新增 operations；Y現有 client完全可以唔知佢哋存在。
- P4-A／P4-B backend contracts凍結；唔准改
  `backtest_admission.py`、`batch_queue.py`、`routes_batch.py`、runner、
  operational documents或P4 tests。
- 唔改 `apps/web/`、六份權威設計、`docs/05`、`data/`、migration、IB、
  P5、P6、insight repository。
- 真 frontend derive／delete seam 等 Y完成現行P4後，由C另出工作令。

## 8. Acceptance

除正常 success contract外，至少有以下會殺死 mutation 嘅證據：

1. structural path 被放行；
2. grandparent 被保留而冇覆蓋 direct parent；
3. parent bytes被改；
4. validation run錯當 blocker；
5. unknown references錯當 known-zero；
6. archive collision被覆蓋；
7. 刪最高 id 後 allocator重用 id；
8. archive publication失敗但 active 被移除。

測試只用 temp roots／injected catalog；唔准碰真
`data/strategies`、真 DB、legacy artifact或IB Gateway。
