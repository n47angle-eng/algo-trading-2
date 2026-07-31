import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  postBacktestPreview,
  type PreviewHttpResult,
} from "../../api/client";
import { ChartGrid } from "../chart/ChartGrid";
import { formatUtcCounterpart } from "../../lib/backtest/time";
import type { InstrumentCatalogRow } from "../../lib/catalog/types";
import {
  buildPreviewRequest,
  eligiblePreviewContracts,
  interpretPreviewFunnel,
  parsePreviewEnvelope,
  previewToChartPanes,
  primaryPreviewContractId,
  type PreviewEnvelope,
} from "../../lib/preview/contract";
import { createOwnerReviewPreviewFixture } from "../../lib/preview/ownerReviewFixture";
import type { StrategyUniverse } from "../../lib/strategy/universe";
import { ValidationReport } from "./ValidationReport";

interface PreviewPanelProps {
  sourceText: string;
  filename: string | null;
  validatedOk: boolean;
  universeClientOk: boolean;
  universe: StrategyUniverse | null;
  catalogRows: InstrumentCatalogRow[] | null;
  ownerReview: boolean;
  resetKey: number;
}

interface PreviewFormState {
  rangeStartLocal: string;
  rangeEndLocal: string;
  commissionText: string;
  slippageText: string;
}

const NORMAL_FORM: PreviewFormState = {
  rangeStartLocal: "",
  rangeEndLocal: "",
  commissionText: "",
  slippageText: "",
};

const OWNER_REVIEW_FORM: PreviewFormState = {
  rangeStartLocal: "2026-07-01T09:30",
  rangeEndLocal: "2026-07-22T16:00",
  commissionText: "2.5",
  slippageText: "1",
};

const CONDITION_LABELS: Record<string, string> = {
  daily_regime_missing: "Daily 市況未達標",
  daily_regime_not_trend: "Daily 市況唔係趨勢",
  mid_direction: "1H 方向未對齊",
  mid_pullback: "1H 回踩未達標",
  entry_pullback: "5m 回踩未達標",
  entry_signal: "5m 入市訊號未形成",
  execution_fill: "執行層未成交",
};

