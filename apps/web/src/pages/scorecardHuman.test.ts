import { describe, expect, it } from "vitest";

import {
  humanDetail,
  scorecardLabel,
  scorecardStatusLabel,
} from "../lib/results/scorecardHuman";

describe("D31 live scorecard human fail-closed", () => {
  it("known dimension and status", () => {
    expect(scorecardLabel("profit_factor")).toBe("獲利因子");
    expect(scorecardStatusLabel("pass")).toBe("通過");
    expect(scorecardStatusLabel("warn")).toBe("警告");
  });

  it("unknown dimension does not surface raw snake_case", () => {
    expect(scorecardLabel("weird_backend_dim")).toBe("記分項目名稱暫未支援");
    expect(scorecardLabel("weird_backend_dim")).not.toMatch(/weird/);
  });

  it("unknown status does not surface raw token", () => {
    expect(scorecardStatusLabel("QUANTILE_FAIL")).toBe("狀態暫未支援");
    expect(scorecardStatusLabel("QUANTILE_FAIL")).not.toMatch(/QUANTILE/);
  });

  it("unknown detail key and nested object fail-closed", () => {
    const text = humanDetail({
      text: "已知說明",
      obscure_metric: 12,
      nested: { a: 1 },
    });
    expect(text).toMatch(/已知說明/);
    expect(text).toMatch(/詳細欄位暫未支援/);
    expect(text).not.toMatch(/obscure_metric/);
    expect(text).not.toMatch(/\[object Object\]/);
    expect(text).not.toMatch(/\{"a":1\}/);
  });

  it("known numeric detail keys keep numbers", () => {
    const text = humanDetail({
      win_rate: 0.5,
      trade_count: 3,
    });
    expect(text).toMatch(/勝率：0\.5/);
    expect(text).toMatch(/成交筆數：3/);
  });
});
