import { useCallback, useEffect, useRef, useState } from "react";

import {
  type StrategyNumericPatch,
  type StrategyVersion,
  confirmStrategy,
  deleteStrategyVersion,
  deriveStrategyVersion,
  fetchStrategies,
  fetchStrategyRunReferences,
} from "../../api/client";
import { InfoButton } from "../ui/InfoButton";
import { displayStrategyName } from "../../lib/backtest/format";
import { DELETE_GRACE_MS } from "../../lib/strategyDelete";
import {
  type RunReferenceList,
  type StrategyDeleteGuard,
  parseRunReferenceList,
  parseStrategyDerive,
  parseStrategyDelete,
  readProblemDetail,
  strategyDeleteGuard,
} from "../../lib/strategy/lifecycleContract";
import {
  extractBasedOnSketchLineage,
  lineageFromStrategyFields,
} from "../../lib/strategyYaml";
import { OwnerSketchPanel } from "./OwnerSketchPanel";
import { UnquantifiedNotes } from "./UnquantifiedNotes";

/**
 * Tab ③ — version list, review, numeric edit → server-derived new version,
 * delete with a ~5s undo window followed by the real archive delete.
 *
 * Every lifecycle answer comes from backend truth (`e5dd179`): the guard, the
 * derived version and the archive are never reconstructed locally.
 *
 * @param focusStrategyId exact strategy version id from deep-link (P5 → P2).
 */
interface LibraryTabProps {
  focusStrategyId?: string | null;
  ownerReview?: boolean;
}

interface PendingDelete {
  strategyId: string;
  name: string;
  timerId: ReturnType<typeof setTimeout>;
}

interface DeleteFailure {
  message: string;
  runIds: string[];
}

export function LibraryTab({
  focusStrategyId,
  ownerReview = false,
}: LibraryTabProps = {}) {
  if (ownerReview) {
    return (
      <div className="detail-stack">
        <section
          className="panel"
          role="region"
          aria-label="版本庫 Owner-review 隔離狀態"
          data-testid="owner-review-library-isolated"
        >
          <h2 className="panel__title">版本庫</h2>
          <p className="state-msg">
            Owner-review 呢個 fixture 唔載入版本庫；請用正常模式查真版本。
          </p>
        </section>
      </div>
    );
  }

  return <LiveLibraryTab focusStrategyId={focusStrategyId} />;
}

const GUARD_UNKNOWN_COPY = "引用數量暫時核實唔到，所以唔准刪";

function inUseCopy(count: number): string {
  return `${count} 次回測用緊呢個版本`;
}

function guardTitle(guard: StrategyDeleteGuard): string {
  if (guard.kind === "allowed") {
    return "刪除";
  }
  if (guard.kind === "in-use") {
    return inUseCopy(guard.count);
  }
  return GUARD_UNKNOWN_COPY;
}

function statusLabel(status: StrategyVersion["status"]): string {
  return status === "confirmed" ? "已確認" : "草稿";
}

