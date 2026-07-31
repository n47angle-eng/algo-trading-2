import type { StrategyVersion } from "../../api/client";

interface StrategyDetailProps {
  version: StrategyVersion;
  busy: boolean;
  onConfirm: () => void;
}

/** Provenance vocabulary from docs/05 §1.2. */
const SOURCE_LABELS: Record<string, string> = {
  owner_explicit: "Owner 明示",
  owner_inferred: "Owner 推斷",
  web_researched: "資料研究",
  market_convention: "市場慣例",
  system_default: "系統預設",
  derived: "推導",
};

/**
 * S2c read-only review: parameters, provenance pills, and the unquantified-notes
 * iron gate — which is never collapsed and, when empty, still says "0 項" so the
 * Owner can tell "the AI reported none" apart from "the UI is not showing them".
 */
export function StrategyDetail({
  version,
  busy,
  onConfirm,
}: StrategyDetailProps) {
  const notes = version.unquantified_notes;

  return (
    <>
      <section className="panel" role="region" aria-label="策略詳情">
        <h2 className="panel__title">
          {version.strategy_id} · {version.name} · {version.status}
        </h2>
        <dl className="meta-grid">
          <div>
            <dt>建立</dt>
            <dd className="table__mono">{version.created}</dd>
          </div>
          <div>
            <dt>spec_ref</dt>
            <dd className="table__mono">{version.spec_ref ?? "—"}</dd>
          </div>
          <div>
            <dt>based_on</dt>
            <dd className="table__mono">{version.based_on ?? "—"}</dd>
          </div>
          <div>
            <dt>based_on_sketch</dt>
            <dd className="table__mono">{version.based_on_sketch ?? "—"}</dd>
          </div>
          <div>
            <dt>內容 SHA-256</dt>
            <dd className="table__mono">
              {version.content_sha256.slice(0, 16)}…
            </dd>
          </div>
          <div>
            <dt>確認時間</dt>
            <dd className="table__mono">{version.confirmed_at ?? "未確認"}</dd>
          </div>
        </dl>
      </section>

      <section className="panel" role="region" aria-label="unquantified notes">
        <h2 className="panel__title">
          unquantified_notes · {notes.length} 項（AI 譯唔到／自行假設咗嘅嘢）
        </h2>
        {notes.length === 0 ? (
          <p className="state-msg">
            0 項——AI 聲明冇任何譯唔到或者自行假設嘅地方。
          </p>
        ) : (
          <ul className="note-list">
            {notes.map((note) => (
              <li key={note.note} className="note-list__item">
                <p className="note-list__text">{note.note}</p>
                {note.action_needed ? (
                  <p className="note-list__action">
                    需要 Owner 處理：{note.action_needed}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel" role="region" aria-label="rationale">
        <h2 className="panel__title">rationale（記分卡維度 8）</h2>
        <p className="prose">{version.rationale}</p>
      </section>

      <section className="panel" role="region" aria-label="參數唯讀表">
        <h2 className="panel__title">參數（唯讀）＋來源</h2>
        <p className="panel__note">
          D9：策略參數喺 UI <strong>唯讀</strong>
          ——改參數必經 terminal AI 出新版文件。
        </p>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>參數</th>
                <th>值</th>
                <th>來源（provenance）</th>
              </tr>
            </thead>
            <tbody>
              {version.parameters.map((row) => (
                <tr key={`${row.label}-${row.path ?? "structure"}`}>
                  <td>{row.label}</td>
                  <td className="table__mono table__cell--nowrap">{row.value}</td>
                  <td>
                    {row.source ? (
                      <span
                        className="chip chip--provenance"
                        title={row.note ?? row.path ?? undefined}
                      >
                        {SOURCE_LABELS[row.source] ?? row.source}
                      </span>
                    ) : (
                      <span className="chip" title="非數值參數，無 provenance 路徑">
                        文件結構
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" role="region" aria-label="確認">
        <h2 className="panel__title">Owner 確認（S2c gate）</h2>
        {version.status === "confirmed" ? (
          <p className="state-msg">
            已於 {version.confirmed_at} 確認——P4 可以揀呢個版本跑回測。
          </p>
        ) : (
          <>
            <p className="state-msg">
              確認之後版本鎖定（A2 不可變），P4 先至可以揀佢。請先睇完上面
              {notes.length} 項 unquantified_notes。
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="btn btn--primary"
                disabled={busy}
                onClick={onConfirm}
              >
                ✓ 確認採用 {version.strategy_id}
              </button>
            </div>
          </>
        )}
      </section>

      <section className="panel" role="region" aria-label="YAML 原文">
        <h2 className="panel__title">YAML 原文（source_text · 逐字保存）</h2>
        <pre className="code-block">{version.source_text}</pre>
      </section>
    </>
  );
}
