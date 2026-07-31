import {
  assetClassLabel,
  type InstrumentCatalogRow,
} from "../../lib/catalog/types";
import { findCatalogRow } from "../../lib/catalog/parseCatalog";
import type {
  StrategyUniverse,
  UniverseGateIssue,
} from "../../lib/strategy/universe";

/** Display-only sketch ref summary for left column (from origin-aware load). */
export type UniverseSketchDisplay =
  | { kind: "none" }
  | { kind: "missing"; sketchId: string; origin: string }
  | { kind: "legacy_incomplete"; sketchId: string; reason: string }
  | {
      kind: "present";
      draft: { instrument: string | null; assetClass: string | null };
    };

interface UniversePanelProps {
  universe: StrategyUniverse | null;
  parseIssues: UniverseGateIssue[];
  gateIssues: UniverseGateIssue[];
  catalogRows: InstrumentCatalogRow[] | null;
  localSketch: UniverseSketchDisplay;
  /** Left-side primary from origin-aware sketch when ready. */
  sketchPrimary: { instrument: string; assetClass: string } | null;
}

/**
 * Whole-universe readonly view — no checkboxes, no approved_instruments.
 * Owner only accepts or rejects the entire strategy elsewhere.
 */
export function UniversePanel({
  universe,
  parseIssues,
  gateIssues,
  catalogRows,
  localSketch,
  sketchPrimary,
}: UniversePanelProps) {
  const allIssues = [...parseIssues, ...gateIssues];
  const missingLocal =
    localSketch.kind === "missing"
      ? `本機缺原草圖 ${localSketch.sketchId}（${localSketch.origin}），無法左右對照。其餘 self-contained／catalog gate 仍會跑。`
      : null;

  return (
    <section
      className="panel"
      role="region"
      aria-label="Strategy universe 唯讀"
      data-testid="universe-panel"
    >
      <h2 className="panel__title">策略 Universe（整份唯讀）</h2>
      <p className="panel__note">
        冇 checkbox、冇刪 member——只可確認整份策略，或退回 Terminal 修改整份。
      </p>

      {missingLocal ? (
        <p
          className="state-msg"
          role="status"
          data-testid="local-sketch-missing"
        >
          {missingLocal}
        </p>
      ) : null}

      {localSketch.kind === "legacy_incomplete" ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="local-sketch-legacy"
        >
          本機草圖 {localSketch.sketchId} 係 legacy／incomplete：
          {localSketch.reason}
        </p>
      ) : null}

      <div className="universe-compare">
        <div className="universe-compare__col" data-testid="universe-left">
          <h3 className="universe-compare__h">左：原草圖 primary</h3>
          {sketchPrimary ? (
            <dl className="meta-grid">
              <div>
                <dt>primary instrument</dt>
                <dd className="table__mono">{sketchPrimary.instrument}</dd>
              </div>
              <div>
                <dt>asset class</dt>
                <dd>
                  {assetClassLabel(sketchPrimary.assetClass)}
                  <span className="instrument-picker__enum">
                    {" "}
                    {sketchPrimary.assetClass}
                  </span>
                </dd>
              </div>
            </dl>
          ) : localSketch.kind === "missing" ? (
            <p className="state-msg">本機無草圖可對照</p>
          ) : localSketch.kind === "legacy_incomplete" ? (
            <p className="state-msg">本機草圖 incomplete（見上）</p>
          ) : (
            <p className="state-msg">無完整 lineage 指向本機草圖</p>
          )}
        </div>

        <div className="universe-compare__col" data-testid="universe-right">
          <h3 className="universe-compare__h">右：Terminal 完整 universe</h3>
          {universe ? (
            <>
              <dl className="meta-grid">
                <div>
                  <dt>primary</dt>
                  <dd className="table__mono" data-testid="universe-primary">
                    {universe.primaryInstrument}
                    <span className="instrument-picker__badge"> PRIMARY</span>
                  </dd>
                </div>
                <div>
                  <dt>asset class</dt>
                  <dd data-testid="universe-asset-class">
                    {assetClassLabel(universe.assetClass)}
                    <span className="instrument-picker__enum">
                      {" "}
                      {universe.assetClass}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>session</dt>
                  <dd className="table__mono">{universe.session}</dd>
                </div>
              </dl>
              <ul className="universe-members" data-testid="universe-members">
                {universe.contracts.map((symbol) => {
                  const row = catalogRows
                    ? findCatalogRow(catalogRows, symbol)
                    : null;
                  const isPrimary = symbol === universe.primaryInstrument;
                  const rationale =
                    universe.expansionRationale[symbol]?.trim() || null;
                  return (
                    <li
                      key={symbol}
                      className={
                        isPrimary
                          ? "universe-members__item universe-members__item--primary"
                          : "universe-members__item"
                      }
                      data-symbol={symbol}
                    >
                      <div className="universe-members__head">
                        <strong>
                          {row?.displayName ?? symbol}
                          {isPrimary ? " · primary" : ""}
                        </strong>
                        <span className="table__mono">{symbol}</span>
                      </div>
                      <div className="universe-members__meta">
                        {row
                          ? `${assetClassLabel(row.assetClass)} · ${row.currency}`
                          : "catalog 未知"}
                      </div>
                      {!isPrimary ? (
                        <p className="universe-members__rationale">
                          擴展理由：{rationale ?? "（缺）"}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {/* Explicit absence of interactive member controls */}
              <span
                className="visually-hidden"
                data-testid="universe-no-checkboxes"
              >
                no member checkboxes, no approved_instruments
              </span>
            </>
          ) : (
            <p className="state-msg">universe 無法解析</p>
          )}
        </div>
      </div>

      {allIssues.length > 0 ? (
        <div
          className="universe-gates"
          role="alert"
          data-testid="universe-gate-issues"
        >
          <h3 className="universe-compare__h">Fail-closed 原因</h3>
          <ul>
            {allIssues.map((issue, i) => (
              <li key={`${issue.path}-${i}`}>
                <code>{issue.path}</code>：{issue.message}
                <br />
                <span className="panel__note">修法：{issue.fix}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : universe ? (
        <p className="state-msg" data-testid="universe-gates-ok" role="status">
          Universe gates 通過——可以確認整份策略（確認前仍零寫入）。
        </p>
      ) : null}
    </section>
  );
}
