import { describe, expect, it } from "vitest";

import {
  findContainingBarAnchor,
  findContainingBarIndex,
  formatHktLocal,
  hktLocalToUnixSec,
  TF_DURATION_SEC,
  unixSecToIso,
  verticalLabel,
} from "./timeIdentity";

describe("D28/D34/D35 time identity + containing bar", () => {
  it("HKT round-trip for trade #1 10:30", () => {
    const sec = hktLocalToUnixSec(2026, 5, 8, 10, 30);
    expect(formatHktLocal(sec)).toBe("2026-05-08 10:30");
    expect(unixSecToIso(sec)).toBe("2026-05-08T02:30:00.000Z");
  });

  it("vertical label includes #N and local datetime", () => {
    const sec = hktLocalToUnixSec(2026, 5, 8, 10, 30);
    expect(verticalLabel(1, sec)).toBe("#1 · 2026-05-08 10:30");
  });

  it("D trading-day: entry on May 8 maps to May 8 bar not May 9", () => {
    const may8 = Date.UTC(2026, 4, 8) / 1000;
    const may9 = Date.UTC(2026, 4, 9) / 1000;
    const times = [may8 - 86400, may8, may9, may9 + 86400];
    const entry = hktLocalToUnixSec(2026, 5, 8, 10, 30);
    const a = findContainingBarAnchor(times, entry, TF_DURATION_SEC.D);
    expect(a).not.toBeNull();
    expect(a!.barTime).toBe(may8);
    expect(a!.barIndex).toBe(1);
    const wrong = times.findIndex((t) => t >= entry);
    expect(times[wrong]).toBe(may9);
    expect(a!.barIndex).not.toBe(wrong);
  });

  it("after-last is OOR (not sticky last bar)", () => {
    const t0 = 1_000_000;
    const times = [t0, t0 + 300, t0 + 600];
    const after = t0 + 600 + 300; // exactly at end of last bar → OOR [start, end)
    expect(
      findContainingBarAnchor(times, after, TF_DURATION_SEC["5m"]),
    ).toBeNull();
    const deepFuture = t0 + 600 + 10_000;
    expect(
      findContainingBarAnchor(times, deepFuture, TF_DURATION_SEC["5m"]),
    ).toBeNull();
  });

  it("before-first is OOR", () => {
    const times = [1000, 1300, 1600];
    expect(
      findContainingBarAnchor(times, 999, TF_DURATION_SEC["5m"]),
    ).toBeNull();
  });

  it("gap between bars is OOR", () => {
    const times = [0, 10_000]; // big gap; duration 300
    expect(
      findContainingBarAnchor(times, 5000, TF_DURATION_SEC["5m"]),
    ).toBeNull();
  });

  it("mutation: first >= jumps D to next day", () => {
    const may8 = Date.UTC(2026, 4, 8) / 1000;
    const may9 = Date.UTC(2026, 4, 9) / 1000;
    const times = [may8, may9];
    const entry = hktLocalToUnixSec(2026, 5, 8, 10, 30);
    expect(findContainingBarIndex(times, entry, TF_DURATION_SEC.D)).toBe(0);
    const wrong = times.findIndex((t) => t >= entry);
    expect(wrong).toBe(1);
  });

  it("1H / 30m / 5m containing bars for trade #1", () => {
    const entry = hktLocalToUnixSec(2026, 5, 8, 10, 30); // 02:30Z
    const hStart = Math.floor(entry / 3600) * 3600;
    const m30Start = Math.floor(entry / 1800) * 1800;
    const m5Start = Math.floor(entry / 300) * 300;
    const hours = Array.from({ length: 10 }, (_, i) => hStart - 5 * 3600 + i * 3600);
    const m30s = Array.from({ length: 20 }, (_, i) => m30Start - 10 * 1800 + i * 1800);
    const m5s = Array.from({ length: 40 }, (_, i) => m5Start - 20 * 300 + i * 300);
    expect(
      findContainingBarAnchor(hours, entry, TF_DURATION_SEC["1H"])!.barTime,
    ).toBe(hStart);
    expect(
      findContainingBarAnchor(m30s, entry, TF_DURATION_SEC["30m"])!.barTime,
    ).toBe(m30Start);
    expect(
      findContainingBarAnchor(m5s, entry, TF_DURATION_SEC["5m"])!.barTime,
    ).toBe(m5Start);
  });
});
