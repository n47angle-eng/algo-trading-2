import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { InfoButton } from "../ui/InfoButton";
import { useWorkbench } from "../../lib/p2/WorkbenchContext";
import { validateDraftAgainstCatalog } from "../../lib/sketch/catalogExportGate";
import { buildSketchZipBlob } from "../../lib/sketch/downloadPackage";
import {
  expectedFromPackage,
  postSketchZip,
  SketchPostError,
} from "../../lib/sketch/postSketchPackage";
import {
  applyChartChange,
  canExportSketch,
  commitLocalSketchExport,
  draftGapSummary,
  duplicateDraftAsNew,
  exportBlockingReasons,
  listDrafts,
  loadOrCreateActiveDraft,
  prepareSketchPackage,
  saveDraft,
  selectDraft,
  selectInstrument,
  startNewDraft,
  readSketchStore,
  writeSketchStore,
} from "../../lib/sketch/store";
import type {
  SketchDraft,
  SketchPackagePreview,
} from "../../lib/sketch/types";
import { ExportDialog } from "./ExportDialog";
import { InstrumentPicker } from "./InstrumentPicker";
import { SketchCell } from "./SketchCell";

interface SketchTabProps {
  onGoQuantify: () => void;
  /** Bumps when owner-review fixture reloads. */
  fixtureEpoch?: number;
}

