/** Progress counters — five buckets must sum to total. */

export interface ProgressCounts {
  completed: number;
  running: number;
  queued: number;
  failed: number;
  cancelled: number;
  total: number;
}

export function countProgress(
  units: ReadonlyArray<{ status: string }>,
): ProgressCounts {
  const counts: ProgressCounts = {
    completed: 0,
    running: 0,
    queued: 0,
    failed: 0,
    cancelled: 0,
    total: units.length,
  };
  for (const u of units) {
    switch (u.status) {
      case "completed":
        counts.completed += 1;
        break;
      case "running":
        counts.running += 1;
        break;
      case "queued":
        counts.queued += 1;
        break;
      case "failed":
        counts.failed += 1;
        break;
      case "cancelled":
        counts.cancelled += 1;
        break;
      default:
        break;
    }
  }
  return counts;
}

export function progressSum(c: ProgressCounts): number {
  return c.completed + c.running + c.queued + c.failed + c.cancelled;
}

/** Owner-facing one-line summary. */
export function formatProgressSummary(c: ProgressCounts): string {
  return (
    `完成 ${c.completed} · 進行中 ${c.running} · 等緊 ${c.queued}` +
    ` · 失敗 ${c.failed} · 已取消 ${c.cancelled} · 共 ${c.total} 次`
  );
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分`;
}
