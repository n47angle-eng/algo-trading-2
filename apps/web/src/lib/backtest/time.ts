/**
 * P4 time helpers — constraint #3 / G-07.
 * Range inputs: local picker + UTC side-by-side; API always UTC.
 * Trading days: labels only, never timezone-shifted.
 */

/** Format a Date as value for datetime-local with second precision. */
export function toDatetimeLocalValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

/** Parse datetime-local as local wall time → ISO UTC. */
export function localDatetimeToUtcIso(localValue: string): string {
  if (!localValue) {
    return "";
  }
  // datetime-local has no TZ; Date parses as local.
  const d = new Date(localValue);
  if (Number.isNaN(d.getTime())) {
    return "";
  }
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Display UTC counterpart for a local datetime-local value. */
export function formatUtcCounterpart(localValue: string): string {
  const iso = localDatetimeToUtcIso(localValue);
  if (!iso) {
    return "—";
  }
  return `${iso.replace("T", " ").replace("Z", "")} UTC`;
}

/** Canonical UTC instant → local datetime-picker value, preserving the instant. */
export function utcIsoToDatetimeLocal(utcValue: string): string {
  const parsed = new Date(utcValue);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return toDatetimeLocalValue(parsed);
}

/** Owner-facing instant with local wall time and its canonical UTC counterpart. */
export function formatLocalAndUtc(utcValue: string): string {
  const parsed = new Date(utcValue);
  if (Number.isNaN(parsed.getTime())) {
    return "時間暫時核實唔到";
  }
  const local = new Intl.DateTimeFormat("zh-HK", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
  return `${local}（本地） · ${utcValue.replace("T", " ").replace("Z", " UTC")}`;
}

/** Trading-day label — return as-is, never convert. */
export function formatTradingDayLabel(tradingDay: string): string {
  return tradingDay;
}

/** Default range: last ~3 local days. */
export function defaultLocalRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - 3 * 24 * 60 * 60 * 1000);
  return {
    start: toDatetimeLocalValue(start),
    end: toDatetimeLocalValue(end),
  };
}
