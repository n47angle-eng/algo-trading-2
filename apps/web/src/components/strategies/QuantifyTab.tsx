import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ChangeEvent } from "react";

import {
  StrategyImportValidationError,
  type StrategyValidationResponse,
  confirmStrategy,
  importStrategy,
  validateStrategy,
} from "../../api/client";
import { InfoButton } from "../ui/InfoButton";
import { useWorkbench } from "../../lib/p2/WorkbenchContext";
import {
  extractBasedOnSketchLineage,
  extractProvenance,
  extractRationale,
  extractUnquantifiedNotes,
  parseYamlDocument,
} from "../../lib/strategyYaml";
import {
  parseStrategyUniverse,
  sketchRefFromLoadState,
  validateUniverseGates,
} from "../../lib/strategy/universe";
import { useOwnerSketchLoad } from "../../lib/sketch/useOwnerSketchLoad";
import { YamlTree, type YamlNode } from "../../lib/yamlTree";
import { OwnerSketchPanel } from "./OwnerSketchPanel";
import { PreviewPanel } from "./PreviewPanel";
import { UnquantifiedNotes } from "./UnquantifiedNotes";
import { UniversePanel } from "./UniversePanel";
import { ValidationReport } from "./ValidationReport";

interface QuantifyTabProps {
  onGoLibrary: () => void;
  fixtureEpoch?: number;
}

interface ValidationIdentity {
  sourceText: string;
  filename: string | null;
}

/**
 * Tab ② — import / validate / origin-aware compare / whole-universe / confirm.
 * Constraint #15: no store write until confirm.
 * Owner-review: no P2 business API (validate/import/confirm/sketch fetch).
 */
