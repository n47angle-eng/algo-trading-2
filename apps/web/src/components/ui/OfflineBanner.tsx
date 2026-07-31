/**
 * The one line that stays on screen while data cannot be trusted.
 *
 * A toast is wrong for this: being offline is a *condition*, not an event, and
 * it has to keep saying so for as long as it lasts. The banner also carries
 * the freshness stamp, because the numbers underneath it are still the last
 * ones we received — visible, honest, and labelled as old.
 */

import {
  dataMayBeStale,
  formatFreshness,
  useReachability,
  type Reachability,
} from "../../lib/net/online";

function headline(state: Reachability): string {
  if (state === "offline") return "而家離線";
  return "連唔到資料服務";
}

/**
 * The two states get different promises, because only one of them is certain.
 * Saying "唔改得嘢" while a write could still succeed would be a claim the app
 * cannot keep.
 */
function consequence(state: Reachability): string {
  if (state === "offline") return "暫時改唔到嘢";
  return "改嘢可能唔成功";
}

export function OfflineBanner() {
  const { state, lastOkAt, recheck } = useReachability();

  if (!dataMayBeStale(state)) return null;

  return (
    <div className="offline-bar" role="status" data-testid="offline-bar">
      <span className="offline-bar__dot" aria-hidden="true" />
      <span className="offline-bar__text">
        <b>{headline(state)}</b>
        <span className="offline-bar__sub">
          {/* Never "no data" — the screen is showing something, and the owner
              must know exactly how old it is. */}
          顯示緊之前拎到嘅資料 · {formatFreshness(lastOkAt)} ·{" "}
          {consequence(state)}
        </span>
      </span>
      <button type="button" className="offline-bar__retry" onClick={recheck}>
        再試
      </button>
    </div>
  );
}
