# strategy.v1 schema predicate → FSM internal state（映射對照表）

日期：2026-07-24 | 最後同步：2026-07-27 | WO-005 / 5-1 | 來源：[030] 裁決、`docs/05` §1.1、`TrendStrategy` 實作
用途：parser 將 YAML 謂詞譯成引擎狀態時嘅**唯一明文對照**——避免 schema 名同 event 名混淆。

## 1. 結構積木（`structures[].type`）

| strategy.v1 `type` | 引擎模組 | P1？ | 備註 |
|---|---|---|---|
| `pullback_lifecycle` | `PullbackLifecycle`（mid + entry 各一） | ✅ | `layer: mid` → `_mid_lifecycle`；`layer: entry` → `_entry_lifecycle` |
| `signal_bar` | Inside / Magic 偵測（entry close） | ✅ | `variant: inside \| magic` → `SignalKind` |
| `lmr` / 其他 | — | ❌ P2 | 驗證器**明確拒絕**（唔准靜靜跳過） |

## 2. Pullback 謂詞（[030] 批准）

| schema 謂詞 | 內部 gate 含義 | FSM 狀態集合 | 事件名（勿混淆） |
|---|---|---|---|
| `pb_mid.completed` | 中層 first-pullback **已 touch 且仍合資格**（未 exhaust / invalidate） | `awaiting_signal`（qualified） | **唔等於** `pullback_completed` event |
| `pb_entry.completed` | 入市層 first-pullback **已 touch 且仍合資格** | `awaiting_signal` | 同上 |
| （terminal）entry 真正建立 | pullback 生命週期完結 | → `completed` | `PULLBACK_COMPLETED` **只喺** `EntryIntent` 建立時記錄 |

**鐵律（[030]）：** schema 名 `.completed` = gate predicate「已 touch ∧ 未作廢」；literal event `pullback_completed` 保留到 entry 發生。v2 或改名 `pb_*.qualified`，v1 唔郁。

### Pullback 狀態機（TRADING_SPEC §12）

| 狀態 | 意義 | 觸發 |
|---|---|---|
| `idle` | 未 armed | 初始／日終 reset／作廢後 |
| `awaiting_touch` | 已有 EMA cross，等第一次 touch | `PULLBACK_ARMED`（cross） |
| `awaiting_signal` | 已 touch，等 signal bar | `PULLBACK_TOUCHED` ← **schema `.completed` 成立** |
| `completed` | 已用呢次 pullback 入市 | `PULLBACK_COMPLETED`（entry intent） |
| `exhausted` | 機會作廢（swing reclaim 等） | `PULLBACK_EXHAUSTED` |
| （invalidate） | 交叉反轉等 | `PULLBACK_INVALIDATED` → 回 `idle` |

`touch` 參數：`structures[].touch` → indicator id → `period` ∈ {18, 90} → `EntrySettings.pullback_ema_period`（P1 mid/entry 必須同一 period）。

## 3. 交叉謂詞

| schema 謂詞 | 引擎 | 備註 |
|---|---|---|
| `cross_state(ema_fast, ema_slow) == golden` | `_direction_from_snapshot` / mid+entry EMA18×90 | long 方向 |
| `cross_state(ema_fast, ema_slow) == death` | 同上 | short 方向 |
| （internal）cross arm pullback | `_cross_direction` on closed bar | arm `PullbackLifecycle` |

Indicator ids 必須喺 `indicators:` 聲明；P1 要求 EMA 18 + EMA 90（快／慢）。

## 4. Signal bar 謂詞

| schema 謂詞 | 引擎 | 事件 |
|---|---|---|
| `signal_bar in [sig_in, sig_mg]` | 已啟用 `SignalKind` 集合 | `SIGNAL_CREATED` / `SIGNAL_REJECTED` / `SIGNAL_CANCELLED` |
| `variant: inside` + `entry_ref`/`stop_ref` | mother bar high/low | Inside |
| `variant: magic` | Magic bar 規則 | Magic |

## 5. Regime / Direction 閘

| schema 欄 | 引擎 | 事件 |
|---|---|---|
| `regime.require_trend: true` | Daily `Regime == TREND` 先准評估 entry | `DAILY_REGIME_CHANGED` |
| `regime.congestion_no_trade: true` | `CONGESTION` 唔開單 | 同上 |
| `regime.sep_mult.value` | `RegimeSettings.separation_percentile` | 校準後 threshold |
| `regime.flat_mult.value` | `RegimeSettings.slope_percentile` | 校準後 threshold |
| `direction.mode: trend_following` | 只順勢 | — |
| `direction.layer_consistency: hard` | 1H 同 5m 方向必須一致 | `MID_DIRECTION_CHANGED` |

## 6. Trigger / Risk / Invalidations

| schema | 引擎 |
|---|---|
| `entry.trigger.type: breakout` + `mode: intrabar` | pending signal → 下支 bar intrabar breakout → `ENTRY_INTENT_CREATED` |
| `risk.stop.offset_ticks` | `RiskSettings.stop_offset_ticks`（P1 = 1） |
| `risk.target.type: r_multiple` / `value` | `RiskSettings.target_r_multiple` |
| `invalidations: cross_reversal_per_layer` | 每層 cross reversal 作廢 pullback / signal |
| `invalidations: day_end_clear` | `DAY_RESET`：清 pending、lifecycle |

## 7. Parser → `StrategySpec` 欄位映射（摘要）

| strategy.v1 路徑 | `StrategySpec` |
|---|---|
| `universe.primary_instrument` | execution binding primary root symbol；唔係 FSM gate |
| `universe.asset_class` | validation／audit metadata；唔係 FSM gate |
| `universe.contracts` | confirmed run-eligible root symbols；P4 每次揀子集 |
| `universe.expansion_rationale` | `StrategyDocument` audit only；唔入 FSM |
| `universe.session` | `universe_session` |
| `timeframes.trio` | `timeframes`（固定 D/1H/5m） |
| `timeframes.entry_layers` | `entry.entry_layers`（P1=3） |
| `regime.sep_mult.value` | `regime.separation_percentile` |
| `regime.flat_mult.value` | `regime.slope_percentile` |
| `direction.*` | `direction.*` |
| `structures` pullback touch period | `entry.pullback_ema_period` |
| `structures` signal_bar variants | `entry.signal_bars` |
| `risk.stop.offset_ticks` | `risk.stop_offset_ticks` |
| `risk.target.value` | `risk.target_r_multiple` |

`meta.based_on_sketch`／`meta.based_on_sketch_origin`／`based_on_insights`／`rationale`／`unquantified_notes`／`provenance` 同 `universe.expansion_rationale` 只存 `StrategyDocument`（溯源／審計／記分卡維度 8），**唔入**引擎 FSM。Primary/class/contracts/session 會約束 execution binding，但同樣唔改變策略狀態機語義。

## 8. 驗證層（docs/05 §1.2）

1. **格式** — YAML + pydantic `StrategyDocument`
2. **引用** — composite sketch identity；primary/contracts ∈ canonical catalog；indicator/structure id；sequence require 引用
3. **語義** — primary ∈ contracts；文件/class/catalog exact match；全 universe 同 class、支援 session、MVP 全 USD；expansion rationale keys 恰好完整；本機有 sketch 時 primary/class exact match；再驗 TF trio、參數範圍、P1 structure/indicator 詞彙（P2 明確拒絕）
4. **provenance** — 每個數值參數路徑一條

錯誤格式：`<路徑>: <乜嘢錯> — <點修>`
