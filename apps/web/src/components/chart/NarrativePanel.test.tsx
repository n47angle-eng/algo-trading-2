import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NarrativePanel } from "./NarrativePanel";

describe("NarrativePanel", () => {
  it("renders deterministic steps from backend templates", () => {
    render(
      <NarrativePanel
        steps={[
          {
            time: "2026-05-07T20:55:00Z",
            time_label: "05-07 20:55",
            layer: "Daily 閘",
            tone: "ok",
            text: "Regime → trend（closed Daily bar 判定）",
          },
          {
            time: "2026-05-21T03:35:00Z",
            time_label: "05-21 03:35",
            layer: "5m 層",
            tone: "ok",
            text: "Signal created（inside）",
          },
        ]}
        note="deterministic templates"
      />,
    );
    expect(screen.getByRole("region", { name: "判斷鏈" })).toBeInTheDocument();
    expect(screen.getByText(/Regime → trend/)).toBeInTheDocument();
    expect(screen.getByText(/Signal created/)).toBeInTheDocument();
    expect(screen.getByText(/deterministic templates/)).toBeInTheDocument();
  });
});
