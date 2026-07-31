# 文件合約 Schema 權威規格

日期：2026-07-24 | 作者：Agent C | 狀態：**v1 定稿 — Owner 五項決策點全部拍板（2026-07-24）**
原則：呢份文件本身就係將來俾 terminal AI 嘅「指令書」——AI 睇完要寫得出合格文件；app 驗證器照住逐條檢查，錯誤訊息帶路徑，Owner 可以原句貼返俾 AI 修。

> 檔名保留 `-draft` 只為避免打斷既有渠道同工作令引用；**文件狀態唔係草稿**。本頁標明嘅 schema 及較新修訂係實作權威。

## 0. 格式總原則（建議）

| 文件 | 格式 | 理由 |
|---|---|---|
| 策略文件 | **YAML** | 人（Owner）要逐項過目、AI 喺 terminal 寫——可讀性優先 |
| 盤前計劃 | **YAML** | **Post-MVP 候選；MVP 不產生、不驗證、不讀取**（Owner 2026-07-26 修訂） |
| 結果文件 | **JSON** | 機器生成、AI 讀取——嚴格性優先 |

共同規則：每份文件第一行 `schema: <name>.v<N>`；keys 用英文（程序穩定），`rationale`／`note` 類文字值可以用廣東話；驗證錯誤格式＝`<路徑>: <乜嘢錯> — <點修>`。

---

## 1. 策略文件 `strategy.v1`（YAML）

> **🔴 v1.4 修訂（2026-07-27，Owner 拍板）：加入 primary instrument 閉環。** `universe` 必須自包含 primary、asset class、完整 contracts、逐項擴展理由同 session；App 只可接受／退回整份文件。Composite sketch identity 規則繼續跟 v1.3；詳見 §1.1a–§1.1c。

### 1.1 結構（十個頂層段）

```yaml
schema: strategy.v1
meta:
  name: 大框架武裝細框架回踩_v3
  created: 2026-07-24
  based_on_sketch: sketch-20260724-01   # ✅ 同下面 origin 成對必填（v1.3）
  based_on_sketch_origin: workshop      # ✅ workshop | journal-app
  based_on: strategy-0002        # 迭代鏈：由邊個版本改出嚟（首版留空）
  spec_ref: TRADING_SPEC_v0.41   # 對應正典版本

rationale: |                     # 必填：呢個 edge 點解存在（俾維度8用）
  大時間框架趨勢中嘅細框架回調係機構分批建倉行為……

unquantified_notes:              # 必填（冇嘢寫就 []）：AI 譯唔到/自行假設咗嘅嘢
  - note: 「盡量希望喺死亡交叉下入市」已按硬條件處理
    action_needed: Owner 確認係咪想改評分制

universe:
  primary_instrument: NQ         # Owner 草圖嘅 primary；必須亦在 contracts
  asset_class: equity_index_futures
  contracts: [NQ, YM]            # 全部必須喺 contracts.yaml、同 class、同 session
  expansion_rationale:           # keys 必須恰好 = contracts - primary；primary-only 寫 {}
    YM: 同屬股指趨勢結構；價差以 ticks／R 正規化，仍待回測
  session: rth                   # rth | eth

timeframes:
  trio: { bias: D, mid: 1H, entry: 5m }
  entry_layers: 3                # 3=細層入市 | 2=中層入市

indicators:                      # 第一層積木：指標實例（有 id 俾下面引用）
  - { id: ema_fast, type: EMA, period: 18 }
  - { id: ema_slow, type: EMA, period: 90 }
  - { id: atr,      type: ATR, period: 14 }

structures:                      # 第二層積木：具名狀態機（引擎庫實現，呢度只點名+傳參）
  - { id: pb_mid,   type: pullback_lifecycle, layer: mid,   touch: ema_slow }
  - { id: pb_entry, type: pullback_lifecycle, layer: entry, touch: ema_slow }
  - { id: sig_in,   type: signal_bar, variant: inside, entry_ref: mother_high, stop_ref: mother_low }
  - { id: sig_mg,   type: signal_bar, variant: magic }

regime:                          # 主閘（spec §11.2–11.3）
  require_trend: true
  congestion_no_trade: true
  sep_mult:  { calibration: percentile, value: 65 }
  flat_mult: { calibration: percentile, value: 65 }

direction:
  mode: trend_following          # trend_following | reversal
  layer_consistency: hard        # 中細方向必須一致（spec §0）

entry:
  sequence:                      # 有序事件序列；require 引用上面 id 嘅狀態
    - { layer: mid,   require: "cross_state(ema_fast, ema_slow) == golden" }
    - { layer: mid,   require: "pb_mid.completed" }
    - { layer: entry, require: "cross_state(ema_fast, ema_slow) == golden" }
    - { layer: entry, require: "pb_entry.completed" }
    - { layer: entry, require: "signal_bar in [sig_in, sig_mg]" }
  trigger: { type: breakout, of: signal_bar.entry_ref, mode: intrabar }

invalidations: [cross_reversal_per_layer, day_end_clear]   # spec §12.4 具名規則

risk:
  stop:   { anchor: signal_bar.stop_ref, offset_ticks: 1 }
  target: { type: r_multiple, value: 1 }
  sizing: { type: fixed_fractional, risk_pct: 1.0 }
  daily_loss_limit_r: 3

provenance:                      # 每個數值參數一條（路徑指返上面）；驗證器檢查冇漏
  - { path: indicators.ema_fast.period, source: owner_explicit }
  - { path: risk.sizing.risk_pct,       source: market_convention, note: 業界慣例 0.5–2% }
  - { path: regime.sep_mult.value,      source: system_default }
```

