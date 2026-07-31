import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { joinValue, splitValue } from "../../lib/datetimeValue";
import { DateField } from "./DateField";

function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <DateField
        label="由 本地時間"
        value={value}
        onChange={setValue}
        testId="range-start-field"
      />
      <output data-testid="emitted">{value}</output>
    </>
  );
}

describe("DateField value handling", () => {
  it("splits and rejoins without changing a single character", () => {
    for (const value of [
      "2026-05-05T00:00:00",
      "2026-12-31T23:59:59",
      "2026-01-01T09:30:00",
    ]) {
      const parts = splitValue(value);
      expect(parts).not.toBeNull();
      expect(joinValue(parts!.date, parts!.time)).toBe(value);
    }
  });

  it("fills seconds when a browser time control omits them", () => {
    expect(joinValue("2026-05-05", "09:30")).toBe("2026-05-05T09:30:00");
  });

  it("refuses to guess at an unparseable value", () => {
    expect(splitValue("")).toBeNull();
    expect(splitValue("2026-05-05")).toBeNull();
    expect(splitValue("not a date")).toBeNull();
  });
});

/*
 * §4 ① of PROJECT_STATE: a calendar date is a label, not an instant. The
 * −2 day incident came from converting one. This suite is the standing guard:
 * the same value must render the same day in a UTC-negative zone, a
 * UTC-positive zone, and the machine's own.
 */
describe("DateField renders the calendar date it was given, in any time zone", () => {
  const ZONES = ["UTC", "America/New_York", "Asia/Tokyo", "Pacific/Kiritimati"];

  for (const zone of ZONES) {
    it(`shows 2026-05-05 00:00:00 verbatim under ${zone}`, () => {
      const original = process.env.TZ;
      process.env.TZ = zone;
      try {
        render(<Harness initial="2026-05-05T00:00:00" />);
        expect(screen.getByTestId("range-start-field")).toHaveTextContent(
          "2026-05-05 00:00:00",
        );
      } finally {
        process.env.TZ = original;
      }
    });
  }
});

describe("DateField picker", () => {
  it("opens a themed panel rather than the browser's own picker", async () => {
    const user = userEvent.setup();
    render(<Harness initial="2026-05-05T09:30:00" />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("range-start-field"));

    expect(screen.getByRole("dialog", { name: "由 本地時間" })).toBeInTheDocument();
    // The trigger shows a formatted value, so its name has to come from the
    // field label — otherwise the control is unnamed to a screen reader.
    expect(
      screen.getByRole("button", { name: "由 本地時間" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("datepick-month")).toHaveTextContent("2026 年 5 月");
    // The control it replaces must be gone, not merely hidden behind it.
    expect(
      document.querySelector('input[type="datetime-local"]'),
    ).toBeNull();
  });

  it("keeps the time when only the day is picked", async () => {
    const user = userEvent.setup();
    render(<Harness initial="2026-05-05T09:30:00" />);

    await user.click(screen.getByTestId("range-start-field"));
    await user.click(screen.getByRole("button", { name: "2026-05-12" }));

    expect(screen.getByTestId("emitted")).toHaveTextContent(
      "2026-05-12T09:30:00",
    );
  });

  it("moves months without touching the selected value", async () => {
    const user = userEvent.setup();
    render(<Harness initial="2026-05-05T09:30:00" />);

    await user.click(screen.getByTestId("range-start-field"));
    await user.click(screen.getByRole("button", { name: "上個月" }));

    expect(screen.getByTestId("datepick-month")).toHaveTextContent("2026 年 4 月");
    expect(screen.getByTestId("emitted")).toHaveTextContent(
      "2026-05-05T09:30:00",
    );
  });

  it("closes on Escape and hands focus back to the field", async () => {
    const user = userEvent.setup();
    render(<Harness initial="2026-05-05T09:30:00" />);

    const trigger = screen.getByTestId("range-start-field");
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("does not open while disabled", async () => {
    const user = userEvent.setup();
    render(
      <DateField
        label="由 本地時間"
        value="2026-05-05T09:30:00"
        onChange={() => undefined}
        testId="range-start-field"
        disabled
      />,
    );

    await user.click(screen.getByTestId("range-start-field"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
