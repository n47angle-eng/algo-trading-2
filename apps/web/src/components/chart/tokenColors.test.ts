import { describe, expect, it } from "vitest";

import {
  isClassicChartColor,
  normalizeCssColorForChart,
} from "./tokenColors";

describe("D17 chart color normalization", () => {
  it("converts modern slash-alpha rgb to classic rgba", () => {
    expect(normalizeCssColorForChart("rgb(255 255 255 / 5.2%)")).toBe(
      "rgba(255, 255, 255, 0.052)",
    );
    expect(normalizeCssColorForChart("rgb(10 132 255 / 0.08)")).toBe(
      "rgba(10, 132, 255, 0.08)",
    );
    expect(normalizeCssColorForChart("rgb(48 209 88)")).toBe(
      "rgb(48, 209, 88)",
    );
  });

  it("passes hex and classic forms", () => {
    expect(normalizeCssColorForChart("#0a84ff")).toBe("#0a84ff");
    expect(normalizeCssColorForChart("rgba(1, 2, 3, 0.5)")).toBe(
      "rgba(1, 2, 3, 0.5)",
    );
  });

  it("isClassicChartColor rejects modern slash form", () => {
    expect(isClassicChartColor("rgb(255 255 255 / 5%)")).toBe(false);
    expect(isClassicChartColor("rgba(255, 255, 255, 0.05)")).toBe(true);
    expect(isClassicChartColor("#fff")).toBe(true);
  });

  it("would fail if normalization were identity for modern colors", () => {
    const modern = "rgb(255 255 255 / 5.2%)";
    const identity = (s: string) => s;
    expect(isClassicChartColor(identity(modern))).toBe(false);
    expect(isClassicChartColor(normalizeCssColorForChart(modern))).toBe(true);
  });
});