### 1.1a 草圖複合溯源必填規則（v1.3，2026-07-26 Owner 拍板方案 A）

**規則**：`kind: strategy` 嘅文件，`meta.based_on_sketch` 同 `meta.based_on_sketch_origin` **成對必填**。前者必須係 canonical `sketch-YYYYMMDD-NN`（真日曆日、正好兩位流水號、零前後空白）；後者只可以係 `workshop | journal-app`。驗證器喺**引用層**檢查；任一缺席、`null`、空字串或非法值 → 打回，錯誤訊息要講返點修。

**點解由可選改成必填**：

分頁 ② 嘅「左右對照」——左邊 Owner 原文、右邊 AI 參數——**係嗰一頁存在嘅唯一理由**。個對照全靠 composite sketch lineage 搵返草圖。任一欄可缺，即係 AI 寫半套都收貨，而 Owner 三個月後先發現查唔返，亦分唔到兩個 app 產生嘅同名包。

**但唔准因此擋住跨系統使用**：

| 情況 | 點處理 |
|---|---|
| `(origin, sketch_id)` 草圖喺本機 | 正常，左邊顯示四圖四段 |
| **草圖唔喺本機**（另一部機出／Owner 清咗資料） | **照收**。左邊寫明搵唔到對應嘅 `(origin, sketch_id)`——冇得對照，**但唔准封鎖匯入、驗證、確認採用** |
| 任一 lineage 欄缺席、`null`、空字串或非法 | ❌ **打回**——呢個係「冇聲明完整溯源」，唔係「草圖唔喺度」，兩者唔同 |

**分野嘅重點**：我哋要求嘅係**AI 聲明佢由邊個 origin 嘅邊張草圖做**，唔係要求**本機有嗰張草圖**。前者係溯源，後者係方便。

**兩份指令書都要把兩個 lineage 欄預填並寫住「唔准改」**（`docs/ui/designs/instructions-v1-sample.md` ／ `-insight-sample.md`）；呢條規則係防止 AI 自作主張刪走或改到指向另一個來源。

### 1.1b 舊策略版本嘅嚴格邊界（v1.2／v1.3，2026-07-26）

現存 `strategy-0001`／`strategy-0002` 早於 v1.2／v1.3 建立；佢哋儲存嘅 `source_text` YAML 冇完整 composite lineage。頂層衍生摘要即使有 `based_on_sketch: null`／`based_on_sketch_origin: null`，亦**唔可以代替原 YAML 嘅真實溯源聲明**。

| 動作 | 結果 |
|---|---|
| 列出／讀取舊策略版本 | ✅ 保留，原檔不可變，供歷史審計 |
| 讀取既有 run／result artifact | ✅ 不受影響；歷史證據唔改、唔重算 |
| 用舊版本開始新回測、重新執行，或任何會重新驗證原 YAML 嘅動作 | ❌ 按同一套 v1.3 validator 判 invalid；冇完整 `origin + sketch_id` 唔算有效 lineage |
| 自動補一個假 sketch、改寫舊 YAML、開 legacy 寬鬆 validator | ❌ 全部禁止 |

如果 Owner 要重新使用舊邏輯，正確復原路徑係：**建立一張真實新草圖，再由 terminal 產生一個符合 v1.3、同時記 id＋origin 嘅新策略版本**。新草圖只代表今次重新確認，唔准冒認為當年原始草圖；舊版本同舊 run 照原樣留低。

### 1.1c Primary instrument 同 universe（v1.4，2026-07-27 Owner 拍板）

Canonical root/product catalog 係 `config/contracts.yaml`；每個 symbol 必須提供 `display_name`、`asset_class`、`currency` 同 `sessions`。MVP 初始 controlled mapping：

| Symbol | 顯示名 | `asset_class` |
|---|---|---|
| `NQ` | E-mini Nasdaq-100 | `equity_index_futures` |
| `YM` | E-mini Dow | `equity_index_futures` |
| `GC` | Gold | `commodity_futures` |

`universe` 五個欄全部必填，validation 順序：

