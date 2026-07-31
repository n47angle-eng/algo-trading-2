import { useState, type ChangeEvent } from "react";

import { isPngDataUrl } from "../../lib/sketch/pngGate";
import {
  INDICATOR_OPTIONS,
  ROLE_OPTIONS,
  TIMEFRAME_OPTIONS,
  type IndicatorToken,
  type SketchChartSlot,
} from "../../lib/sketch/types";

interface SketchCellProps {
  chart: SketchChartSlot;
  index: number;
  onChange: (next: SketchChartSlot) => void;
  /** When true, disable image/text edits (exported or parent lock). */
  readonly?: boolean;
}

export function SketchCell({
  chart,
  index,
  onChange,
  readonly = false,
}: SketchCellProps) {
  const inputId = `sketch-upload-${chart.slotId}`;
  const viewId = `sketch-view-${chart.slotId}`;
  const [imageError, setImageError] = useState<string | null>(null);

  const setIndicators = (token: IndicatorToken) => {
    if (readonly) return;
    const has = chart.indicatorsShown.includes(token);
    const indicatorsShown = has
      ? chart.indicatorsShown.filter((t) => t !== token)
      : [...chart.indicatorsShown, token];
    onChange({ ...chart, indicatorsShown });
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || readonly) {
      return;
    }
    // Guide picker to PNG only; still validate content.
    if (file.type && file.type !== "image/png") {
      setImageError("只接受 PNG 圖檔（唔接受 JPEG／WebP 等）");
      return;
    }
    if (!file.type.startsWith("image/") && file.type !== "") {
      setImageError("只接受 PNG 圖檔");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : null;
      if (!result || !isPngDataUrl(result)) {
        setImageError("檔案唔係合法 PNG（簽名唔啱或類型錯誤）");
        return;
      }
      setImageError(null);
      onChange({
        ...chart,
        imageDataUrl: result,
        imageFileName: file.name.endsWith(".png")
          ? file.name
          : file.name.replace(/\.[^.]+$/, "") + ".png",
      });
    };
    reader.onerror = () => {
      setImageError("讀檔失敗");
    };
    reader.readAsDataURL(file);
  };

  return (
    <article
      className="sketch-cell"
      aria-label={`圖格 ${index + 1} · ${chart.timeframe}`}
    >
      <div className="sketch-cell__meta">
        <label className="field field--inline">
          <span>時間框架</span>
          <select
            className="inp"
            value={chart.timeframe}
            aria-label={`圖格 ${index + 1} 時間框架`}
            disabled={readonly}
            onChange={(e) => {
              onChange({ ...chart, timeframe: e.target.value });
            }}
          >
            {TIMEFRAME_OPTIONS.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
            {!TIMEFRAME_OPTIONS.includes(
              chart.timeframe as (typeof TIMEFRAME_OPTIONS)[number],
            ) ? (
              <option value={chart.timeframe}>{chart.timeframe}</option>
            ) : null}
          </select>
        </label>
        <label className="field field--inline">
          <span>角色</span>
          <select
            className="inp"
            value={chart.role}
            aria-label={`圖格 ${index + 1} 角色`}
            disabled={readonly}
            onChange={(e) => {
              onChange({
                ...chart,
                role: e.target.value as SketchChartSlot["role"],
              });
            }}
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="sketch-cell__drop">
        {chart.imageDataUrl ? (
          <div className="sketch-cell__preview">
            <img
              src={chart.imageDataUrl}
              alt={`${chart.timeframe} 上載預覽`}
              className="sketch-cell__img"
            />
            <div className="sketch-cell__preview-actions">
              <label className="btn" htmlFor={inputId}>
                換圖
              </label>
              <button
                type="button"
                className="btn btn--danger"
                disabled={readonly}
                onClick={() => {
                  setImageError(null);
                  onChange({
                    ...chart,
                    imageDataUrl: null,
                    imageFileName: null,
                  });
                }}
              >
                刪圖
              </button>
            </div>
            {chart.imageFileName ? (
              <p className="sketch-cell__fname">{chart.imageFileName}</p>
            ) : null}
          </div>
        ) : (
          <label className="sketch-cell__upload" htmlFor={inputId}>
            <span className="sketch-cell__upload-label">上載 PNG</span>
            <span>拖放或揀 {chart.timeframe} 圖截圖（只接受 PNG）</span>
          </label>
        )}
        <input
          id={inputId}
          className="visually-hidden"
          type="file"
          accept="image/png,.png"
          disabled={readonly}
          onChange={onFile}
        />
        {imageError ? (
          <p className="state-msg state-msg--error" role="alert">
            {imageError}
          </p>
        ) : null}
      </div>

      <div
        className="sketch-cell__chips"
        role="group"
        aria-label={`圖格 ${index + 1} 指標`}
      >
        {INDICATOR_OPTIONS.map((token) => {
          const on = chart.indicatorsShown.includes(token);
          return (
            <button
              key={token}
              type="button"
              className={on ? "chip-btn chip-btn--on" : "chip-btn"}
              aria-pressed={on}
              disabled={readonly}
              onClick={() => {
                setIndicators(token);
              }}
            >
              {token}
            </button>
          );
        })}
      </div>
      <p className="sketch-cell__chip-hint">
        剔完冇 → 匯出寫 <code>[]</code>（同「未填」唔同）
      </p>

      <label className="field" htmlFor={viewId}>
        <span>
          {chart.timeframe} 圖判斷（必填）
          {chart.ownerView.trim() ? "" : " · 未填"}
        </span>
        <textarea
          id={viewId}
          className="inp inp--area sketch-cell__view"
          rows={4}
          value={chart.ownerView}
          placeholder={`${chart.timeframe} 圖你見到咩、點判斷…`}
          disabled={readonly}
          onChange={(e) => {
            onChange({ ...chart, ownerView: e.target.value });
          }}
        />
      </label>
    </article>
  );
}
