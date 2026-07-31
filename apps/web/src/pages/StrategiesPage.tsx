import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { InsightTab } from "../components/strategies/InsightTab";
import { LibraryTab } from "../components/strategies/LibraryTab";
import { OwnerReviewFixtureBar } from "../components/strategies/OwnerReviewFixtureBar";
import { QuantifyTab } from "../components/strategies/QuantifyTab";
import { SketchTab } from "../components/strategies/SketchTab";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";
import { WorkbenchProvider, useWorkbench } from "../lib/p2/WorkbenchContext";
import { OWNER_REVIEW_P2_URL } from "../lib/p2/ownerReviewFixtures";

type WorkbenchTab = "sketch" | "quantify" | "library" | "insight";

function WorkbenchBody() {
  const [params] = useSearchParams();
  const { ownerReview, activeFixture, fixtureRevision } = useWorkbench();
  const initialTab = ((): WorkbenchTab => {
    const t = params.get("tab");
    if (t === "library" || t === "quantify" || t === "insight" || t === "sketch") {
      return t;
    }
    return "sketch";
  })();
  const [tab, setTab] = useState<WorkbenchTab>(initialTab);
  const focusStrategyId = params.get("strategy");
  // Monotonic revision — never length-based (D18 collision fix)
  const fixtureEpoch = fixtureRevision;

  useEffect(() => {
    const t = params.get("tab");
    if (t === "library" || t === "quantify" || t === "insight" || t === "sketch") {
      setTab(t);
    }
  }, [params]);

  // When fixture loads quantify YAML, jump to quantify
  useEffect(() => {
    if (
      activeFixture &&
      ["valid_nq_ym", "invalid_nq_gc", "primary_only", "local_404", "server_5xx"].includes(
        activeFixture,
      )
    ) {
      setTab("quantify");
    }
  }, [activeFixture]);

  const tabs: { id: WorkbenchTab; label: string }[] = [
    { id: "sketch", label: "① 草圖" },
    { id: "quantify", label: "② 量化確認" },
    { id: "library", label: "③ 版本庫" },
    { id: "insight", label: "④ 市場洞察" },
  ];

  return (
    <div className="detail-stack">
      <PageHeader
        title="策略工作台"
        info={
          <>
            由左至右行一次：草圖（用日常講法寫低諗法）→ 量化確認（逐項變成明確條件）→
            版本庫（確認完會生成一個唔會再改嘅版本）→ 市場洞察（記低相關觀察）。
            草圖幾時都改得，版本改唔到——因為回測成績一定要對得返一個固定嘅策略定義。
          </>
        }
      />

      {ownerReview ? (
        <p
          className="owner-review-banner"
          role="status"
          data-testid="owner-review-banner"
        >
          Owner-review 模式：P2 業務狀態用頁面 in-memory store（唔讀／唔寫
          localStorage 草稿庫）。Catalog／sketch detail 用隔離 fixture，唔 call
          coverage／sketch／strategy validate-import-confirm。App shell 既有 IB
          status 探測不屬 P2 fixture。穩定 URL：{" "}
          <code>{OWNER_REVIEW_P2_URL}</code>
        </p>
      ) : null}

      {ownerReview ? <OwnerReviewFixtureBar /> : null}

      <PageTabBar
        ariaLabel="策略工作台分頁"
        className="page-tabs--sticky"
        active={tab}
        onChange={setTab}
        tabs={tabs}
      />

      {tab === "sketch" ? (
        <div
          id="page-panel-sketch"
          role="tabpanel"
          aria-labelledby="page-tab-sketch"
        >
          <SketchTab
            fixtureEpoch={fixtureEpoch}
            onGoQuantify={() => {
              setTab("quantify");
            }}
          />
        </div>
      ) : null}

      {tab === "quantify" ? (
        <div
          id="page-panel-quantify"
          role="tabpanel"
          aria-labelledby="page-tab-quantify"
        >
          <QuantifyTab
            fixtureEpoch={fixtureEpoch}
            onGoLibrary={() => {
              setTab("library");
            }}
          />
        </div>
      ) : null}

      {tab === "library" ? (
        <div
          id="page-panel-library"
          role="tabpanel"
          aria-labelledby="page-tab-library"
        >
          <LibraryTab
            focusStrategyId={focusStrategyId}
            ownerReview={ownerReview}
          />
        </div>
      ) : null}

      {tab === "insight" ? (
        <div
          id="page-panel-insight"
          role="tabpanel"
          aria-labelledby="page-tab-insight"
        >
          <InsightTab fixtureEpoch={fixtureEpoch} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * P2 策略工作台.
 * Owner-review: ?scenario=owner-review — isolated in-memory P2 store.
 */
export function StrategiesPage() {
  const [params] = useSearchParams();
  const ownerReview = params.get("scenario") === "owner-review";

  return (
    <WorkbenchProvider ownerReview={ownerReview}>
      <WorkbenchBody />
    </WorkbenchProvider>
  );
}