1. `primary_instrument` 必須在 `contracts`；
2. primary 同每個 contract 都必須存在於 catalog；
3. 文件 `asset_class` 必須同 primary catalog exact match，而且全部 members 同一 class；
4. 每個 member 支援 `session`；
5. MVP 每個 member 必須係 USD；未有 FX conversion，其他幣別 fail closed；
6. `expansion_rationale` keys 必須恰好等於 `contracts - {primary_instrument}`，每個值非空；primary-only 寫 `{}`；
7. 本機存在 lineage 指向嘅 `sketch.v1` 時，strategy primary/class 必須同 sketch exact match；本機缺包仍按 §1.1a 接受完整 lineage，但其餘 self-contained validation 照跑。

P2 只顯示唯讀 universe，Owner 接受或退回整份 strategy；唔准用 checkbox 刪 member、唔另存 `approved_instruments`、唔補 rationale。P4 先由已確認 universe 同 configured/available data 嘅交集揀本次 run 子集。Sketch/strategy 用 root symbol（例如 `NQ`）；run manifest 另鎖 exact expiry-specific contract id（例如 `NQ-202609-CME`）。

### 1.2 設計要點

1. **數值只寫一次**：參數值住喺結構入面；`provenance` 用「路徑註解」指返去——避免值同註解兩邊唔同步。Source 枚舉：`owner_explicit / owner_inferred / web_researched / market_convention / system_default / derived`。
2. **兩層積木**照 D7：`indicators`+`entry.sequence` 係宣告式；`structures` 只准點名引擎庫已有嘅狀態機——AI 唔可以發明新 structure type，發明＝驗證器打回。
3. **`unquantified_notes` 係鐵閘**：AI 任何譯唔到、估咗、簡化咗嘅嘢必須入呢度；P2 策略工作台會逼 Owner 睇完先確認。
4. 驗證層級：格式 → 引用完整性（id 存在、路徑啱）→ 語義（TF 喺 trio 內、參數範圍）→ provenance 全覆蓋。

---

## 2. 結果文件 `result.v1`（JSON）

```jsonc
{
  "schema": "result.v1",
  "run": {
    "run_id": "run-2026-0042",
    "strategy_version": "strategy-0003",
    "manifest": { "contract": "NQ-202609-CME", "range": ["2026-01-05","2026-07-18"],
                  "capital": 100000, "costs": {...}, "fill_model": "conservative",
                  "data_fingerprint": "..." },
    "engine": { "nautilus": "1.230.0", "app": "..." }
  },
  "metrics": { "net_r": 42.5, "trades": 118, "win_rate": 0.47, "profit_factor": 1.62,
               "max_dd_r": -8.2, "dd_duration_days": 19, "sharpe": 1.1, "expectancy_r": 0.36,
               "skew": 0.8, "kurtosis": 4.2, "tail_ratio": 1.4, "var95_r": -1.9, "cvar95_r": -2.6,
               "psr": 0.87, "profit_concentration": { "top5_removed_net_r": 21.3, "top10_removed_net_r": 9.8 } },
  "scorecard": [
    { "dim": "1_樣本量", "status": "pass", "detail": { "trades": 118, "per_param_ratio": 39 } },
    { "dim": "2b_好運依賴", "status": "warn", "detail": { "top10_removed_net_r": 9.8 } }
    // … 8+擴充維度逐項；P2 維度出 "status": "not_available_p2"
  ],
  "trades_ref": "trades/run-2026-0042.json",   // 逐筆連 record tags — sidecar 檔
  "equity_curve_ref": "equity/run-2026-0042.json",
  "events_ref": "events/run-2026-0042.json",   // 事件日誌 sidecar（2026-07-24 採納）——敘事面板/圖表檢視器數據源
  "warnings": [ "same_bar_ambiguous ×3（保守 stop-first 處理）" ],
  "owner_action": null                          // 晉升決定寫返入嚟（A7 快照）
}
```

**設計要點**：主檔細（AI 一啖讀晒攞到全貌）；逐筆交易同權益曲線做 **sidecar** 檔用 ref 指過去——AI 需要先逐層深挖，唔會一開波就迫佢吞幾千行。

### 2.1 結果頁匯出包（2026-07-26 Owner 拍板）

結果頁每次只匯出一個 run，格式如下：

```text
result-<run_id>.zip
├─ result.json
├─ trades/<run_id>.json
├─ equity/<run_id>.json
└─ events/<run_id>.json
```

- `result.json` 必須符合上面 `result.v1`，而且三個 `*_ref` 必須指向 zip 內實際存在嘅 sidecar；**唔准寫本機絕對路徑**。
- 匯出只係打包既有不可變 run artifact；**唔准為匯出重新跑、重算或改寫結果**。
- 0 成交一樣要出完整合法包；`events` sidecar 要帶被截原因同最接近成交三次，令 Terminal Agent 可以追查。
- UI 位置、Dialog、Terminal 開場白同錯誤處理見 `docs/ui/designs/p5-results.html` 約束 #16。