export function QuantifyTab({
  onGoLibrary,
  fixtureEpoch = 0,
}: QuantifyTabProps) {
  const {
    ownerReview,
    catalog,
    sketchDetailLookup,
    quantifySeedYaml,
    setQuantifySeedYaml,
  } = useWorkbench();
  const [sourceText, setSourceText] = useState("");
  const [filename, setFilename] = useState<string | null>(null);
  const [report, setReport] = useState<StrategyValidationResponse | null>(null);
  const [validatedOk, setValidatedOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [writeCount, setWriteCount] = useState(0);
  const validationGenerationRef = useRef(0);
  const validationIdentityRef = useRef<ValidationIdentity>({
    sourceText: "",
    filename: null,
  });
  const validationBusyGenerationRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const replaceSourceIdentity = useCallback(
    (nextSourceText: string, nextFilename: string | null) => {
      const nextGeneration = validationGenerationRef.current + 1;
      validationGenerationRef.current = nextGeneration;
      validationIdentityRef.current = {
        sourceText: nextSourceText,
        filename: nextFilename,
      };
      if (validationBusyGenerationRef.current !== null) {
        validationBusyGenerationRef.current = null;
        setBusy(false);
      }
      setSourceText(nextSourceText);
      setFilename(nextFilename);
      setReport(null);
      setValidatedOk(false);
      setError(null);
      setNotice(null);
      return nextGeneration;
    },
    [],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      validationGenerationRef.current += 1;
      validationBusyGenerationRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (quantifySeedYaml) {
      replaceSourceIdentity(
        quantifySeedYaml,
        `fixture-${fixtureEpoch}.yaml`,
      );
      setNotice("已載入 fixture YAML——撳「驗證」睇結果。");
      setQuantifySeedYaml(null);
    }
  }, [
    quantifySeedYaml,
    fixtureEpoch,
    replaceSourceIdentity,
    setQuantifySeedYaml,
  ]);

  const lineage = useMemo(
    () =>
      sourceText.trim() ? extractBasedOnSketchLineage(sourceText) : null,
    [sourceText],
  );

  // Single origin-aware load for left panel + gates (D9)
  const sketchLoad = useOwnerSketchLoad(
    lineage,
    Boolean(sourceText.trim()),
    sketchDetailLookup,
  );
  const sketchRef = useMemo(
    () => sketchRefFromLoadState(sketchLoad),
    [sketchLoad],
  );

  const parsedDoc = useMemo(() => {
    if (!sourceText.trim() || !validatedOk) {
      return null;
    }
    try {
      return parseYamlDocument(sourceText) as YamlNode;
    } catch {
      return null;
    }
  }, [sourceText, validatedOk]);

  const universeParse = useMemo(() => {
    if (!sourceText.trim()) {
      return null;
    }
    try {
      const doc = parseYamlDocument(sourceText);
      return parseStrategyUniverse(doc);
    } catch {
      return {
        ok: false as const,
        issues: [
          {
            path: "universe",
            message: "策略 YAML 無法解析。",
            fix: "修正 YAML 語法。",
          },
        ],
      };
    }
  }, [sourceText]);

  const catalogRows = catalog.status === "ready" ? catalog.rows : null;

  const gateIssues = useMemo(() => {
    if (!universeParse || !universeParse.ok) {
      return [];
    }
    return validateUniverseGates(
      universeParse.universe,
      catalogRows,
      sketchRef,
    );
  }, [universeParse, catalogRows, sketchRef]);

  const universeClientOk =
    Boolean(universeParse?.ok) &&
    gateIssues.length === 0 &&
    catalog.status === "ready" &&
    sketchLoad.kind !== "loading" &&
    sketchLoad.kind !== "error" &&
    sketchLoad.kind !== "lineage";

  const canConfirm = validatedOk && universeClientOk && !busy;

  const sketchPrimary =
    sketchRef.kind === "ready"
      ? {
          instrument: sketchRef.instrument,
          assetClass: sketchRef.assetClass,
        }
      : null;

  const notes = useMemo(
    () => (sourceText.trim() ? extractUnquantifiedNotes(sourceText) : []),
    [sourceText],
  );
  const rationale = useMemo(
    () => (sourceText.trim() ? extractRationale(sourceText) : ""),
    [sourceText],
  );
  const provenance = useMemo(
    () => (sourceText.trim() ? extractProvenance(sourceText) : []),
    [sourceText],
  );

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const readGeneration = replaceSourceIdentity("", file.name);
    setNotice(`正在讀入 ${file.name}…`);
    void (async () => {
      try {
        const fileText = await file.text();
        if (
          !mountedRef.current ||
          validationGenerationRef.current !== readGeneration ||
          validationIdentityRef.current.filename !== file.name
        ) {
          return;
        }
        replaceSourceIdentity(fileText, file.name);
        setNotice(`已讀入 ${file.name}——撳「驗證」睇結果。`);
      } catch (err) {
        if (
          mountedRef.current &&
          validationGenerationRef.current === readGeneration
        ) {
          setNotice(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    event.target.value = "";
  };

  const runValidation = useCallback(async () => {
    const requestIdentity: ValidationIdentity = {
      sourceText,
      filename,
    };
    const requestGeneration = validationGenerationRef.current + 1;
    validationGenerationRef.current = requestGeneration;
    validationBusyGenerationRef.current = requestGeneration;
    const acceptsValidationResult = () =>
      mountedRef.current &&
      requestGeneration === validationGenerationRef.current &&
      validationIdentityRef.current.sourceText ===
        requestIdentity.sourceText &&
      validationIdentityRef.current.filename === requestIdentity.filename;

    setBusy(true);
    setError(null);
    setNotice(null);
    setReport(null);
    setValidatedOk(false);
    try {
      if (ownerReview) {
        try {
          parseYamlDocument(requestIdentity.sourceText);
          if (!acceptsValidationResult()) {
            return;
          }
          setReport(null);
          setValidatedOk(true);
          setNotice(
            "Owner-review：YAML 可解析（唔 call backend validate）。請睇 universe gates。",
          );
        } catch (err) {
          if (!acceptsValidationResult()) {
            return;
          }
          setError(err instanceof Error ? err.message : String(err));
        }
        return;
      }
      const result = await validateStrategy(requestIdentity.sourceText);
      if (!acceptsValidationResult()) {
        return;
      }
      setReport(result.valid ? null : result);
      setValidatedOk(result.valid);
      if (result.valid) {
        setNotice(
          "四層驗證通過——請再睇 universe 對照。確認之前系統零寫入。",
        );
      }
    } catch (err) {
      if (!acceptsValidationResult()) {
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (
        acceptsValidationResult() &&
        validationBusyGenerationRef.current === requestGeneration
      ) {
        validationBusyGenerationRef.current = null;
        setBusy(false);
      }
    }
  }, [sourceText, filename, ownerReview]);

  const runConfirmAdopt = useCallback(async () => {
    if (!canConfirm) {
      setError("Universe gates 未通過——確認 disabled，零寫入。");
      return;
    }
    if (ownerReview) {
      setNotice(
        "Owner-review 模式：唔會 import/confirm（零寫入、唔 call P2 write API）。",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const imported = await importStrategy(sourceText, filename ?? undefined);
      setWriteCount((c) => c + 1);
      const confirmed = await confirmStrategy(imported.version.strategy_id);
      setWriteCount((c) => c + 1);
      setNotice(
        `已確認採用 ${confirmed.strategy_id}（${confirmed.name}）——已入版本庫。`,
      );
      setValidatedOk(false);
      onGoLibrary();
    } catch (err) {
      if (err instanceof StrategyImportValidationError) {
        setReport(err.report);
        setValidatedOk(false);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  }, [canConfirm, ownerReview, sourceText, filename, onGoLibrary]);

  // Map sketchRef to UniversePanel localSketch-compatible shape
  const localSketchForPanel = useMemo(() => {
    if (sketchRef.kind === "missing") {
      return {
        kind: "missing" as const,
        sketchId: sketchRef.sketchId,
        origin: sketchRef.origin,
      };
    }
    if (sketchRef.kind === "legacy_incomplete") {
      return {
        kind: "legacy_incomplete" as const,
        sketchId: sketchRef.sketchId,
        reason: sketchRef.reason,
      };
    }
    if (sketchRef.kind === "ready") {
      return {
        kind: "present" as const,
        draft: {
          instrument: sketchRef.instrument,
          assetClass: sketchRef.assetClass,
        },
      };
    }
    return { kind: "none" as const };
  }, [sketchRef]);

  return (
    <div className="detail-stack" data-write-count={writeCount}>
      {error ? (
        <p className="state-msg state-msg--error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="state-msg" role="status">
          {notice}
        </p>
      ) : null}

      <section className="panel" role="region" aria-label="匯入策略文件">
        <div className="panel__head">
          <h2 className="panel__title">匯入 AI 量化結果</h2>
          <InfoButton label="量化確認點做" align="end">
            呢步係「你講嘅嘢，機器點理解」。撳「驗證」只係檢查，唔會寫入任何嘢；只有撳「確認整份策略」先會生成一個版本入版本庫。版本一經確認就改唔到，要改就開新版本。
          </InfoButton>
        </div>
        <p className="panel__note">
          驗證唔會寫入系統。只有撳「確認整份策略」才會入版本庫（#15／#31）。
          {ownerReview
            ? " Owner-review：唔 call validate/import/confirm／coverage。"
            : null}
        </p>
        <div className="form-actions">
          <label className="btn" htmlFor="quantify-yaml-file">
            ⬆ 揀 YAML 檔
          </label>
          <input
            id="quantify-yaml-file"
            className="visually-hidden"
            type="file"
            accept=".yaml,.yml,text/yaml,text/plain"
            onChange={onFile}
          />
          <span className="state-msg">{filename ?? "或者直接貼文字"}</span>
        </div>
        <label className="field">
          <span>strategy.v1 YAML</span>
          <textarea
            className="inp inp--area"
            rows={10}
            spellCheck={false}
            value={sourceText}
            aria-label="strategy.v1 YAML"
            placeholder="schema: strategy.v1&#10;meta:&#10;  name: …"
            onChange={(e) => {
              replaceSourceIdentity(e.target.value, filename);
            }}
          />
        </label>
        <div className="form-actions">
          <button
            type="button"
            className="btn"
            disabled={busy || !sourceText.trim()}
            onClick={() => {
              void runValidation();
            }}
          >
            驗證
          </button>
        </div>
      </section>

      {report && !report.valid ? <ValidationReport report={report} /> : null}

      {validatedOk || (sourceText.trim() && universeParse) ? (
        <UniversePanel
          universe={universeParse?.ok ? universeParse.universe : null}
          parseIssues={
            universeParse && !universeParse.ok ? universeParse.issues : []
          }
          gateIssues={gateIssues}
          catalogRows={catalogRows}
          localSketch={localSketchForPanel}
          sketchPrimary={sketchPrimary}
        />
      ) : null}

      {validatedOk ? (
        <>
          <div className="compare-split" role="region" aria-label="左右對照">
            <OwnerSketchPanel
              lineage={lineage}
              active={validatedOk}
              heading="左：我想講嘅"
              loadState={sketchLoad}
              fixtureLookup={sketchDetailLookup}
            />

            <section className="panel" aria-label="右邊 AI 聽到嘅">
              <h2 className="panel__title">右：AI 聽到嘅</h2>
              <p className="panel__note">
                來源：匯入 YAML · <strong>一個字都唔係 Owner 打</strong>
              </p>
              {parsedDoc ? (
                <div role="region" aria-label="參數樹">
                  <YamlTree data={parsedDoc} />
                </div>
              ) : (
                <p className="state-msg">無法解析 YAML 樹</p>
              )}
              <UnquantifiedNotes notes={notes} />
              <details className="fold">
                <summary>rationale（可摺）</summary>
                <p className="prose">{rationale || "—"}</p>
              </details>
              <details className="fold">
                <summary>provenance · {provenance.length} 項（可摺）</summary>
                <pre className="code-block">
                  {JSON.stringify(provenance, null, 2)}
                </pre>
              </details>
              <details className="fold">
                <summary>YAML 原文（可摺）</summary>
                <pre className="code-block">{sourceText}</pre>
              </details>
            </section>
          </div>

          <PreviewPanel
            sourceText={sourceText}
            filename={filename}
            validatedOk={validatedOk}
            universeClientOk={universeClientOk}
            universe={universeParse?.ok ? universeParse.universe : null}
            catalogRows={catalogRows}
            ownerReview={ownerReview}
            resetKey={fixtureEpoch}
          />

          <div className="form-actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={!canConfirm}
              data-testid="confirm-adopt"
              title={
                canConfirm
                  ? "確認整份策略"
                  : "Universe gates 未通過或未驗證"
              }
              onClick={() => {
                void runConfirmAdopt();
              }}
            >
              ✓ 確認整份策略 → 入版本庫
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              data-testid="reject-strategy"
              onClick={() => {
                setValidatedOk(false);
                setNotice(
                  "已退回——請返 Terminal 修改整份策略，改完 YAML 再貼入嚟驗證。",
                );
              }}
            >
              唔同意，返 Terminal 修改整份策略
            </button>
          </div>
          {!universeClientOk ? (
            <p className="panel__note" role="status">
              Universe fail-closed：confirm disabled，import/confirm 寫入保持 0。
            </p>
          ) : (
            <p className="panel__note">
              確認前 import/confirm write-count 為 0。
            </p>
          )}
        </>
      ) : null}

      <span className="visually-hidden" data-testid="pre-confirm-write-count">
        {writeCount}
      </span>
    </div>
  );
}
