import {
  assetClassLabel,
  type CatalogState,
} from "../../lib/catalog/types";

interface InstrumentPickerProps {
  catalog: CatalogState;
  instrument: string | null;
  assetClass: string | null;
  locked: boolean;
  exported: boolean;
  /**
   * True only for migrated pre-instrument drafts missing instrument.
   * Fresh drafts with null instrument show required only — not legacy.
   */
  legacyIncomplete: boolean;
  disabled?: boolean;
  onSelect: (symbol: string, assetClass: string) => void;
}

/**
 * Primary instrument picker — after title, before four chart cells.
 * Catalog-driven; never hard-codes NQ/YM/GC names or classes.
 */
export function InstrumentPicker({
  catalog,
  instrument,
  assetClass,
  locked,
  exported,
  legacyIncomplete,
  disabled,
  onSelect,
}: InstrumentPickerProps) {
  const readOnly = exported || locked || Boolean(disabled);
  const selectedRow =
    catalog.status === "ready" && instrument
      ? catalog.rows.find((r) => r.symbol === instrument) ?? null
      : null;

  return (
    <div
      className="instrument-picker"
      role="group"
      aria-label="Primary instrument"
      data-testid="instrument-picker"
      data-locked={locked ? "true" : "false"}
      data-exported={exported ? "true" : "false"}
    >
      <div className="instrument-picker__head">
        <span className="instrument-picker__label">交易市場（必填）</span>
        {exported ? (
          <span className="instrument-picker__badge">已匯出 · 唯讀</span>
        ) : locked ? (
          <span className="instrument-picker__badge">已上載圖片 · 已鎖定</span>
        ) : legacyIncomplete ? (
          <span className="instrument-picker__badge instrument-picker__badge--warn">
            舊草稿缺 instrument · 請補揀一次
          </span>
        ) : (
          <span className="instrument-picker__required">必填</span>
        )}
      </div>

      {catalog.status === "loading" ? (
        <p className="state-msg" data-testid="catalog-loading">
          載入合約清單…
        </p>
      ) : null}
      {catalog.status === "empty" ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="catalog-empty"
        >
          {catalog.message}
        </p>
      ) : null}
      {catalog.status === "error" ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="catalog-error"
        >
          {catalog.message}
        </p>
      ) : null}
      {catalog.status === "invalid" ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="catalog-invalid"
        >
          {catalog.message}
        </p>
      ) : null}

      {catalog.status === "ready" ? (
        <div className="instrument-picker__grid">
          <label className="field">
            <span>Instrument</span>
            <select
              className={
                instrument ? "inp" : "inp inp--required-empty"
              }
              aria-label="選擇 primary instrument"
              aria-required="true"
              disabled={readOnly}
              value={instrument ?? ""}
              data-testid="instrument-select"
              onChange={(e) => {
                const symbol = e.target.value;
                if (!symbol) {
                  return;
                }
                const row = catalog.rows.find((r) => r.symbol === symbol);
                if (row) {
                  onSelect(row.symbol, row.assetClass);
                }
              }}
            >
              <option value="">— 請選擇 —</option>
              {catalog.rows.map((row) => (
                <option key={row.symbol} value={row.symbol}>
                  {row.displayName}（{row.symbol}）
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span>資產類別（catalog）</span>
            <div
              className="instrument-picker__class"
              data-testid="instrument-class"
              title={assetClass ?? undefined}
            >
              {selectedRow ? (
                <>
                  <strong>{assetClassLabel(selectedRow.assetClass)}</strong>
                  <span className="instrument-picker__meta">
                    {selectedRow.currency} · session{" "}
                    {selectedRow.sessionsAvailable.join("／")}
                  </span>
                  <span className="instrument-picker__enum" title="technical">
                    {selectedRow.assetClass}
                  </span>
                </>
              ) : instrument && assetClass ? (
                <>
                  <strong>{assetClassLabel(assetClass)}</strong>
                  <span className="instrument-picker__enum">{assetClass}</span>
                </>
              ) : (
                <span className="state-msg">揀 instrument 後自動帶入</span>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {exported && !instrument ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="legacy-exported-missing-instrument"
        >
          此已匯出草圖缺 instrument／asset_class（legacy）——不可重用匯出或確認；請「複製成新草圖」。
        </p>
      ) : null}
    </div>
  );
}