**校準 provenance（WO-003）：**`run.manifest.calibration` 係 optional immutable `calibration.v1` object。當 Trend Daily percentile gate 使用 run 前 canonical history 時，必須記低 `source=pre_run_canonical_bars`、half-open warm-up window、該 history 的 `canonical-bars.v1` SHA-256 fingerprint、完整／ready Daily bar counts、calibration sample size、status（`calibrated`／`unavailable`）及已選 thresholds。warm-up 只可供 compiled indicators／percentile calibration；唔入 run range、strategy replay 或 PnL。

### 2.2 模擬盤偏離快照 `paper-review.v1`（zero-runtime historical profile）

> **2026-07-31 runtime修訂：**本節v1保持byte-compatible，供provision-only／
> zero-runtime historical artifacts讀取，唔代表真runtime contract。完整P6會新增
> `paper-review.v2`，包含market provenance、decisions、orders／fills／trades、
> positions／PnL、runtime／safety events及timeframe identities；現行設計authority係
> `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md` §13。
> W實作v2後先將exact JSON／ZIP schema同步到本文，禁止提前猜欄位。

用途：把單一模擬交易員截至同一個邏輯時間點嘅偏離證據，連同建立交易員時鎖定嘅完整 `result.v1` baseline，一包交畀 Terminal。匯出係只讀分析交接，**唔會平倉、暫停、永久停止、重新開始或改寫交易員**。

主檔 `paper-review.json`：

```jsonc
{
  "schema": "paper-review.v1",
  "snapshot": {
    "snapshot_id": "paper-review-0008",
    "request_id": "export-request-01J4...",
    "captured_at": "2026-07-26T07:42:00Z",
    "high_water_marks": {
      "trades": 7,
      "equity": 1842,
      "events": 392,
      "expected_decisions": 8
    }
  },
  "trader": {
    "trader_id": "trader-0002",
    "status": "running",
    "created_at": "2026-07-20T01:30:00Z",
    "last_status_change_at": "2026-07-20T01:30:00Z"
  },
  "strategy": {
    "strategy_version_id": "strategy-0003",
    "name": "回踩 18EMA",
    "content_sha256": "..."
  },
  "contract": {
    "contract_id": "NQ-202609-CME",
    "exchange": "CME",
    "timezone": "America/Chicago"
  },
  "baseline": {
    "run_id": "run-2026-0042",
    "result_sha256": "...",
    "result_ref": "baseline/result.json"
  },
  "account_origin": {
    "account_id": "paper-account-0002",
    "currency": "USD",
    "initial_capital": 100000,
    "independent_account": true
  },
  "as_of_state": {
    "cash": 101035,
    "equity": 101180,
    "realized_pnl": 1035,
    "unrealized_pnl": 145,
    "open_positions": [
      {
        "contract_id": "NQ-202609-CME",
        "side": "long",
        "quantity": 1,
        "entry_price": 18824.25,
        "mark_price": 18831.50,
        "marked_at": "2026-07-26T07:42:00Z",
        "unrealized_pnl": 145,
        "protective_orders": { "stop": 18792.25, "target": 18872.25 }
      }
    ],
    "safety_status": { "state": "normal", "drawdown_r": -1.4, "loss_streak": 2 }
  },
  "summary": {
    "expected_trades": 8,
    "actual_trades": 7,
    "matched": 7,
    "missed": 1,
    "extra": 0,
    "average_slippage_points": 0.8,
    "expectancy_delta_r": -0.06
  },
  "interpretation": {
    "owner_view": "少咗一筆係數據流斷咗 90 秒；其餘差異屬執行環境。",
    "categories": ["data", "execution"],
    "supporting_event_refs": [
      { "path": "paper/events.json", "event_id": "event-0391" }
    ]
  },
  "refs": {
    "paper_trades": "paper/trades.json",
    "paper_equity": "paper/equity.json",
    "paper_events": "paper/events.json",
    "expected_actual": "divergence/expected-actual.json"
  },
  "members": [
    { "path": "paper/trades.json", "bytes": 12345, "sha256": "..." },
    { "path": "paper/equity.json", "bytes": 45678, "sha256": "..." },
    { "path": "paper/events.json", "bytes": 23456, "sha256": "..." },
    { "path": "divergence/expected-actual.json", "bytes": 9876, "sha256": "..." },
    { "path": "baseline/result.json", "bytes": 3456, "sha256": "..." }
    // baseline/result.json 引用嘅每個 sidecar 亦要逐一列入
  ]
}
```

匯出包：

```text
paper-review-<trader_id>-<captured_at_utc_compact>.zip
├─ paper-review.json
├─ paper/
│  ├─ trades.json
│  ├─ equity.json
│  └─ events.json
├─ divergence/
│  └─ expected-actual.json
└─ baseline/
   ├─ result.json
   ├─ trades/<run_id>.json
   ├─ equity/<run_id>.json
   └─ events/<run_id>.json
```

不可妥協規則：