function LiveLibraryTab({
  focusStrategyId,
}: Pick<LibraryTabProps, "focusStrategyId">) {
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [selected, setSelected] = useState<StrategyVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /**
   * D1: one entry per version. A missing entry or a null value means the
   * reference count is *unknown* — never a known zero.
   */
  const [references, setReferences] = useState<
    Record<string, RunReferenceList | null>
  >({});
  const [pendingDeletes, setPendingDeletes] = useState<
    Record<string, PendingDelete>
  >({});
  const [deleteFailures, setDeleteFailures] = useState<
    Record<string, DeleteFailure>
  >({});
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [paramsOpen, setParamsOpen] = useState(true);
  const [deriveError, setDeriveError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "ok" | "fail">("idle");
  const [highlightId, setHighlightId] = useState<string | null>(null);

  const mountedRef = useRef(true);
  /** Mirror of pendingDeletes so unmount cleanup can flush the real DELETE. */
  const pendingRef = useRef<Record<string, PendingDelete>>({});
  /** Invalidates in-flight reference lookups when the list is reloaded. */
  const referenceEpochRef = useRef(0);

  const loadReferences = useCallback(async (ids: string[], epoch: number) => {
    const entries = await Promise.all(
      ids.map(async (id): Promise<[string, RunReferenceList | null]> => {
        try {
          const response = await fetchStrategyRunReferences(id);
          if (response.status !== 200) {
            return [id, null];
          }
          return [id, parseRunReferenceList(response.body, id)];
        } catch {
          return [id, null];
        }
      }),
    );
    if (!mountedRef.current || epoch !== referenceEpochRef.current) {
      return;
    }
    setReferences(Object.fromEntries(entries));
  }, []);

  const reload = useCallback(async () => {
    // Every reload starts unknown so a prior success cannot leak forward.
    const epoch = referenceEpochRef.current + 1;
    referenceEpochRef.current = epoch;
    setReferences({});
    try {
      const list = await fetchStrategies();
      if (!mountedRef.current || epoch !== referenceEpochRef.current) {
        return;
      }
      const visible = list.versions;
      setVersions(visible);
      if (focusStrategyId) {
        const hit = visible.find((v) => v.strategy_id === focusStrategyId);
        // Exact match only — never fall back to first row (D5).
        setSelected(hit ?? null);
        setEditing(false);
        if (hit) {
          setParamsOpen(hit.status === "draft");
          setNotice(null);
        } else {
          setNotice(
            `找不到鎖定版本 ${focusStrategyId}（可能已刪或未確認）。`,
          );
        }
      }
      await loadReferences(
        visible.map((v) => v.strategy_id),
        epoch,
      );
    } catch (err) {
      if (!mountedRef.current) {
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [focusStrategyId, loadReferences]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /**
   * Leaving the page / switching tab / unmount = confirm (#21). The grace
   * period is the only undo, so a pending delete must still reach the server.
   */
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const pending = Object.values(pendingRef.current);
      pendingRef.current = {};
      for (const item of pending) {
        clearTimeout(item.timerId);
        void deleteStrategyVersion(item.strategyId, true).catch(() => {
          // Nothing can be shown after unmount; the server remains the truth.
        });
      }
    };
  }, []);

  useEffect(() => {
    const onBeforeUnload = () => {
      for (const item of Object.values(pendingRef.current)) {
        clearTimeout(item.timerId);
        void deleteStrategyVersion(item.strategyId, true).catch(() => {
          /* page is going away */
        });
      }
      pendingRef.current = {};
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, []);

  const openReview = (version: StrategyVersion) => {
    setSelected(version);
    setEditing(false);
    setParamsOpen(version.status === "draft");
    setEditValues({});
    setNotice(null);
    setDeriveError(null);
  };

  const startEdit = () => {
    if (!selected) {
      return;
    }
    const init: Record<string, string> = {};
    for (const row of selected.parameters) {
      if (row.kind === "numeric" && row.path) {
        init[row.path] = row.value;
      }
    }
    setEditValues(init);
    setEditing(true);
    setParamsOpen(true);
    setDeriveError(null);
  };

  /**
   * D2: only numeric leaves the Owner actually changed are sent. An unparsable
   * entry blocks the save instead of being silently dropped.
   */
  const collectPatches = (
    version: StrategyVersion,
  ): {
    patches: StrategyNumericPatch[];
    /** Owner-facing labels for the same rows — paths stay off the screen. */
    changedLabels: string[];
    invalid: string[];
  } => {
    const patches: StrategyNumericPatch[] = [];
    const changedLabels: string[] = [];
    const invalid: string[] = [];
    for (const row of version.parameters) {
      if (row.kind !== "numeric" || !row.path) {
        continue;
      }
      const raw = editValues[row.path];
      if (raw === undefined || raw === row.value) {
        continue;
      }
      const trimmed = raw.trim();
      const value = Number(trimmed);
      if (trimmed === "" || !Number.isFinite(value)) {
        invalid.push(row.label);
        continue;
      }
      if (String(value) === String(Number(row.value))) {
        continue;
      }
      patches.push({ path: row.path, value });
      changedLabels.push(row.label);
    }
    return { patches, changedLabels, invalid };
  };

  const pendingEdit = selected && editing ? collectPatches(selected) : null;
  const saveDisabled =
    busy ||
    pendingEdit === null ||
    pendingEdit.invalid.length > 0 ||
    pendingEdit.patches.length === 0;

  const saveAsNewVersion = async () => {
    if (!selected || !pendingEdit || pendingEdit.patches.length === 0) {
      return;
    }
    const parentId = selected.strategy_id;
    const requestedPaths = pendingEdit.patches.map((p) => p.path);
    setBusy(true);
    setError(null);
    setDeriveError(null);
    setCopyState("idle");
    try {
      const response = await deriveStrategyVersion(parentId, pendingEdit.patches);
      if (!mountedRef.current) {
        return;
      }
      if (response.status === 200 || response.status === 201) {
        const parsed = parseStrategyDerive(
          response.body,
          parentId,
          requestedPaths,
        );
        if (!parsed) {
          // Never invent a local version from an unverifiable response.
          setError(
            "衍生結果未能核實，畫面唔會顯示未經確認嘅新版本；請重新讀取列表。",
          );
          return;
        }
        setEditing(false);
        setNotice(
          parsed.deduplicated
            ? `同一修改已有版本 ${parsed.version.strategy_id}（冇再開新版本）。原版本 ${parentId} 不變。`
            : `已儲存為新版本 ${parsed.version.strategy_id}（來源：Owner UI 微調 · 改咗 ${parsed.changed_count} 項）。原版本 ${parentId} 不變。`,
        );
        setHighlightId(parsed.version.strategy_id);
        await reload();
        if (mountedRef.current) {
          setSelected(parsed.version);
          setParamsOpen(parsed.version.status === "draft");
        }
        return;
      }
      const problem = readProblemDetail(
        response.body,
        `衍生失敗（HTTP ${response.status}）`,
      );
      if (response.status === 422) {
        setDeriveError(problem.text);
        return;
      }
      setError(
        response.status === 409
          ? `未能建立新版本：${problem.text}`
          : response.status === 503
            ? `策略儲存暫時不可用，未有建立新版本：${problem.text}`
            : problem.text,
      );
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
      }
    }
  };

  const dropPending = (strategyId: string) => {
    // Mirror synchronously: an unmount in the same tick must still see truth.
    const next = { ...pendingRef.current };
    delete next[strategyId];
    pendingRef.current = next;
    setPendingDeletes(next);
  };

  /** D3: the grace period is over — this is the real, final delete. */
  const finalizeDelete = async (strategyId: string) => {
    dropPending(strategyId);
    let response;
    try {
      response = await deleteStrategyVersion(strategyId);
    } catch (err) {
      if (mountedRef.current) {
        setDeleteFailures((prev) => ({
          ...prev,
          [strategyId]: {
            message: `暫時刪唔到，冇任何嘢被刪：${
              err instanceof Error ? err.message : String(err)
            }`,
            runIds: [],
          },
        }));
      }
      return;
    }
    if (!mountedRef.current) {
      return;
    }
    if (response.status === 200) {
      if (!parseStrategyDelete(response.body, strategyId)) {
        setDeleteFailures((prev) => ({
          ...prev,
          [strategyId]: {
            message: "刪除結果未能核實；已重新由後端讀取版本列表。",
            runIds: [],
          },
        }));
        await reload();
        return;
      }
      setNotice(`${strategyId} 已刪除並歸檔。`);
      setDeleteFailures((prev) => {
        const next = { ...prev };
        delete next[strategyId];
        return next;
      });
      if (selected?.strategy_id === strategyId) {
        setSelected(null);
        setEditing(false);
      }
      await reload();
      return;
    }
    if (response.status === 404) {
      await reload();
      return;
    }
    const problem = readProblemDetail(
      response.body,
      `刪除失敗（HTTP ${response.status}）`,
    );
    setDeleteFailures((prev) => ({
      ...prev,
      [strategyId]:
        response.status === 409 && problem.blocked
          ? {
              message: `未能刪除：${inUseCopy(
                problem.blocked.standard_run_count,
              )}`,
              runIds: problem.blocked.run_ids,
            }
          : response.status === 409
            ? { message: `未能刪除：${problem.text}`, runIds: [] }
          : response.status === 503
            ? {
                message: `暫時刪唔到，冇任何嘢被刪：${problem.text}`,
                runIds: [],
              }
            : { message: problem.text, runIds: [] },
    }));
  };

  const requestDelete = (version: StrategyVersion) => {
    const guard = strategyDeleteGuard(references[version.strategy_id] ?? null);
    if (guard.kind !== "allowed") {
      return;
    }
    if (pendingDeletes[version.strategy_id]) {
      return;
    }
    setDeleteFailures((prev) => {
      const next = { ...prev };
      delete next[version.strategy_id];
      return next;
    });
    const timerId = setTimeout(() => {
      void finalizeDelete(version.strategy_id);
    }, DELETE_GRACE_MS);
    const next = {
      ...pendingRef.current,
      [version.strategy_id]: {
        strategyId: version.strategy_id,
        name: version.name,
        timerId,
      },
    };
    pendingRef.current = next;
    setPendingDeletes(next);
  };

  const undoDelete = (strategyId: string) => {
    const pending = pendingDeletes[strategyId];
    if (!pending) {
      return;
    }
    clearTimeout(pending.timerId);
    dropPending(strategyId);
    setNotice(`${strategyId} 已復原——冇發出任何刪除。`);
  };

  const copyDeriveError = () => {
    if (!deriveError) {
      return;
    }
    void navigator.clipboard
      .writeText(deriveError)
      .then(() => {
        setCopyState("ok");
      })
      .catch(() => {
        setCopyState("fail");
      });
  };

  return (
    <div className="detail-stack">
      {error ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="library-error"
        >
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="state-msg" role="status" data-testid="library-notice">
          {notice}
        </p>
      ) : null}

      <section className="panel" role="region" aria-label="版本列表">
        <div className="panel__head">
          <h2 className="panel__title">版本庫</h2>
          <InfoButton label="版本庫點用" align="end">
            每個版本都係一份鎖死咗嘅策略定義——回測同模擬盤只認版本，唔認草圖。所以列表冇「改參數」掣：要改，要行「過目」→「編輯參數」兩步，行完會出一個新版本，舊嗰個照樣留低。
          </InfoButton>
        </div>
        <p className="panel__note">
          列表<strong>冇</strong>「改參數」掣——要「過目」→「✎
          編輯參數」兩步（#16）。
        </p>
        {versions.length === 0 ? (
          <p className="state-msg">仲未有版本——去分頁 ② 確認採用一份策略。</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>版本</th>
                  <th>名 ／ 溯源</th>
                  <th>狀態</th>
                  <th>動作</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((version) => {
                  const pending = pendingDeletes[version.strategy_id];
                  const guard = strategyDeleteGuard(
                    references[version.strategy_id] ?? null,
                  );
                  const failure = deleteFailures[version.strategy_id];
                  if (pending) {
                    return (
                      <tr key={version.strategy_id}>
                        <td className="table__mono">{version.strategy_id}</td>
                        <td colSpan={2}>已刪除 · 約 5 秒內可復原</td>
                        <td>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => {
                              undoDelete(version.strategy_id);
                            }}
                          >
                            復原
                          </button>
                        </td>
                      </tr>
                    );
                  }
                  return (
                    <tr
                      key={version.strategy_id}
                      aria-current={
                        highlightId === version.strategy_id ? "true" : undefined
                      }
                      ref={(el) => {
                        if (
                          el &&
                          highlightId === version.strategy_id &&
                          typeof el.scrollIntoView === "function"
                        ) {
                          el.scrollIntoView({ block: "nearest" });
                        }
                      }}
                    >
                      <td className="table__mono table__cell--nowrap">
                        {version.strategy_id}
                      </td>
                      <td>
                        <span title={version.name}>
                          {displayStrategyName(version.name)}
                        </span>{" "}
                        <span className="state-msg">
                          ←{" "}
                          {version.based_on_sketch_origin &&
                          version.based_on_sketch
                            ? `${version.based_on_sketch_origin}/${version.based_on_sketch}`
                            : (version.based_on_sketch ??
                              version.based_on ??
                              "—")}
                        </span>
                      </td>
                      <td>
                        <span
                          className={
                            version.status === "confirmed"
                              ? "chip chip--pass"
                              : "chip"
                          }
                        >
                          {statusLabel(version.status)}
                        </span>
                      </td>
                      <td>
                        <div className="form-actions">
                          <button
                            type="button"
                            className="btn"
                            onClick={() => {
                              openReview(version);
                            }}
                          >
                            過目
                          </button>
                          <button
                            type="button"
                            className="btn btn--danger"
                            disabled={guard.kind !== "allowed"}
                            title={guardTitle(guard)}
                            onClick={() => {
                              requestDelete(version);
                            }}
                          >
                            刪除
                          </button>
                          {guard.kind === "unknown" ? (
                            <span className="state-msg">
                              {GUARD_UNKNOWN_COPY}
                            </span>
                          ) : guard.kind === "in-use" ? (
                            <span
                              className="state-msg"
                              data-testid="delete-guard-copy"
                            >
                              {inUseCopy(guard.count)}
                              <span className="table__mono">
                                {" "}
                                {guard.runIds.join("、")}
                              </span>
                            </span>
                          ) : null}
                          {failure ? (
                            <span
                              className="state-msg state-msg--error"
                              role="alert"
                            >
                              {failure.message}
                              {failure.runIds.length > 0 ? (
                                <span className="table__mono">
                                  {" "}
                                  {failure.runIds.join("、")}
                                </span>
                              ) : null}
                            </span>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected ? (
        <div className="detail-stack" role="region" aria-label="策略詳情">
          <header className="sketch-head">
            <h2 className="panel__title" title={selected.name}>
              過目 {selected.strategy_id} ·{" "}
              {displayStrategyName(selected.name)}
            </h2>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setSelected(null);
                setEditing(false);
              }}
            >
              收起
            </button>
          </header>

          <section className="panel" role="region" aria-label="策略溯源">
            <h3 className="panel__title">鎖定溯源</h3>
            <dl className="kv-list">
              <div>
                <dt>strategy_id</dt>
                <dd className="table__mono">{selected.strategy_id}</dd>
              </div>
              <div>
                <dt>based_on_sketch_origin</dt>
                <dd className="table__mono">
                  {selected.based_on_sketch_origin ?? "—"}
                </dd>
              </div>
              <div>
                <dt>based_on_sketch</dt>
                <dd className="table__mono">
                  {selected.based_on_sketch ?? "—"}
                </dd>
              </div>
            </dl>
          </section>

          <OwnerSketchPanel
            active
            heading="原始草圖（Owner 圖文包）"
            lineage={
              selected.based_on_sketch_origin || selected.based_on_sketch
                ? lineageFromStrategyFields(
                    selected.based_on_sketch_origin,
                    selected.based_on_sketch,
                  )
                : extractBasedOnSketchLineage(selected.source_text)
            }
          />

          <UnquantifiedNotes notes={selected.unquantified_notes} />

          <section className="panel" role="region" aria-label="參數唯讀表">
            <button
              type="button"
              className="fold-header"
              aria-expanded={paramsOpen}
              onClick={() => {
                if (!editing) {
                  setParamsOpen((o) => !o);
                }
              }}
            >
              參數表 ＋ provenance · {selected.parameters.length} 項
              {paramsOpen ? " （收起）" : " （展開）"}
            </button>
            {paramsOpen ? (
              <>
                {!editing ? (
                  <p className="panel__note">
                    {selected.status === "draft"
                      ? "草稿版本默認展開"
                      : "已確認版本默認摺埋"}
                    。改參數要撳「✎ 編輯參數」。
                  </p>
                ) : (
                  <p className="panel__note">
                    只准改<strong>數值</strong>；structures／入市序列唯讀（#17）。儲存＝新版本，唔改{" "}
                    {selected.strategy_id}（#18）。
                  </p>
                )}
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>參數</th>
                        <th>{editing ? "值（可改）" : "值"}</th>
                        {editing ? <th>原值</th> : null}
                        <th>來源</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.parameters.map((row) => {
                        const editable =
                          editing && row.kind === "numeric" && row.path;
                        return (
                          <tr key={`${row.label}-${row.path ?? "s"}`}>
                            <td>{row.label}</td>
                            <td className="table__mono">
                              {editable && row.path ? (
                                <input
                                  className="inp"
                                  type="text"
                                  inputMode="decimal"
                                  aria-label={`編輯 ${row.label}`}
                                  value={editValues[row.path] ?? row.value}
                                  onChange={(e) => {
                                    const path = row.path as string;
                                    setEditValues((prev) => ({
                                      ...prev,
                                      [path]: e.target.value,
                                    }));
                                  }}
                                />
                              ) : (
                                row.value
                              )}
                            </td>
                            {editing ? (
                              <td className="table__mono state-msg">
                                {row.kind === "numeric" ? row.value : "—"}
                              </td>
                            ) : null}
                            <td>
                              {row.kind === "structure" ? (
                                <span className="chip">文件結構 · UI 改唔到</span>
                              ) : (
                                <span className="chip chip--provenance">
                                  {row.source ?? "—"}
                                </span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {editing && pendingEdit ? (
                  <p className="state-msg" data-testid="derive-change-summary">
                    {pendingEdit.invalid.length > 0
                      ? `以下欄位唔係有效數值，改返先可以儲存：${pendingEdit.invalid.join(
                          "、",
                        )}`
                      : pendingEdit.patches.length === 0
                        ? "冇改任何數值——儲存掣關咗。"
                        : `會送出 ${pendingEdit.patches.length} 項改動：${pendingEdit.changedLabels.join(
                            "、",
                          )}`}
                  </p>
                ) : null}
                {deriveError ? (
                  <div
                    className="state-msg state-msg--error"
                    role="alert"
                    data-testid="derive-error"
                  >
                    <p>後端拒絕咗今次改動，冇建立任何新版本：</p>
                    <pre className="code-block" data-testid="derive-error-text">
                      {deriveError}
                    </pre>
                    <div className="form-actions">
                      <button
                        type="button"
                        className="btn"
                        onClick={copyDeriveError}
                      >
                        ⧉ 複製全部錯誤
                      </button>
                      {copyState === "ok" ? (
                        <span className="state-msg">已複製純文字原文</span>
                      ) : copyState === "fail" ? (
                        <span className="state-msg">
                          複製唔到，請手動選取上面文字
                        </span>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                <div className="form-actions">
                  {!editing ? (
                    <button
                      type="button"
                      className="btn"
                      onClick={startEdit}
                    >
                      ✎ 編輯參數
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn btn--primary"
                        disabled={saveDisabled}
                        onClick={() => {
                          void saveAsNewVersion();
                        }}
                      >
                        儲存為新版本
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => {
                          setEditing(false);
                          setDeriveError(null);
                        }}
                      >
                        取消
                      </button>
                    </>
                  )}
                  {selected.status === "draft" && !editing ? (
                    <button
                      type="button"
                      className="btn btn--primary"
                      disabled={busy}
                      onClick={() => {
                        void (async () => {
                          setBusy(true);
                          try {
                            const c = await confirmStrategy(
                              selected.strategy_id,
                            );
                            setSelected(c);
                            setNotice(`${c.strategy_id} 已確認`);
                            await reload();
                          } catch (err) {
                            setError(
                              err instanceof Error ? err.message : String(err),
                            );
                          } finally {
                            setBusy(false);
                          }
                        })();
                      }}
                    >
                      ✓ 確認採用
                    </button>
                  ) : null}
                </div>
              </>
            ) : null}
          </section>

          <details className="fold">
            <summary>YAML 原文 source_text</summary>
            <pre className="code-block" data-testid="library-source-text">
              {selected.source_text}
            </pre>
          </details>
        </div>
      ) : null}
    </div>
  );
}
