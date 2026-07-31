/** Per-run unread/read state for P5 main list. */

const LIVE_KEY = "p5-result-read-v1";

/** Owner-review: in-memory only — never localStorage. */
const reviewRead = new Set<string>();

export function isRunRead(runId: string, mode: "live" | "owner-review"): boolean {
  if (mode === "owner-review") {
    return reviewRead.has(runId);
  }
  try {
    const raw = localStorage.getItem(LIVE_KEY);
    if (!raw) {
      return false;
    }
    const parsed = JSON.parse(raw) as Record<string, boolean>;
    return Boolean(parsed[runId]);
  } catch {
    return false;
  }
}

export function markRunRead(
  runId: string,
  mode: "live" | "owner-review",
): void {
  if (mode === "owner-review") {
    reviewRead.add(runId);
    return;
  }
  try {
    const raw = localStorage.getItem(LIVE_KEY);
    const parsed = raw
      ? (JSON.parse(raw) as Record<string, boolean>)
      : {};
    parsed[runId] = true;
    localStorage.setItem(LIVE_KEY, JSON.stringify(parsed));
  } catch {
    /* ignore quota */
  }
}

/** Tests only. */
export function __resetReviewReadState(): void {
  reviewRead.clear();
}