1. 一個包只屬於一個不可變 snapshot、一個 trader、一個鎖定 baseline。
2. 後端先原子鎖定 `captured_at` 同四組 high-water marks；所有 sidecar 只收 cutoff 之前記錄，**唔准每份檔截到唔同時間**。
3. `baseline/` 必須係建立 trader 時鎖定嗰個完整 `result.v1` 包，唔准改用最新 run 或另一個 run。
4. 所有 refs 用 zip 內相對路徑，唔准本機絕對路徑或走出 zip；`members` 要覆蓋每個必要成員並核對 bytes ＋ SHA-256。
5. 只准打包已保存證據，唔准重播、補算、重算、改寫或生成替代 sidecar。
6. 有未平倉持倉要喺 `as_of_state.open_positions` 照實記錄，唔平倉、唔虛構離場；`paper/trades.json` 只列真正成交。
7. 零模擬成交係合法包：`paper/trades.json` 用空清單；偏離檔仍列 baseline 預期、未成交原因及最接近觸發三次。
8. 同一 `request_id` 重試只返回原 snapshot；Owner 明確再次匯出先建立新 request／snapshot。ready snapshot 不可更新或刪除。
9. 任一必要檔案缺失、引用不可解、雜湊不一致或無法取得一致 cutoff，整包 fail-closed，**唔准部分 zip**。
10. `divergence/expected-actual.json` 每項至少有 baseline expected ref、paper actual ref（可為 `null`）、`matched|missed|extra|timing|price|quantity|exit|cost` 分類、數值差異、`market|data|execution|strategy-understanding|unknown` 解讀分類、支持事件 refs 同 Owner 人話摘要。
11. 所有 JSON 用 UTF-8；機器時間用 UTC ISO 8601，UI 另顯示 Owner 本地時間及時區。
12. v1 UI位置及下載行為只作historical compatibility；runtime v2位置、lifecycle、
    Terminal opener及錯誤處理以2026-07-31 P6 runtime設計§§13–14為準。舊P6視覺稿
    及divergence spec已從現行工作樹刪除，唔再係active authority。

---

## 3. 盤前計劃 `premarket.v1`（YAML）

> **⛔ 非 MVP 實作依據（Owner 2026-07-26 較新決定）：**MVP 不設 A3、獨立設定頁或市場狀態輸入。以下 schema 只保留作歷史研究草案；MVP 前後端不得要求、產生或消費 `premarket.v1`。MVP 完成後如要重啟，必須重新同 Owner 設計及拍板，唔可以直接照本節實作。

```yaml
schema: premarket.v1
date: 2026-07-24
applies_to: [NQ]

bias:
  direction: long        # long | short | none（none=今日唔做，spec §10 單方向）
  reason: 連跌五日到達日線主要支持位，值博率高
  confidence: medium     # low | medium | high

levels:
  - { price: 23150, kind: support,    source_tf: D,  strength: strong, note: 三月起嘅區間底 }
  - { price: 23420, kind: resistance, source_tf: 4H, strength: normal }

volatility_view:         # Owner 肉眼判斷（2026-07-24 拍板加入）
  expected: high         # high | normal | low
  reason: 過去三日 daily range 明顯擴張    # 可選

no_trade: false          # true 就成日唔開單（凌駕一切）
notes: |                 # 自由文字（唔入邏輯，顯示用）
  CPI 8:30am ET 公佈，頭半個鐘可能亂
```

**波動判斷三層設計**：① `volatility_view` = Owner 主觀輸入（純記錄，P1 唔入邏輯）；② 系統對照指標（自動計、總覽頁顯示）：daily ATR(14) 自身 250 日百分位 + range 比率（近 3 日平均 TR ÷ 近 20 日平均，>1.2 擴張 / <0.8 收縮）；③ record layer 每單 tag 埋 Owner 判斷 + 機械數 + 當日實際 range——儲夠數據三者對照，證實 Owner 日線判斷有冇超越機械指標嘅價值，先決定升唔升級做注碼因子（spec §11.6 同款哲學）。

**歷史用途構想（非 MVP）**：原本構想係由 `bias.direction` 做 daily plan 單方向 override、總覽顯示同 record layer tag，再由策略積木引用 `levels`。呢套用途已被 2026-07-26 較新決定押後，MVP 全部唔做。

---

## 3.5 草圖包 `sketch.v1`（2026-07-24 加入；**v1.2 修訂 2026-07-27**）

```
sketch-20260725-01/               ← 一律 sketch- 前綴（兩個 kind 都係），NN = 當日流水號
  INSTRUCTIONS.md
  meta.yaml
  chart-D.png · chart-1H.png · chart-30m.png · chart-5m.png
```

ZIP／下載包內部 root 保持 `<sketch-id>/`。匯入 repo 後嘅 canonical 持久化路徑係 `data/sketches/<origin>/<sketch-id>/`；`origin` 必須由已驗證 `meta.yaml` 取得，唔信 client 另傳值。同一 id、不同 origin 可共存；同一 pair 先算撞檔。

