import { describe, expect, it } from "vitest";

import {
  applyChartChange,
  createEmptyDraft,
  duplicateDraftAsNew,
  migrateDraftShape,
  selectInstrument,
} from "./store";
import type { SketchDraft } from "./types";

function withInstrument(d: SketchDraft): SketchDraft {
  return {
    ...d,
    instrument: "NQ",
    assetClass: "equity_index_futures",
    instrumentLocked: false,
  };
}

describe("instrument lock state machine", () => {
  it("allows instrument change before first image", () => {
    let d = withInstrument(createEmptyDraft([]));
    d = selectInstrument(d, "YM", "equity_index_futures");
    expect(d.instrument).toBe("YM");
    expect(d.instrumentLocked).toBe(false);
  });

  it("locks instrument on first image attach; delete does not unlock", () => {
    let d = withInstrument(createEmptyDraft([]));
    expect(d.instrumentLocked).toBe(false);
    const withImg = {
      ...d.charts[0],
      imageDataUrl: "data:image/png;base64,abc",
      imageFileName: "shot.png",
    };
    d = applyChartChange(d, withImg);
    expect(d.instrumentLocked).toBe(true);
    // Try to change instrument — blocked
    const blocked = selectInstrument(d, "GC", "commodity_futures");
    expect(blocked.instrument).toBe("NQ");
    // Remove image — still locked
    const cleared = applyChartChange(d, {
      ...withImg,
      imageDataUrl: null,
      imageFileName: null,
    });
    expect(cleared.instrumentLocked).toBe(true);
    expect(selectInstrument(cleared, "GC", "commodity_futures").instrument).toBe(
      "NQ",
    );
  });

  it("legacy unexported missing instrument: one-time select then lock if images", () => {
    let d = createEmptyDraft([]);
    d = {
      ...d,
      instrument: null,
      assetClass: null,
      instrumentLocked: false,
      charts: d.charts.map((c, i) =>
        i === 0
          ? {
              ...c,
              imageDataUrl: "data:image/png;base64,x",
              imageFileName: "a.png",
            }
          : c,
      ),
    };
    // migrate marks locked if has image even without instrument
    d = migrateDraftShape(d);
    expect(d.instrumentLocked).toBe(true);
    // selectInstrument still allows one-time when instrument empty despite locked flag
    // Work order: "容許 Owner一次補揀，隨即按現有圖片狀態鎖"
    // Our selectInstrument: if instrumentLocked && instrument → block; if no instrument, allow
    d = selectInstrument(d, "NQ", "equity_index_futures");
    expect(d.instrument).toBe("NQ");
    expect(d.instrumentLocked).toBe(true);
    // second change blocked
    expect(selectInstrument(d, "YM", "equity_index_futures").instrument).toBe(
      "NQ",
    );
  });

  it("exported draft cannot change instrument; duplicate clears images and unlocks", () => {
    let d = withInstrument(createEmptyDraft([]));
    d = {
      ...d,
      exported: true,
      exportedAt: "2026-07-27T00:00:00.000Z",
      instrumentLocked: true,
      charts: d.charts.map((c, i) =>
        i === 0
          ? {
              ...c,
              imageDataUrl: "data:image/png;base64,nqshot",
              imageFileName: "nq.png",
              ownerView: "NQ 判斷",
            }
          : c,
      ),
    };
    expect(selectInstrument(d, "YM", "equity_index_futures").instrument).toBe(
      "NQ",
    );
    const copy = duplicateDraftAsNew(d, [d.sketchId]);
    expect(copy.sketchId).not.toBe(d.sketchId);
    expect(copy.exported).toBe(false);
    expect(copy.instrumentLocked).toBe(false);
    expect(copy.instrument).toBe("NQ");
    // D13: no leftover NQ image/text after unlock
    expect(copy.charts.every((c) => !c.imageDataUrl && c.ownerView === "")).toBe(
      true,
    );
    const changed = selectInstrument(copy, "GC", "commodity_futures");
    expect(changed.instrument).toBe("GC");
    // still no images bound to GC
    expect(changed.charts.every((c) => !c.imageDataUrl)).toBe(true);
  });

  it("migrateDraftShape marks pre-instrument schema as instrumentLegacy", () => {
    const raw = {
      ...createEmptyDraft([]),
    } as Record<string, unknown>;
    delete raw.instrument;
    delete raw.assetClass;
    delete raw.instrumentLocked;
    delete raw.instrumentLegacy;
    const m = migrateDraftShape(raw as unknown as SketchDraft);
    expect(m.instrument).toBeNull();
    expect(m.assetClass).toBeNull();
    expect(m.instrumentLegacy).toBe(true);
    // fresh empty draft is NOT legacy
    expect(createEmptyDraft([]).instrumentLegacy).toBe(false);
  });
});
