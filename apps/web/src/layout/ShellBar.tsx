import { Icon } from "../components/ui/Icon";

interface ShellBarProps {
  /** Where pages portal their tab strip. */
  tabSlotRef: React.RefCallback<HTMLDivElement>;
  onOpenMore: () => void;
  moreOpen: boolean;
  onOpenTraders: () => void;
  tradersOpen: boolean;
}

/**
 * The single top bar for both layouts.
 *
 * It carries identity, the current page's section tabs (portalled up from the
 * page body) and the overflow entry. Everything that used to sit as a separate
 * strip under the page title now lives on this one line, so content starts
 * higher on every screen.
 */
export function ShellBar({
  tabSlotRef,
  onOpenMore,
  moreOpen,
  onOpenTraders,
  tradersOpen,
}: ShellBarProps) {
  return (
    <header className="shell-bar">
      {/* The wordmark is desktop-only; on a phone the mark alone holds identity
          so the section tabs can share the same single row. */}
      <span className="shell-bar__brand">
        <span className="shell-bar__mark" aria-hidden="true">
          <Icon name="brand" />
        </span>
        <span className="shell-bar__wordmark">Futures Research</span>
      </span>

      <div className="shell-bar__tabs" ref={tabSlotRef} />

      <div className="shell-bar__actions">
        {/* Same glance surface as the phone tab bar's 交易員 button, so the
            roster is one click from every page on desktop too. */}
        <button
          type="button"
          className="shell-bar__traders"
          aria-expanded={tradersOpen}
          onClick={onOpenTraders}
        >
          <Icon name="daytrade" />
          <span>交易員</span>
        </button>

        {/* On a phone the tab bar carries 更多; this is the desktop entry. */}
        <button
          type="button"
          className="icon-btn shell-bar__more"
          aria-label="更多選項"
          aria-expanded={moreOpen}
          onClick={onOpenMore}
        >
          <Icon name="more" />
        </button>
      </div>
    </header>
  );
}