```yaml
# meta.yaml
schema: sketch.v1
sketch_id: sketch-20260725-01     # 一律 sketch- 前綴；唯一性 = origin + sketch_id
kind: strategy                    # strategy | insight
origin: workshop                  # workshop | journal-app（產生系統，純記錄）
chart_source: rendered            # rendered（有機讀 drawings）| screenshot（注釋焗喺圖入面）
instructions_template: instructions.v1
instrument: NQ
asset_class: equity_index_futures  # 由 canonical catalog 帶入，同 instrument exact match
created: 2026-07-25               # 必填——包嘅時間錨點
title: 大框架武裝細框架回踩         # 必填——人類識別
rationale: |                      # kind: strategy 必填；kind: insight 可省略
  大框架趨勢中嘅細框架回調係機構分批建倉行為……
charts:
  - file: chart-D.png
    timeframe: D
    role: bias                    # 可選：bias | mid | auxiliary | entry
    range: [2026-05-01, 2026-07-25]          # 可選（rendered 應自動填；screenshot 隨 Owner）
    indicators_shown: [ema18, ema50, ema90]  # canonical token，見下表
    indicators_other: [我畫嘅趨勢線]          # 可選：自由文字，唔入 canonical enum
    drawings: [ { type: hline, price: 23150, label: 區間底 } ]   # 可選（screenshot 可省略）
    owner_view: |                 # 必填——整個格式最核心嘅欄位
      日線喺三月起嘅大區間內，而家接近底部，做空阻力大……
  - file: chart-1H.png
    timeframe: 1H
    role: mid
    indicators_shown: [ema18, ema90]
    owner_view: |
      1H 啱啱黃金交叉，等第一次回踩掂 90 EMA……
  # 30m（auxiliary）／5m（entry）同款，最少四張
```

### 3.5.0 欄位規則（v1.1 定案）

| 欄位 | 必填？ | 規則 |
|---|---|---|
| `sketch_id` | ✅ | **一律 `sketch-YYYYMMDD-NN`**——兩個 `kind` 都用 `sketch-` 前綴（`kind` 只喺欄位區分）。`sketch` 指「圖文載體」，唔係內容類型；`based_on_sketch` 呢個溯源欄名亦假設咗呢個前綴 |
| `origin` + `sketch_id` | — | **唯一性 = 兩者組合**。兩個系統會各自產生 `sketch-20260725-01`，靠 `origin` 分辨。`insight_id` 同理（見 §3.5.1） |
| `instrument` / `asset_class` | ✅ | **v1.2（2026-07-27）**：一個包一個 primary root symbol；Owner 選 instrument，class 由 canonical catalog 帶入並重新驗證。唔准由標題／圖片推斷。首張圖附加後兩欄鎖定；匯出後整包不可變 |
| `created` / `title` | ✅ | v1.1 正式加入（v1.0 example 遺漏，已補） |
| `rationale` | `strategy` ✅ ／ `insight` 可省 | 唔好寫 `rationale: null`，直接省略。`insight` 嘅假設寫喺 `insight.v1` 嘅 `measurement.hypothesis` |
| `charts[].role` | 可選 | `bias`／`mid`／`auxiliary`／`entry`——表達「呢張圖喺分析層級擔任咩角色」。**值可重複**（例如兩格 `auxiliary`）；`bias`／`entry` **建議**各一格但唔硬性限制，驗證器最多出 warning |
| `charts[].range` | 可選 | `rendered` 應自動填（系統知道）；`screenshot` 由 Owner 自願填。**時間錨點嘅必要保證係頂層 `created`**，唔係逐格 range |
| `charts[].indicators_shown` | ✅（空就寫 `[]`） | **v1.1 補充 2026-07-25（答 [W-007]）**：v1.0 表遺漏咗呢欄但 example 每張圖都有——已補。**必須永遠輸出**：`[]` 表示「Owner 明確冇剔任何指標」，**同「完全缺席」（未知／未填）對消費 AI 嚟講語義唔同**。 |
| `charts[].indicators_other` | 可選（空就省略） | 同上一欄相反——佢係「額外補充」，冇就係冇，唔需要明確聲明「冇額外」 |
| `charts[].owner_view` | ✅ | **最核心欄位**——缺一格都應該擋住匯出 |
| `charts[].drawings` | 可選 | `screenshot` 可省略（座標不可機讀唔算錯） |
| `instructions_template` | ✅ | 值 = `instructions.v1`；同時要寫喺 `INSTRUCTIONS.md` 檔頭（見 §3.5.2） |

**可選欄一律「省略」而唔係「寫 null」（v1.1 貫穿規則）**：`role`／`range`／`indicators_other`／`drawings`／`rationale`（insight 時）——冇值就**唔要嗰個 key**。原因同上面 `indicators_shown` 一樣：**缺席 ≠ null ≠ 空值**，三者對消費 AI 係三個唔同訊號。Golden fixture 要有獨立測試釘死呢點。

