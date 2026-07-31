import { useWorkbench } from "../../lib/p2/WorkbenchContext";
import type { OwnerReviewFixtureId } from "../../lib/p2/ownerReviewSession";

const FIXTURES: { id: OwnerReviewFixtureId; label: string }[] = [
  { id: "fresh", label: "① 新草稿" },
  { id: "export_ready", label: "①b 可匯出 PNG+理據" },
  { id: "valid_nq_ym", label: "② 合法 NQ+YM" },
  { id: "invalid_nq_gc", label: "③ 非法 NQ+GC" },
  { id: "primary_only", label: "④ primary-only {}" },
  { id: "local_404", label: "⑤ 缺草圖 404" },
  { id: "server_5xx", label: "⑥ server 5xx" },
  { id: "legacy_unexported", label: "⑦ legacy 未匯出" },
  { id: "legacy_exported", label: "⑧ legacy 已匯出" },
  { id: "catalog_loading", label: "catalog loading" },
  { id: "catalog_empty", label: "catalog empty" },
  { id: "catalog_503", label: "catalog 503" },
  { id: "catalog_invalid", label: "catalog invalid" },
];

/** Visible fixture loader for owner-review hand-test (D14). */
export function OwnerReviewFixtureBar() {
  const { ownerReview, activeFixture, loadFixture } = useWorkbench();
  if (!ownerReview) {
    return null;
  }
  return (
    <div
      className="fixture-bar"
      role="group"
      aria-label="Owner-review fixtures"
      data-testid="owner-review-fixtures"
    >
      <span className="fixture-bar__label">
        Fixture 一鍵載入（只影響本頁 in-memory，唔寫 normal localStorage、唔 call P2
        business API）
      </span>
      {FIXTURES.map((f) => (
        <button
          key={f.id}
          type="button"
          className={
            activeFixture === f.id
              ? "btn fixture-bar__btn fixture-bar__btn--on"
              : "btn fixture-bar__btn"
          }
          data-testid={`fixture-${f.id}`}
          onClick={() => {
            loadFixture(f.id);
          }}
        >
          {f.label}
        </button>
      ))}
    </div>
  );
}