export function SketchTab({
  onGoQuantify,
  fixtureEpoch = 0,
}: SketchTabProps) {
  const { storage, catalog, ownerReview } = useWorkbench();
  const [draft, setDraft] = useState<SketchDraft | null>(null);
  const [drafts, setDrafts] = useState<SketchDraft[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportPreview, setExportPreview] =
    useState<SketchPackagePreview | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  /**
   * Background A local-commit failure while viewing another draft (Correction B).
   * Independent of active B error/notice — never opens A dialog or steals B.
   */
  const [backgroundAlert, setBackgroundAlert] = useState<string | null>(null);
  /** UI generation for setState only — store commit on exact 201 is independent. */
  const exportSeq = useRef(0);
  /** Sync guard against double-click before React re-render. */
  const pendingExportIdsRef = useRef<Set<string>>(new Set());
  const activeDraftRef = useRef<SketchDraft | null>(null);
  const mountedRef = useRef(true);

  const refreshList = useCallback(() => {
    setDrafts(listDrafts(storage));
  }, [storage]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const d = loadOrCreateActiveDraft(storage);
    setDraft(d);
    activeDraftRef.current = d;
    setDrafts(listDrafts(storage));
    setExportPreview(null);
    setError(null);
    setBackgroundAlert(null);
    setExportBusy(false);
    exportSeq.current += 1; // drop late UI updates on fixture remount
  }, [storage, fixtureEpoch]);

  useEffect(() => {
    activeDraftRef.current = draft;
    // Busy indicator only while the active draft itself is in-flight.
    setExportBusy(
      draft ? pendingExportIdsRef.current.has(draft.sketchId) : false,
    );
  }, [draft]);

  const blockers = useMemo(() => {
    if (!draft) return [];
    return [
      ...exportBlockingReasons(draft),
      ...validateDraftAgainstCatalog(draft, catalog),
    ];
  }, [draft, catalog]);
  const canExport = useMemo(
    () =>
      draft
        ? canExportSketch(draft) &&
          validateDraftAgainstCatalog(draft, catalog).length === 0
        : false,
    [draft, catalog],
  );

  const updateDraft = useCallback((next: SketchDraft) => {
    setDraft(next);
    activeDraftRef.current = next;
  }, []);

  const cloneDraft = (d: SketchDraft): SketchDraft =>
    JSON.parse(JSON.stringify(d)) as SketchDraft;

  const runExport = useCallback(async () => {
    if (!draft) {
      return;
    }
    // Sync double-submit guard (same sketch) — do not wait for React disabled.
    if (pendingExportIdsRef.current.has(draft.sketchId)) {
      return;
    }
    pendingExportIdsRef.current.add(draft.sketchId);
    setExportBusy(true);
    setError(null);
    setNotice(null);
    // Do not clear backgroundAlert here — other drafts' repo-success notices stay.

    const seq = ++exportSeq.current;
    // Immutable snapshot at click — package + success commit use only this.
    const snapshot = cloneDraft(draft);
    const origin = snapshot.origin;
    const sketchId = snapshot.sketchId;
    const localBadgeFailMsg = (forBackground: boolean) =>
      forBackground
        ? `背景匯出 ${origin}/${sketchId}：repo 已寫入，但本地 badge／草稿狀態同步失敗——請重新整理草稿列表確認狀態，唔好用同一 id 盲重試。`
        : `repo 已寫入，但本地 badge／草稿狀態同步失敗（${origin}/${sketchId}）——請重新整理草稿列表確認狀態，唔好用同一 id 盲重試。`;

    const stillViewingOrigin = () =>
      Boolean(
        mountedRef.current &&
          activeDraftRef.current?.sketchId === sketchId &&
          activeDraftRef.current.origin === origin,
      );

    const clearPending = () => {
      pendingExportIdsRef.current.delete(sketchId);
      if (
        mountedRef.current &&
        activeDraftRef.current?.sketchId === sketchId
      ) {
        setExportBusy(false);
      }
    };

    try {
      const packagePreview = prepareSketchPackage(snapshot, catalog);

      if (ownerReview) {
        // Isolation: no business POST; local commit only
        const result = commitLocalSketchExport(
          snapshot,
          packagePreview,
          storage,
        );
        if (stillViewingOrigin() && seq === exportSeq.current) {
          setDraft(result.draft);
          activeDraftRef.current = result.draft;
          setExportPreview(result.package);
          setNotice(
            `Owner-review：已準備 in-memory 圖文包（${result.package.relativeDir}）——冇寫 backend`,
          );
        }
        if (mountedRef.current) {
          setDrafts(listDrafts(storage));
        }
        return;
      }

      const zipBlob = await buildSketchZipBlob(packagePreview);
      const expected = expectedFromPackage(packagePreview, snapshot);
      await postSketchZip(zipBlob, expected);

      // Exact-valid 201: always commit snapshot A to store (never discard truth).
      const viewingA = stillViewingOrigin();
      try {
        const result = commitLocalSketchExport(
          snapshot,
          packagePreview,
          storage,
          new Date(),
          { preserveActiveId: !viewingA },
        );
        if (!mountedRef.current) {
          return;
        }
        setDrafts(listDrafts(storage));
        // Only open A dialog / update A editor when still viewing A.
        if (viewingA && seq === exportSeq.current) {
          setDraft(result.draft);
          activeDraftRef.current = result.draft;
          setExportPreview(result.package);
          setNotice(
            `已寫入 ${result.package.relativeDir}——Terminal 可直接讀`,
          );
        }
        // If already on B / unmounted: store A exported; no A dialog, B untouched.
      } catch {
        // Backend already accepted ZIP — local badge failure must stay visible.
        if (!mountedRef.current) {
          return;
        }
        if (viewingA && seq === exportSeq.current) {
          setError(localBadgeFailMsg(false));
          setExportPreview(null);
        } else {
          // Viewing B / new draft: keep B state; independent background alert.
          setBackgroundAlert(localBadgeFailMsg(true));
        }
      }
    } catch (err) {
      // POST failure: zero local export. Only surface error if still on A.
      if (stillViewingOrigin() && seq === exportSeq.current) {
        const msg =
          err instanceof SketchPostError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err);
        setError(msg);
        setExportPreview(null);
      }
    } finally {
      clearPending();
    }
  }, [draft, catalog, ownerReview, storage]);

  // D13: only migrated pre-instrument drafts are "legacy"
  const legacyIncomplete = Boolean(
    draft &&
      draft.instrumentLegacy &&
      !draft.exported &&
      (!draft.instrument || !draft.assetClass),
  );

  if (!draft) {
    return <p className="state-msg">載入草圖…</p>;
  }

  const fieldsReadonly = draft.exported;

  return (
    <div className="detail-stack">
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {backgroundAlert ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="sketch-background-alert"
        >
          {backgroundAlert}
        </p>
      ) : null}
      {notice ? (
        <p className="state-msg" role="status">
          {notice}
        </p>
      ) : null}

      <section className="panel" role="region" aria-label="草稿列表">
        <div className="panel__head">
          <h2 className="panel__title">草稿列表</h2>
          <InfoButton label="草稿列表點用" align="end">
            撳一行就可以繼續填嗰份草稿。撳「新草圖」之前，而家呢份會自動存起，唔會掉失。草稿係可以隨時改嘅——只有確認咗嘅版本先鎖死。
            {ownerReview ? "（owner-review：in-memory store）" : null}
          </InfoButton>
        </div>
        {drafts.length === 0 ? (
          <p className="state-msg">仲未有草稿。</p>
        ) : (
          <ul className="draft-list">
            {drafts.map((item) => {
              const active = item.sketchId === draft.sketchId;
              const status = item.exported ? "已匯出" : "草稿";
              const gaps = draftGapSummary(item);
              return (
                <li key={item.sketchId}>
                  <button
                    type="button"
                    className={
                      active
                        ? "draft-list__btn draft-list__btn--active"
                        : "draft-list__btn"
                    }
                    aria-current={active ? "true" : undefined}
                    onClick={() => {
                      if (active) return;
                      const opened = selectDraft(item.sketchId, draft, storage);
                      if (opened) {
                        setDraft(opened);
                        refreshList();
                        setNotice(`已開 ${opened.sketchId}`);
                        setError(null);
                        setExportPreview(null);
                      }
                    }}
                  >
                    <span className="draft-list__id">{item.sketchId}</span>
                    <span className="draft-list__status">{status}</span>
                    <span className="draft-list__gaps">{gaps}</span>
                    {active ? (
                      <span className="draft-list__now">而家開緊</span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="panel" role="region" aria-label="草圖編輯">
        <header className="sketch-head">
          <h2 className="panel__title">草圖</h2>
          <InfoButton label="草圖點填" align="end">
            用日常講法寫低你嘅諗法就得，唔使寫得好似程式。標題、交易市場、資產類別係必填——填唔齊就唔會俾你去量化確認，呢個係故意嘅。可以上載圖表截圖做參考。
          </InfoButton>
          <span className="sketch-head__id" aria-label="草圖編號">
            {draft.sketchId}
            {draft.exported ? " · 已匯出" : " · 草稿"}
            {draft.instrument ? ` · ${draft.instrument}` : ""}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => {
              const next = startNewDraft(draft, storage);
              setDraft(next);
              refreshList();
              setNotice(`已開新草圖 ${next.sketchId}`);
              setError(null);
              setExportPreview(null);
            }}
          >
            新草圖
          </button>
          {draft.exported ? (
            <button
              type="button"
              className="btn"
              data-testid="duplicate-sketch"
              onClick={() => {
                saveDraft(draft, storage);
                const snap = readSketchStore(storage);
                const copy = duplicateDraftAsNew(
                  draft,
                  snap.drafts.map((d) => d.sketchId),
                );
                writeSketchStore(
                  {
                    ...snap,
                    drafts: [...snap.drafts, copy],
                    activeId: copy.sketchId,
                  },
                  storage,
                );
                setDraft(copy);
                refreshList();
                setNotice(
                  `已複製成新草圖 ${copy.sketchId}（已清走圖片同判斷文字，需重新上載；可改 instrument）`,
                );
                setError(null);
                setExportPreview(null);
              }}
            >
              複製成新草圖
            </button>
          ) : null}
        </header>

        <label className="field">
          <span>草圖標題（必填）</span>
          <input
            className={
              draft.title.trim() ? "inp" : "inp inp--required-empty"
            }
            type="text"
            value={draft.title}
            placeholder="例如「NQ 趨勢日回踩 90EMA」"
            aria-label="草圖標題"
            aria-required="true"
            disabled={fieldsReadonly}
            onChange={(e) => {
              updateDraft({ ...draft, title: e.target.value });
            }}
          />
        </label>

        <InstrumentPicker
          catalog={catalog}
          instrument={draft.instrument}
          assetClass={draft.assetClass}
          locked={draft.instrumentLocked}
          exported={draft.exported}
          legacyIncomplete={legacyIncomplete}
          onSelect={(symbol, assetClass) => {
            updateDraft(selectInstrument(draft, symbol, assetClass));
          }}
        />

        <div className="sketch-grid" role="region" aria-label="四格圖文">
          {draft.charts.map((chart, index) => (
            <SketchCell
              key={chart.slotId}
              chart={chart}
              index={index}
              readonly={fieldsReadonly}
              onChange={(nextChart) => {
                if (fieldsReadonly) return;
                // Pending export uses click-time snapshot; live edits do not
                // rebind the in-flight package (immutable snapshot).
                updateDraft(applyChartChange(draft, nextChart));
              }}
            />
          ))}
        </div>

        <p className="panel__note">
          每格嘅時間框架、角色、指標 chips 都由你揀（可重複）。ZIP／meta 檔名固定
          chart-D／1H／30m／5m.png。首圖上載後 instrument 永久鎖定。四張真 PNG
          同 strategy 理據齊先可匯出。
        </p>

        <label className="field">
          <span>
            整體理據 rationale（必填）
            {draft.rationale.trim() ? "" : " · 未填"}
          </span>
          <textarea
            className={
              draft.rationale.trim() ? "inp inp--area" : "inp inp--area inp--required-empty"
            }
            rows={4}
            value={draft.rationale}
            placeholder="四張圖串埋一齊嘅想法（AI 最需要呢段）"
            aria-label="整體理據"
            aria-required="true"
            disabled={fieldsReadonly}
            onChange={(e) => {
              updateDraft({ ...draft, rationale: e.target.value });
            }}
          />
        </label>

        <div className="form-actions">
          <button
            type="button"
            className="btn btn--primary"
            data-testid="sketch-export"
            // Double-submit is blocked by sync pendingExportIdsRef, not only
            // the next React paint of disabled. Keep button clickable so a
            // second click is a no-op via the ref (D3).
            disabled={!canExport || fieldsReadonly}
            aria-busy={exportBusy || undefined}
            title={
              exportBusy
                ? "匯出中…"
                : canExport
                  ? ownerReview
                    ? "匯出（owner-review 本地）"
                    : "匯出並寫入 repo"
                  : blockers.join(" · ")
            }
            onClick={() => {
              void runExport();
            }}
          >
            {exportBusy ? "匯出中…" : "⬇ 匯出圖文包"}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              const saved = saveDraft(draft, storage);
              setDraft(saved);
              refreshList();
              setNotice(`草稿已存（${saved.sketchId}）`);
              setError(null);
            }}
          >
            存草稿
          </button>
          {!canExport ? (
            <span className="state-msg" role="status" aria-label="匯出阻擋原因">
              {blockers.join(" · ")} → 匯出掣 disabled
            </span>
          ) : null}
        </div>
      </section>

      {exportPreview ? (
        <ExportDialog
          packagePreview={exportPreview}
          mode={
            ownerReview
              ? "owner_review"
              : "normal_persisted"
          }
          onClose={() => {
            setExportPreview(null);
          }}
          onGoQuantify={() => {
            setExportPreview(null);
            onGoQuantify();
          }}
        />
      ) : null}
    </div>
  );
}