**Golden fixture 兩條硬要求（v1.1，2026-07-25）**：
1. **斷言用語義等價，唔准逐字**（見下段）；
2. **fixture 本身必須人手照規格寫，唔准由 emitter 生成**——由 emitter 生成嘅 fixture 只會證明「emitter 同自己一致」，**證明唔到「emitter 跟規格」**。（來源：`C-trading journal` [W-007]，Agent C 採納為兩邊通用規則。）

**Golden fixture 斷言方式（v1.1 補充 2026-07-25，答 [W-004]）**：兩個獨立實作用**兩個唔同語言嘅 YAML 庫**，key 次序／block scalar 風格／引號／縮排必然唔同但全部係**非語義**差異。所以——**一律用語義等價（semantic round-trip），唔准逐字（byte-for-byte）比對**：`parse(emit(model))` 深度相等於 `parse(canonical_fixture_text)`；比較前正常化：① 忽略 key 次序；② 多行 scalar strip 尾隨空白；③ **缺席欄位同 `null` 視為不等**（可選欄要省略而唔係寫 null）。`meta.yaml` key 次序仍建議跟 §3.5 example 原文，但**唔做斷言**。

**`indicators_shown` canonical token 規則**（v1.1 定案）：小楷 `<type><period>`——`ema18`／`ema50`／`ema90`／`sma20`／`atr14`／`rsi14`；冇週期嘅用純名——`vwap`／`volume`／`macd`；多參數用底線——`bb20_2`（週期_標準差）。**認唔到嘅一律放 `indicators_other` 自由文字陣列**，唔好硬塞入 `indicators_shown`——咁 AI 有穩定 token 可以認，Owner 又唔會被鎖死。

**同一格式、兩個獨立實作（2026-07-25 Owner 澄清）**：呢個圖文包格式會喺**兩個互相獨立嘅系統**各自實作——① 本系統嘅 **P1.5 工房**；② Owner 嘅**交易日記 app**（由另一個 agent 負責，規格見 `docs/07`）。

**兩者係獨立功能，唔係一條 pipeline**：各自出包、各自匯入返自己系統、各自服務自己嘅用途。**唯一嘅關聯係「產出格式一模一樣」**——所以 Owner 隨時可以將任何一邊得出嘅量化結果（strategy.yaml／insight.v1）人手複製去另一邊使用，唔需要任何系統整合。

`origin` 純粹記錄邊個系統產生（唔影響消費邏輯）；`chart_source` 聲明圖表係渲染（有機讀 `drawings` 座標）定係截圖（注釋焗喺圖入面，`drawings` 可省略）。驗證器對兩種都要接受。

用法：Owner 喺策略工作台分頁 ① 選 primary instrument → catalog 帶入 asset class → 首圖後鎖定 → 匯出；terminal AI 讀 meta.yaml＋PNG → 輸出 self-contained strategy.yaml（複合溯源＋v1.4 universe）→ 分頁 ② 左草圖右參數對照，Owner 只接受／退回整份策略。**strategy.v1 嘅 `meta` 段兩個 sketch lineage 欄由 v1.3 起成對必填**（見 §1.1a）；`based_on_insights: [insight-007@v2, …]` 仍然可選。

### 3.5.1 市場洞察支線（Owner 拍板 2026-07-24；工房第二個 tab，機器 100% 共用）

- **出**：同款圖文包，`meta.yaml` 加 `kind: insight`（strategy 草圖係 `kind: strategy`）。
- **入**：AI 返回 **`insight.v1`**（量化洞察）：

```yaml
schema: insight.v1
insight_id: insight-007
origin: workshop                          # workshop | journal-app（v1.1 加入）
version: 2
based_on_sketch: sketch-20260725-02      # 溯源返你嘅圖文
based_on_sketch_origin: workshop         # 同上面 id 成對，已預填
instrument: NQ                           # 同所引用 sketch 一致
asset_class: equity_index_futures        # 同 catalog／sketch 一致
title: 開市頭30分鐘假突破多
condition:                                # 量化條件定義（將來可升級做具名積木）
  type: session_time_filter
  suggested_params: { skip_first_minutes: 30 }
measurement:                              # record layer 應該點 tag 嚟統計驗證
  tag: entry_within_open_30m
  hypothesis: 呢類入市嘅期望值顯著低過其他時段
validation_status: unverified             # unverified → recording → supported → rejected
```

- **Composite 草圖溯源**：`based_on_sketch` 同 `based_on_sketch_origin` 係一個不可拆開嘅 reference，兩欄成對必填；格式、enum、本機缺包仍可接受等規則全部跟 §1.1a。呢個係文件合約；Agent X 今次批 1 唔實作 insight repository／import API。
- **Instrument 語境**：`instrument` 同 `asset_class` 成對必填，同引用草圖及 canonical catalog exact match。一個 insight package 只對應一個 primary instrument；否則日後無法分辨同一句觀察係 NQ 定 GC。
- **兩條鐵律（保策略獨立性）**：① 洞察版本化不可變，改＝新版本；② 策略文件永遠自包含——洞察只喺 AI 撰寫策略嗰刻焗入做實參數，`based_on_insights` 純溯源，**執行層永不運行時查洞察庫**。
- 統計 `supported` 之後先可以升級做詞彙表具名積木（P2）。

