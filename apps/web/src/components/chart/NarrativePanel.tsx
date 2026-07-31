import type { NarrativeStep } from "../../api/chartTypes";

interface NarrativePanelProps {
  steps: NarrativeStep[];
  note?: string;
  loading?: boolean;
  error?: string | null;
}

/** docs/03: 事件敘事面板 — deterministic template text from backend. */
export function NarrativePanel({
  steps,
  note,
  loading,
  error,
}: NarrativePanelProps) {
  return (
    <section className="panel" role="region" aria-label="判斷鏈">
      <h2 className="panel__title">判斷鏈（事件日誌 · deterministic）</h2>
      {loading ? <p className="state-msg">載入敘事…</p> : null}
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {!loading && !error && steps.length === 0 ? (
        <p className="state-msg">呢個 run 未有可敘事事件（或全被模板過濾）。</p>
      ) : null}
      <div className="narr">
        {steps.map((step, index) => (
          <div className="narr__step" key={`${step.time}-${index}`}>
            <span className="narr__time">{step.time_label}</span>
            <span
              className={
                step.tone === "ok"
                  ? "chip chip--pass"
                  : step.tone === "warn"
                    ? "chip chip--warn"
                    : "chip"
              }
            >
              {step.layer}
            </span>
            <span className="narr__text">{step.text}</span>
          </div>
        ))}
      </div>
      {note ? <p className="state-msg">{note}</p> : null}
    </section>
  );
}
