import { useEffect, useId, useRef, useState } from "react";

import {
  buildSketchZipBlob,
  triggerBlobDownload,
  zipFileName,
} from "../../lib/sketch/downloadPackage";
import type { SketchPackagePreview } from "../../lib/sketch/types";

interface ExportDialogProps {
  packagePreview: SketchPackagePreview;
  onClose: () => void;
  onGoQuantify: () => void;
  /**
   * normal_persisted: backend 201 succeeded — repo path is live.
   * owner_review: isolated fixture — no backend write.
   * local_only: insight / legacy local zip only.
   */
  mode?: "normal_persisted" | "owner_review" | "local_only";
}

export function ExportDialog({
  packagePreview,
  onClose,
  onGoQuantify,
  mode = "local_only",
}: ExportDialogProps) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState(false);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const copyText = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopyNotice(`已複製：${label}`);
    } catch {
      setCopyNotice("複製失敗——請人手選取文字複製");
    }
  };

  const downloadZip = async () => {
    setDownloadBusy(true);
    setCopyNotice(null);
    try {
      const blob = await buildSketchZipBlob(packagePreview);
      const name = zipFileName(packagePreview.sketchId);
      const url = triggerBlobDownload(blob, name);
      if (mode === "normal_persisted") {
        setCopyNotice(
          `已下載備份 ${name}。repo 已有 ${packagePreview.relativeDir}——下載只係跨環境／備份，唔使再搬同一包返去已存在路徑。`,
        );
      } else {
        setCopyNotice(
          `已下載 ${name} → 瀏覽器預設下載資料夾（通常 Downloads）。解壓後把「${packagePreview.sketchId}/」成個資料夾搬去專案入面 data/sketches/workshop/，就可以去 terminal。`,
        );
      }
      window.setTimeout(() => {
        URL.revokeObjectURL(url);
      }, 60_000);
    } catch (err) {
      setCopyNotice(
        `下載失敗：${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setDownloadBusy(false);
    }
  };

  const lineCount = packagePreview.terminalOpener.split("\n").length;

  const title =
    mode === "normal_persisted"
      ? "圖文包已寫入 repo"
      : mode === "owner_review"
        ? "Owner-review 圖文包（隔離預覽）"
        : "圖文包已準備";

  const blurb =
    mode === "normal_persisted" ? (
      <p className="panel__note" data-testid="export-dialog-persisted">
        已持久化到{" "}
        <code>{packagePreview.relativeDir}</code>
        。Terminal 可直接讀呢個路徑。
        「⬇ 下載圖文包」仍係同一份 package 嘅 browser ZIP 備份（唔由 backend 重砌）。
      </p>
    ) : mode === "owner_review" ? (
      <p className="panel__note" data-testid="export-dialog-owner-review">
        Owner-review 隔離預覽——<strong>冇寫 backend</strong>
        、in-memory only。下面路徑係 repo 形狀示範。
      </p>
    ) : (
      <p className="panel__note">
        內容已準備好。請用「⬇ 下載圖文包」落到你部電腦，解壓後搬去{" "}
        <code>data/sketches/workshop/</code>
        （ZIP 內 root 仍係 <code>{packagePreview.sketchId}/</code>
        ），再去 terminal。下面路徑係 repo 相對路徑。
      </p>
    );

  return (
    <div className="modal-root" role="presentation">
      <button
        type="button"
        className="modal-root__backdrop"
        aria-label="關閉匯出結果"
        onClick={onClose}
      />
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="modal-dialog__head">
          <h2 id={titleId} className="modal-dialog__title">
            {title}
          </h2>
          <button
            ref={closeRef}
            type="button"
            className="btn"
            onClick={onClose}
          >
            關閉
          </button>
        </header>

        {blurb}

        <p className="export-path">
          目標路徑：
          <code>{packagePreview.relativeDir}</code>
        </p>

        <pre className="export-tree" aria-label="圖文包檔案樹">
          {packagePreview.files
            .map((file, index, arr) => {
              const branch = index === arr.length - 1 ? "└" : "├";
              return `  ${branch} ${file}`;
            })
            .join("\n")}
        </pre>

        <section className="export-opener" aria-label="terminal 開場白">
          <h3 className="panel__title">terminal 開場白（可貼）</h3>
          <textarea
            className="inp inp--area export-opener__text"
            readOnly
            rows={Math.max(lineCount + 1, 6)}
            value={packagePreview.terminalOpener}
            aria-label="terminal 開場白全文"
          />
        </section>

        {copyNotice ? (
          <p className="state-msg" role="status">
            {copyNotice}
          </p>
        ) : null}

        <div className="form-actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={downloadBusy}
            onClick={() => {
              void downloadZip();
            }}
          >
            ⬇ 下載圖文包
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              void copyText("terminal 開場白", packagePreview.terminalOpener);
            }}
          >
            ⧉ 複製 terminal 開場白
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              void copyText("資料夾路徑", packagePreview.relativeDir);
            }}
          >
            開資料夾（複製路徑）
          </button>
          <button type="button" className="btn" onClick={onGoQuantify}>
            去分頁 ② 等匯入
          </button>
        </div>
      </div>
    </div>
  );
}