**v1.1 補充（2026-07-25，答 [W-002] Q7／Q8）**：
- **`origin` 欄必填**——`insight_id` 唯一性 = `origin` + `insight_id`（兩個系統會各自編到 `insight-007`，唔加 origin 一 copy 就撞）。
- **匯入外部洞察時本地缺前置版本（例如只 copy 咗 v2）：照收**，`version` 保留原值，記錄標記外部來源（建議欄：`imported_from_origin`），UI 顯示「外部來源，本地缺 v1」。鐵律係「洞察不可變」，**唔係「本地要有完整版本鏈」**——拒收或重新編號都會斬斷跨系統 copy 呢個核心用例。

### 3.5.2 指令書 `INSTRUCTIONS.md`（Owner 拍板 2026-07-24；兩類圖文包必備成員）

每個圖文包匯出時 app **自動生成**一份指令書，令 Owner 成個包掉俾任何 terminal agent 就開得工。**必須自包含**（嵌入 schema 精要——收件 agent 可能冇 repo 訪問權，唔准引用外部文件）。六段式模板（`instructions.v1`，按 kind 套內容）：

1. **項目背景**：期貨算法交易研究平台；你係策略參數化／洞察量化 agent；Owner 以圖＋文字表達判斷。
2. **包內容導讀**：四張 TF 圖各屬邊層、Owner 逐圖文字判斷、meta.yaml 畫作座標解讀方法。
3. **你嘅任務**：貫穿理解 → 有唔明必須問，唔准靜靜哋估。
4. **輸出格式**（嵌入該 kind 嘅 schema 精要）：strategy → strategy.v1 YAML（積木詞彙表、rationale 必填、unquantified_notes 鐵閘、兩個 sketch lineage 欄已預填）；insight → insight.v1（量化條件＋建議參數＋量度方法，同樣預填 sketch id＋origin）。
5. **規則**：唔准發明詞彙表以外嘅 structure；每個數值要 provenance；[課程原文] 參數唔准改。
6. **交付**：輸出交返 Owner 放入系統；驗證錯誤會原句貼返俾你修。

模板由 app 維護、有版本號；schema 升版時模板同步更新——「schema 說明書兼任 AI 指令書」原則（D2）嘅正式落地。

**版本號寫喺兩處（v1.1 定案，答 [W-002] Q9）**：① `meta.yaml` 嘅 `instructions_template: instructions.v1`（機器讀）；② `INSTRUCTIONS.md` **檔頭第一行** HTML 註釋 `<!-- instructions.v1 -->`（AI 讀個檔案時即刻見到）。兩處必須一致——驗證器可以攞嚟交叉檢查。

**第六段「交付」嘅精確措辭（v1.1 定案 2026-07-25，答 [W-010]）**——三件事缺一不可：

1. **主路徑：寫檔，而且指定位置同檔名**——AI 要將輸出**寫入同一個包資料夾**，檔名固定 `strategy.yaml`（`kind: strategy`）或 `insight.yaml`（`kind: insight`）。包資料夾本身已經帶 id（`sketch-YYYYMMDD-NN/`），所以入面用固定檔名唔會撞，而 Owner 返嚟匯入時有個「一眼就係佢」嘅目標檔。
2. **後路：同時將全文打印喺 terminal**——有啲 agent 冇寫檔權限。**如果寫唔到檔，要明確講出嚟**（唔准靜靜哋只打印就當完成）。兩邊 app 嘅匯入都必須支援「貼文字」。
3. **時序：只有喺澄清完成之後先寫檔**——指令書第三段要求「有唔明必須問」，所以 AI 唔應該一 load 完就即刻出檔。要喺模板明寫：**問完、Owner 答完，先至寫**。

**建議（唔係硬性，但兩邊都應該做）**：匯入時**原封保留 YAML 原文**（例如存做 `source_text`）。理由：跨系統 copy、或者將來 schema 升版要重新解析時，由已 parse 嘅 object 重新生成一定走樣。（來源：`C-trading journal` 喺 [W-010] 嘅設計，Agent C 採納。）

## 4. 決策點（Owner 2026-07-24 全部拍板）

1. 格式分工（策略=YAML，結果=JSON；盤前 YAML 只保留 Post-MVP 草案）✅
2. Provenance 路徑註解（值只寫一次）✅
3. 結果文件 sidecar（主檔細、逐筆分檔 ref）✅
4. `bias.confidence` 原設計係純記錄唔入邏輯；**MVP 不消費整份盤前文件** ✅
5. `volatility_view` 保留喺 Post-MVP 歷史草案；**MVP 不實作** ✅
6. 模擬盤偏離採自包含不可變snapshot：舊zero-runtime用`paper-review.v1`；
   真runtime用`paper-review.v2`，兩者都帶鎖定完整`result.v1` baseline ✅