function conditionLabel(id: string): string {
  return CONDITION_LABELS[id] ?? "未有翻譯";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function PreviewPanel({
  sourceText,
  filename,
  validatedOk,
  universeClientOk,
  universe,
  catalogRows,
  ownerReview,
  resetKey,
}: PreviewPanelProps) {
  const [form, setForm] = useState<PreviewFormState>(
    ownerReview ? OWNER_REVIEW_FORM : NORMAL_FORM,
  );
  const [contractId, setContractId] = useState("");
  const [busy, setBusy] = useState(false);
  const [envelope, setEnvelope] = useState<PreviewEnvelope | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const eligible = useMemo(
    () => eligiblePreviewContracts(universe, catalogRows),
    [universe, catalogRows],
  );
  const eligibleIdentity = eligible
    .map((row) => `${row.symbol}\u0000${row.contractId}`)
    .join("\u0001");
  const primaryDefault = useMemo(
    () => primaryPreviewContractId(universe, eligible),
    [universe, eligible],
  );

  const invalidatePreview = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    inFlightRef.current = false;
    setBusy(false);
    setEnvelope(null);
    setRequestError(null);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
      inFlightRef.current = false;
    };
  }, []);

  useEffect(() => {
    setForm(ownerReview ? OWNER_REVIEW_FORM : NORMAL_FORM);
    setContractId(primaryDefault);
    invalidatePreview();
  }, [
    ownerReview,
    resetKey,
    sourceText,
    filename,
    primaryDefault,
    eligibleIdentity,
    invalidatePreview,
  ]);

  const requestResult = useMemo(
    () =>
      buildPreviewRequest({
        sourceText,
        filename,
        contractId,
        rangeStartLocal: form.rangeStartLocal,
        rangeEndLocal: form.rangeEndLocal,
        commissionText: form.commissionText,
        slippageText: form.slippageText,
      }),
    [sourceText, filename, contractId, form],
  );

  const selectedContractIsEligible = eligible.some(
    (row) => row.contractId === contractId,
  );
  const canPreview =
    validatedOk &&
    universeClientOk &&
    universe !== null &&
    catalogRows !== null &&
    selectedContractIsEligible &&
    requestResult.ok &&
    !busy;

  const runPreview = useCallback(async () => {
    if (inFlightRef.current) {
      return;
    }
    const built = buildPreviewRequest({
      sourceText,
      filename,
      contractId,
      rangeStartLocal: form.rangeStartLocal,
      rangeEndLocal: form.rangeEndLocal,
      commissionText: form.commissionText,
      slippageText: form.slippageText,
    });
    if (
      !validatedOk ||
      !universeClientOk ||
      !universe ||
      !selectedContractIsEligible ||
      !built.ok
    ) {
      setEnvelope(null);
      setRequestError(
        built.ok
          ? "驗證、catalog 或 universe gate 未通過。"
          : built.reason,
      );
      return;
    }

    const requestGeneration = generationRef.current + 1;
    generationRef.current = requestGeneration;
    const controller = new AbortController();
    controllerRef.current = controller;
    inFlightRef.current = true;
    setBusy(true);
    setEnvelope(null);
    setRequestError(null);

    try {
      const response: PreviewHttpResult = ownerReview
        ? {
            status: 200,
            body: createOwnerReviewPreviewFixture(
              built.request.contract_id,
              universe.session,
            ),
          }
        : await postBacktestPreview(built.request, controller.signal);
      if (
        !mountedRef.current ||
        requestGeneration !== generationRef.current
      ) {
        return;
      }
      const parsed = parsePreviewEnvelope(response.status, response.body, {
        contractId: built.request.contract_id,
        sessionName: universe.session,
      });
      if (!parsed.ok) {
        setEnvelope(null);
        setRequestError(`預覽回應未通過安全檢查：${parsed.reason}`);
        return;
      }
      setEnvelope(parsed.value);
    } catch (error) {
      if (
        isAbortError(error) ||
        !mountedRef.current ||
        requestGeneration !== generationRef.current
      ) {
        return;
      }
      setEnvelope(null);
      setRequestError(
        `預覽暫時連唔到：${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    } finally {
      if (
        mountedRef.current &&
        requestGeneration === generationRef.current
      ) {
        inFlightRef.current = false;
        controllerRef.current = null;
        setBusy(false);
      }
    }
  }, [
    sourceText,
    filename,
    contractId,
    form,
    validatedOk,
    universeClientOk,
    universe,
    selectedContractIsEligible,
    ownerReview,
  ]);

  useEffect(() => {
    // Explicit action only: validation/gate changes may invalidate, never POST.
    if (!validatedOk || !universeClientOk || !contractId) {
      invalidatePreview();
    }
  }, [
    validatedOk,
    universeClientOk,
    contractId,
    invalidatePreview,
  ]);

  const changeForm = (patch: Partial<PreviewFormState>) => {
    invalidatePreview();
    setForm((current) => ({ ...current, ...patch }));
  };

  const success = envelope?.kind === "success" ? envelope : null;
  const failure = envelope?.kind === "failure" ? envelope : null;
  const panes = useMemo(
    () => (success ? previewToChartPanes(success) : []),
    [success],
  );
  const anchoredRejectCount = panes.reduce(
    (count, pane) => count + (pane.verticalAnnotations?.length ?? 0),
    0,
  );

  return (
    <section
      className="panel preview-panel"
      role="region"
      aria-label="試跑預覽"
      data-testid="preview-panel"
    >
      <div className="preview-panel__head">
        <h2 className="panel__title">試跑預覽</h2>
        <span className="chip">dry-run · 唔存 artifact · 唔佔 run 編號</span>
      </div>
      <p className="panel__note">
        只會喺你明確撳掣後跑一次。未有 exact 最近 20 個交易日清單，所以 normal
        mode 唔會用電腦今日或 calendar-day 猜範圍。
      </p>

      <div className="preview-form-grid">
        <label className="field">
          <span>Exact contract</span>
          <select
            className="inp"
            aria-label="預覽 exact contract"
            data-testid="preview-contract"
            value={contractId}
            onChange={(event) => {
              invalidatePreview();
              setContractId(event.target.value);
            }}
          >
            <option value="">請明確揀 contract</option>
            {eligible.map((row) => (
              <option key={row.contractId} value={row.contractId}>
                {row.displayName}（{row.symbol}） · {row.contractId}
              </option>
            ))}
          </select>
          <small className="utc-hint">
            {primaryDefault
              ? "Primary 只有一個合資格 exact contract，已預選。"
              : "Primary 有零個或多個候選，系統唔會靠 symbol 猜。"}
          </small>
        </label>

        <label className="field">
          <span>開始（本地時間）</span>
          <input
            className="inp"
            type="datetime-local"
            step="1"
            aria-label="預覽開始時間"
            data-testid="preview-range-start"
            value={form.rangeStartLocal}
            onChange={(event) => {
              changeForm({ rangeStartLocal: event.target.value });
            }}
          />
          <small className="utc-hint">
            UTC：{formatUtcCounterpart(form.rangeStartLocal)}
          </small>
        </label>

        <label className="field">
          <span>結束（本地時間）</span>
          <input
            className="inp"
            type="datetime-local"
            step="1"
            aria-label="預覽結束時間"
            data-testid="preview-range-end"
            value={form.rangeEndLocal}
            onChange={(event) => {
              changeForm({ rangeEndLocal: event.target.value });
            }}
          />
          <small className="utc-hint">
            UTC：{formatUtcCounterpart(form.rangeEndLocal)}
          </small>
        </label>

        <label className="field">
          <span>初始資金（USD）</span>
          <input
            className="inp"
            aria-label="預覽初始資金"
            value="100000"
            readOnly
          />
          <small className="utc-hint">已批准 baseline：USD 100,000</small>
        </label>

        <label className="field">
          <span>每邊佣金（USD）</span>
          <input
            className="inp"
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            aria-label="預覽每邊佣金"
            data-testid="preview-commission"
            value={form.commissionText}
            onChange={(event) => {
              changeForm({ commissionText: event.target.value });
            }}
          />
        </label>

        <label className="field">
          <span>滑點（ticks）</span>
          <input
            className="inp"
            type="number"
            min="0"
            step="1"
            inputMode="numeric"
            aria-label="預覽滑點 ticks"
            data-testid="preview-slippage"
            value={form.slippageText}
            onChange={(event) => {
              changeForm({ slippageText: event.target.value });
            }}
          />
        </label>
      </div>

      {!requestResult.ok ? (
        <p className="state-msg" data-testid="preview-form-reason">
          未可試跑：{requestResult.reason}
        </p>
      ) : null}

      <div className="form-actions">
        <button
          type="button"
          className="btn btn--primary"
          data-testid="run-preview"
          disabled={!canPreview}
          title={
            canPreview
              ? "只跑一次 dry-run preview"
              : "先通過驗證／universe gate，再填齊 exact contract、範圍、佣金同滑點"
          }
          onClick={() => {
            void runPreview();
          }}
        >
          {busy ? "試跑中…" : "▶ 試跑預覽"}
        </button>
        <span className="state-msg">
          確認採用前 import／confirm／run／artifact 寫入保持 0。
        </span>
      </div>

      {requestError ? (
        <p
          className="state-msg state-msg--error"
          role="alert"
          data-testid="preview-fail-closed"
        >
          {requestError}
        </p>
      ) : null}

      {failure ? (
        <div
          className="preview-result preview-result--failure"
          data-testid="preview-422"
          data-preview-persisted="false"
        >
          <h3>預覽未完成 · persisted:false · 零寫入</h3>
          {!failure.validation.valid ? (
            <ValidationReport report={failure.validation} />
          ) : (
            <p className="state-msg">
              四層驗證已通過；今次係 engine／資料層未完成預覽。
            </p>
          )}
          <ul className="preview-message-list" aria-label="預覽錯誤">
            {failure.errors.map((message, index) => (
              <li key={`${index}-${message}`}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {success ? (
        <div
          className="preview-result"
          role="region"
          aria-label="預覽結果"
          data-testid="preview-success"
          data-preview-persisted="false"
        >
          <div className="preview-result__head">
            <h3>預覽完成 · dry-run</h3>
            <span className="chip chip--ok">persisted:false · 無 run identity</span>
          </div>
          <p className="utc-hint">
            request sha256：<code>{success.requestFingerprint.digest}</code>
          </p>

          <div className="preview-funnel-grid">
            <section
              className="preview-scale"
              aria-label="日級漏斗"
              data-preview-scale="daily"
            >
              <h4>日級</h4>
              <dl>
                <div>
                  <dt>Daily 趨勢閘</dt>
                  <dd>{success.funnel.daily_trend_days}</dd>
                </div>
              </dl>
              <p>單位：交易日</p>
            </section>

            <section
              className="preview-scale"
              aria-label="評估級漏斗"
              data-preview-scale="evaluation"
            >
              <h4>評估級</h4>
              <dl>
                <div>
                  <dt>過 Daily 閘</dt>
                  <dd>
                    {success.funnel.evaluations_passing_daily_gate}
                  </dd>
                </div>
                <div>
                  <dt>過中層閘</dt>
                  <dd>{success.funnel.evaluations_passing_mid_gate}</dd>
                </div>
                <div>
                  <dt>入市訊號</dt>
                  <dd>{success.funnel.signals_created}</dd>
                </div>
              </dl>
              <p>單位：5m 評估次數</p>
            </section>

            <section
              className="preview-scale"
              aria-label="成交漏斗"
              data-preview-scale="fills"
            >
              <h4>成交</h4>
              <dl>
                <div>
                  <dt>成交</dt>
                  <dd>{success.funnel.fills}</dd>
                </div>
              </dl>
              <p>單位：筆</p>
            </section>
          </div>

          <section
            className="preview-interpretation"
            aria-label="系統解讀"
          >
            <h4>系統解讀</h4>
            <p>{interpretPreviewFunnel(success.funnel)}</p>
          </section>

          <div className="preview-count-grid">
            <section aria-label="Reject reasons">
              <h4>Reject reasons</h4>
              {Object.keys(success.funnel.reject_reasons).length === 0 ? (
                <p className="state-msg">0 項</p>
              ) : (
                <ul>
                  {Object.entries(success.funnel.reject_reasons).map(
                    ([id, count]) => (
                      <li key={id}>
                        {conditionLabel(id)} · <code>{id}</code>：{count}
                      </li>
                    ),
                  )}
                </ul>
              )}
            </section>
            <section aria-label="Blocking condition counts">
              <h4>Blocking condition counts</h4>
              {Object.keys(
                success.evidenceSummary.blocking_condition_counts,
              ).length === 0 ? (
                <p className="state-msg">0 項</p>
              ) : (
                <ul>
                  {Object.entries(
                    success.evidenceSummary.blocking_condition_counts,
                  ).map(([id, count]) => (
                    <li key={id}>
                      {conditionLabel(id)} · <code>{id}</code>：{count}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <section
            className="preview-chart-section"
            aria-label="D 1H 30m 5m 四格圖"
          >
            <div className="preview-chart-section__head">
              <h4>D／1H／30m／5m 四格圖</h4>
              <span className="state-msg">
                Backend candles／EMA／markers／levels 原值；冇由文字反推。
              </span>
            </div>
            <ChartGrid panes={panes} />
            <p className="panel__note" data-testid="preview-overlay-evidence">
              Reject 位置只用已驗證 evidence 真 timestamp：
              {anchoredRejectCount} 個。
              {anchoredRejectCount < success.rejectionEvidence.length
                ? ` 另外 ${
                    success.rejectionEvidence.length - anchoredRejectCount
                  } 個缺可識別 layer，明示 unavailable，冇畫假位置。`
                : null}
              {" "}Daily payload 冇逐日 structured timestamps，所以冇畫假 trend
              日著色。
            </p>
          </section>

          {success.warnings.length > 0 ? (
            <ul className="preview-message-list" aria-label="預覽 warnings">
              {success.warnings.map((message, index) => (
                <li key={`${index}-${message}`}>{message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
