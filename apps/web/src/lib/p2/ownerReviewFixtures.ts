/**
 * Isolated owner-review fixtures for P2 instrument closed loop.
 * Never written to localStorage; never call backend.
 */

import { OWNER_REVIEW_CATALOG } from "../catalog/fixtureCatalog";
import type { SketchDraft } from "../sketch/types";

/** Stable URL for Owner hand-test. */
export const OWNER_REVIEW_P2_URL =
  "/strategies?scenario=owner-review&tab=sketch";

export function fixtureValidNqYmStrategy(sketchId: string): string {
  return `schema: strategy.v1

meta:
  name: NQ趨勢日回踩90EMA_v1
  created: 2026-07-27
  based_on_sketch: ${sketchId}
  based_on_sketch_origin: workshop
  based_on: null

rationale: |
  大框架趨勢中嘅細框架回調；同 class 擴展 YM 待回測。

unquantified_notes: []

universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts:
    - NQ
    - YM
  expansion_rationale:
    YM: 同屬股指趨勢結構；價差以 ticks／R 正規化，仍待回測
  session: eth

timeframes:
  trio: { bias: D, mid: 1H, entry: 5m }
  entry_layers: 3

indicators:
  - { id: ema_fast, type: EMA, period: 18 }
  - { id: ema_slow, type: EMA, period: 90 }

structures: []
regime:
  require_trend: true
direction:
  mode: trend_following
  layer_consistency: hard
entry:
  sequence: []
  trigger: { type: breakout, of: signal_bar.entry_ref, mode: intrabar }
invalidations: [day_end_clear]
risk:
  stop: { anchor: signal_bar.stop_ref, offset_ticks: 1 }
  target: { type: r_multiple, value: 1 }
  sizing: { type: fixed_fractional, risk_pct: 1.0 }
  daily_loss_limit_r: 3
provenance: []
`;
}

export function fixtureInvalidNqGcStrategy(sketchId: string): string {
  return `schema: strategy.v1

meta:
  name: NQ_cross_class_invalid
  created: 2026-07-27
  based_on_sketch: ${sketchId}
  based_on_sketch_origin: workshop

rationale: |
  故意跨 asset class 測試 fail-closed。

unquantified_notes: []

universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts:
    - NQ
    - GC
  expansion_rationale:
    GC: 不應通過——商品 vs 股指
  session: eth

timeframes:
  trio: { bias: D, mid: 1H, entry: 5m }
  entry_layers: 3
indicators: []
structures: []
regime: { require_trend: true }
direction: { mode: trend_following, layer_consistency: hard }
entry: { sequence: [], trigger: { type: breakout, of: x, mode: intrabar } }
invalidations: [day_end_clear]
risk:
  stop: { anchor: x, offset_ticks: 1 }
  target: { type: r_multiple, value: 1 }
  sizing: { type: fixed_fractional, risk_pct: 1.0 }
  daily_loss_limit_r: 3
provenance: []
`;
}

export function fixturePrimaryOnlyStrategy(sketchId: string): string {
  return `schema: strategy.v1

meta:
  name: NQ_primary_only
  created: 2026-07-27
  based_on_sketch: ${sketchId}
  based_on_sketch_origin: workshop

rationale: primary only

unquantified_notes: []

universe:
  primary_instrument: NQ
  asset_class: equity_index_futures
  contracts:
    - NQ
  expansion_rationale: {}
  session: eth

timeframes:
  trio: { bias: D, mid: 1H, entry: 5m }
  entry_layers: 3
indicators: []
structures: []
regime: { require_trend: true }
direction: { mode: trend_following, layer_consistency: hard }
entry: { sequence: [], trigger: { type: breakout, of: x, mode: intrabar } }
invalidations: [day_end_clear]
risk:
  stop: { anchor: x, offset_ticks: 1 }
  target: { type: r_multiple, value: 1 }
  sizing: { type: fixed_fractional, risk_pct: 1.0 }
  daily_loss_limit_r: 3
provenance: []
`;
}

/** Seed draft shape for owner-review memory — caller must not persist to real LS. */
export function fixtureNqDraft(partial?: Partial<SketchDraft>): SketchDraft {
  const now = new Date().toISOString();
  return {
    schema: "sketch.v1",
    sketchId: partial?.sketchId ?? "sketch-20260727-01",
    kind: "strategy",
    origin: "workshop",
    chartSource: "screenshot",
    instructionsTemplate: "instructions.v1",
    title: partial?.title ?? "NQ 趨勢日回踩 90EMA",
    rationale: partial?.rationale ?? "測試理據",
    charts: partial?.charts ?? [
      {
        slotId: "slot-1",
        timeframe: "D",
        role: "bias",
        indicatorsShown: ["ema18", "ema90"],
        ownerView: "判斷 1",
        imageDataUrl: null,
        imageFileName: null,
      },
      {
        slotId: "slot-2",
        timeframe: "1H",
        role: "mid",
        indicatorsShown: ["ema18", "ema90"],
        ownerView: "判斷 2",
        imageDataUrl: null,
        imageFileName: null,
      },
      {
        slotId: "slot-3",
        timeframe: "30m",
        role: "auxiliary",
        indicatorsShown: ["ema18"],
        ownerView: "判斷 3",
        imageDataUrl: null,
        imageFileName: null,
      },
      {
        slotId: "slot-4",
        timeframe: "5m",
        role: "entry",
        indicatorsShown: ["ema18", "ema90"],
        ownerView: "判斷 4",
        imageDataUrl: null,
        imageFileName: null,
      },
    ],
    instrument: partial?.instrument ?? "NQ",
    assetClass: partial?.assetClass ?? "equity_index_futures",
    instrumentLocked: partial?.instrumentLocked ?? false,
    instrumentLegacy: partial?.instrumentLegacy ?? false,
    created: partial?.created ?? now,
    updatedAt: partial?.updatedAt ?? now,
    exported: partial?.exported ?? false,
    exportedAt: partial?.exportedAt ?? null,
  };
}

export { OWNER_REVIEW_CATALOG };
