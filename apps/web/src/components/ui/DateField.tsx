/**
 * Date + time picker that opens in a popover.
 *
 * Replaces `<input type="datetime-local">`, whose picker is drawn by the
 * browser and ignores the app's themes entirely.
 *
 * The contract is deliberately identical to the input it replaces: value and
 * onChange both speak `YYYY-MM-DDTHH:mm:ss` **local wall-clock**. Nothing here
 * converts a time zone — the surrounding form already owns the UTC counterpart
 * it sends to the backend, and a second conversion in here is exactly how the
 * −2 day trading-date incident happened (docs/PROJECT_STATE §4 ①).
 *
 * So every operation below is either string slicing or calendar arithmetic on
 * a local `Date` built from explicit parts. No parsing of the whole string
 * into a `Date`, no `toISOString`, ever.
 */

import { useState } from "react";

import { joinValue, splitValue } from "../../lib/datetimeValue";
import { Popover } from "./Popover";

export interface DateFieldProps {
  /** `YYYY-MM-DDTHH:mm:ss`; empty string when unset. */
  value: string;
  onChange: (next: string) => void;
  /** Accessible name — matches the visible field label. */
  label: string;
  disabled?: boolean;
  testId?: string;
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** `Date` is used only as a calendar; the label is built back from parts. */
function dateLabel(y: number, mIndex: number, d: number): string {
  return `${String(y)}-${pad2(mIndex + 1)}-${pad2(d)}`;
}

function daysInMonth(y: number, mIndex: number): number {
  return new Date(y, mIndex + 1, 0).getDate();
}

function firstWeekday(y: number, mIndex: number): number {
  return new Date(y, mIndex, 1).getDay();
}

function todayParts(): { y: number; m: number; d: number } {
  const now = new Date();
  return { y: now.getFullYear(), m: now.getMonth(), d: now.getDate() };
}

function Calendar({
  selectedDate,
  onPick,
}: {
  selectedDate: string | null;
  onPick: (date: string) => void;
}) {
  const initial = selectedDate
    ? {
        y: Number(selectedDate.slice(0, 4)),
        m: Number(selectedDate.slice(5, 7)) - 1,
      }
    : { y: todayParts().y, m: todayParts().m };
  const [view, setView] = useState(initial);

  const total = daysInMonth(view.y, view.m);
  const lead = firstWeekday(view.y, view.m);
  const today = todayParts();

  const step = (delta: number) => {
    const next = new Date(view.y, view.m + delta, 1);
    setView({ y: next.getFullYear(), m: next.getMonth() });
  };

  return (
    <div className="datepick">
      <div className="datepick__head">
        <button
          type="button"
          className="datepick__nav"
          aria-label="上個月"
          onClick={() => {
            step(-1);
          }}
        >
          ‹
        </button>
        <span className="datepick__month" data-testid="datepick-month">
          {view.y} 年 {view.m + 1} 月
        </span>
        <button
          type="button"
          className="datepick__nav"
          aria-label="下個月"
          onClick={() => {
            step(1);
          }}
        >
          ›
        </button>
      </div>

      <div className="datepick__grid" role="grid" aria-label="日期">
        {WEEKDAYS.map((w) => (
          <span key={w} className="datepick__wd" aria-hidden="true">
            {w}
          </span>
        ))}
        {Array.from({ length: lead }, (_, i) => (
          <span key={`lead-${String(i)}`} className="datepick__blank" />
        ))}
        {Array.from({ length: total }, (_, i) => {
          const day = i + 1;
          const label = dateLabel(view.y, view.m, day);
          const isSelected = label === selectedDate;
          const isToday =
            view.y === today.y && view.m === today.m && day === today.d;
          return (
            <button
              key={label}
              type="button"
              className={[
                "datepick__day",
                isSelected ? "datepick__day--on" : "",
                isToday ? "datepick__day--today" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              aria-pressed={isSelected}
              aria-label={label}
              onClick={() => {
                onPick(label);
              }}
            >
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function DateField({
  value,
  onChange,
  label,
  disabled = false,
  testId,
}: DateFieldProps) {
  const parts = splitValue(value);
  const display = parts ? `${parts.date} ${parts.time}` : "未揀";

  return (
    <Popover
      label={label}
      align="start"
      disabled={disabled}
      triggerClassName="datefield-trigger"
      triggerTestId={testId}
      trigger={
        <>
          <span className="datefield-trigger__value">{display}</span>
          <span className="datefield-trigger__icon" aria-hidden="true">
            ▾
          </span>
        </>
      }
    >
      {(close) => (
        <div className="datefield-panel">
          <Calendar
            selectedDate={parts?.date ?? null}
            onPick={(date) => {
              onChange(joinValue(date, parts?.time ?? "00:00:00"));
            }}
          />

          <label className="datefield-time">
            <span>時間</span>
            <input
              type="time"
              step={1}
              className="inp"
              value={parts?.time ?? ""}
              aria-label={`${label} 時間`}
              onChange={(e) => {
                const nextTime = e.target.value;
                if (!nextTime) return;
                const date =
                  parts?.date ??
                  dateLabel(todayParts().y, todayParts().m, todayParts().d);
                onChange(joinValue(date, nextTime));
              }}
            />
          </label>

          <div className="datefield-actions">
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => {
                const t = todayParts();
                onChange(
                  joinValue(
                    dateLabel(t.y, t.m, t.d),
                    parts?.time ?? "00:00:00",
                  ),
                );
              }}
            >
              今日
            </button>
            <button type="button" className="btn btn--sm" onClick={close}>
              完成
            </button>
          </div>
        </div>
      )}
    </Popover>
  );
}
