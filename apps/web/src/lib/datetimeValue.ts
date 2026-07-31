/**
 * Local wall-clock datetime strings, handled as strings.
 *
 * `YYYY-MM-DDTHH:mm:ss` with no zone — the same shape
 * `<input type="datetime-local">` produced, so the forms around it are
 * unchanged. Everything here is slicing and concatenation on purpose: parsing
 * one of these into a `Date` applies the machine's zone, and that is precisely
 * the conversion that mis-filed a trading day by two days
 * (docs/PROJECT_STATE §4 ①).
 */

const VALUE_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

export interface DatetimeParts {
  /** `YYYY-MM-DD` */
  date: string;
  /** `HH:mm:ss` */
  time: string;
}

/** Null for anything that is not a complete value — never a guess. */
export function splitValue(value: string): DatetimeParts | null {
  const m = VALUE_RE.exec(value);
  if (!m) return null;
  return {
    date: `${m[1]}-${m[2]}-${m[3]}`,
    time: `${m[4]}:${m[5]}:${m[6] ?? "00"}`,
  };
}

/** Seconds are filled in when a time control reports only `HH:mm`. */
export function joinValue(date: string, time: string): string {
  const t = time.length === 5 ? `${time}:00` : time;
  return `${date}T${t}`;
}
