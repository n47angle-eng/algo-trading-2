/**
 * Iron gate panel — never collapsible (constraint #11).
 * Even 0 items must show "0 項".
 */

export interface NoteItem {
  note: string;
  action_needed: string | null;
}

export function UnquantifiedNotes({ notes }: { notes: NoteItem[] }) {
  return (
    <section className="panel" role="region" aria-label="unquantified notes">
      <h2 className="panel__title">
        unquantified_notes · {notes.length} 項
        {notes.length === 0 ? "（AI 聲明冇假設）" : "（AI 譯唔到／自行假設）"}
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
  );
}
