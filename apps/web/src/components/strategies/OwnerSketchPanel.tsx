/**
 * Owner original sketch package (left compare / library lineage).
 * Server truth only — never rebuilt from strategy.v1 / localStorage id alone.
 */
import {
  resolveApiUrl,
  type SketchDetailResponse,
} from "../../api/client";
import type { SketchLineage } from "../../lib/strategyYaml";
import {
  useOwnerSketchLoad,
  type OwnerSketchLoadState,
  type SketchDetailLookup,
} from "../../lib/sketch/useOwnerSketchLoad";

export type { OwnerSketchLoadState };

interface OwnerSketchPanelProps {
  lineage: SketchLineage | null;
  /** When true, load sketch; false resets idle. */
  active: boolean;
  heading?: string;
  /**
   * Shared load state from parent (Quantify left+gates single truth).
   * When provided, panel does not fetch.
   */
  loadState?: OwnerSketchLoadState;
  /** Owner-review: fixture detail lookup (no HTTP). */
  fixtureLookup?: SketchDetailLookup | null;
}

function ServerSketchBody({ detail }: { detail: SketchDetailResponse }) {
  const { meta, images } = detail;
  const byFile = new Map(images.map((im) => [im.file, im]));
  return (
    <div data-testid="sketch-ready">
      <p className="state-msg">
        {meta.title}
        <span className="utc-hint">
          {" "}
          ·{" "}
          <code>
            {meta.origin}/{meta.sketch_id}
          </code>
        </span>
      </p>
      {meta.instrument ? (
        <p className="state-msg" data-testid="sketch-instrument-class">
          instrument: <code>{meta.instrument}</code>
          {meta.asset_class ? (
            <>
              {" "}
              · asset_class: <code>{meta.asset_class}</code>
            </>
          ) : null}
        </p>
      ) : null}
      <div
        className="sketch-grid sketch-grid--readonly"
        data-testid="sketch-four-grid"
      >
        {meta.charts.map((c) => {
          const img = byFile.get(c.file);
          const src = img ? resolveApiUrl(img.url) : null;
          const alt = c.role
            ? `${c.timeframe} · ${c.role} · ${meta.title}`
            : `${c.timeframe} · ${meta.title}`;
          return (
            <article
              key={c.file}
              className="sketch-cell"
              aria-label={`草圖格 ${c.timeframe}`}
            >
              <div className="sketch-cell__meta">
                <strong>
                  {c.timeframe}
                  {c.role ? ` · ${c.role}` : ""}
                </strong>
                {c.indicators_shown?.length ? (
                  <span className="utc-hint">
                    {" "}
                    · {c.indicators_shown.join(", ")}
                  </span>
                ) : null}
              </div>
              {src ? (
                <img className="sketch-cell__img" src={src} alt={alt} />
              ) : (
                <p className="state-msg">（呢格冇圖）</p>
              )}
              <p className="prose" data-source="sketch-owner-view">
                {c.owner_view || "（未填判斷）"}
              </p>
            </article>
          );
        })}
      </div>
      {meta.rationale ? (
        <div className="field">
          <span>整體理據 rationale</span>
          <p className="prose" data-source="sketch-rationale">
            {meta.rationale}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function SketchStates({ state }: { state: OwnerSketchLoadState }) {
  if (state.kind === "idle") {
    return <p className="state-msg">未有策略內容可對照。</p>;
  }
  if (state.kind === "lineage") {
    return (
      <div
        className="panel panel--alert"
        role="status"
        aria-label="草圖溯源不完整"
        data-testid="sketch-lineage-error"
      >
        <p className="state-msg state-msg--error">
          <strong>引用層問題：</strong>
          {state.reason}
        </p>
        <p className="panel__note">
          呢個唔係「本機缺草圖」。補齊 YAML 溯源後再驗證。
        </p>
      </div>
    );
  }
  if (state.kind === "loading") {
    return (
      <p className="state-msg" role="status" data-testid="sketch-loading">
        正在讀取草圖{" "}
        <code>
          {state.origin}/{state.sketchId}
        </code>
        …
      </p>
    );
  }
  if (state.kind === "not_found") {
    return (
      <p
        className="state-msg state-msg--error"
        role="status"
        data-testid="sketch-not-found"
        aria-label="本機缺少原始草圖"
      >
        本機缺少呢份原始草圖（
        <code>
          {state.origin}/{state.sketchId}
        </code>
        ）。策略仍可按後端驗證結果確認採用——呢個<strong>唔係</strong>
        引用層格式錯。
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <p
        className="state-msg state-msg--error"
        role="alert"
        data-testid="sketch-read-error"
      >
        {state.message}
        {!state.isNotFound ? " 唔好當成「本機缺包」。" : null}
      </p>
    );
  }
  return <ServerSketchBody detail={state.detail} />;
}

export function OwnerSketchPanel({
  lineage,
  active,
  heading = "左：我想講嘅",
  loadState: externalState,
  fixtureLookup = null,
}: OwnerSketchPanelProps) {
  const internal = useOwnerSketchLoad(
    externalState !== undefined ? null : lineage,
    externalState !== undefined ? false : active,
    fixtureLookup,
  );
  const state = externalState !== undefined ? externalState : internal;

  return (
    <section className="panel" aria-label={heading}>
      <h2 className="panel__title">{heading}</h2>
      <p className="panel__note">
        來源：server 草圖包（origin + sketch_id 精確讀取）·{" "}
        <strong>唔係</strong>由 AI strategy.v1 重建 ·{" "}
        <strong>唔係</strong>瀏覽器 localStorage 同名草圖
      </p>
      <SketchStates state={state} />
    </section>
  );
}
