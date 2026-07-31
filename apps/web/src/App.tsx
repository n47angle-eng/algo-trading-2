import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./layout/AppShell";
import { NAV_ITEMS } from "./nav";
import { ThemeProvider } from "./theme/ThemeProvider";

/** Route-level code split: cold open only downloads the page you open. */
const OverviewPage = lazy(() =>
  import("./pages/OverviewPage").then((m) => ({ default: m.OverviewPage })),
);
const StrategiesPage = lazy(() =>
  import("./pages/StrategiesPage").then((m) => ({ default: m.StrategiesPage })),
);
const ResultsPage = lazy(() =>
  import("./pages/ResultsPage").then((m) => ({ default: m.ResultsPage })),
);
const DataPage = lazy(() =>
  import("./pages/DataPage").then((m) => ({ default: m.DataPage })),
);
const BacktestPage = lazy(() =>
  import("./pages/BacktestPage").then((m) => ({ default: m.BacktestPage })),
);
const PaperPage = lazy(() =>
  import("./pages/PaperPage").then((m) => ({ default: m.PaperPage })),
);
const DaytradePage = lazy(() =>
  import("./pages/DaytradePage").then((m) => ({ default: m.DaytradePage })),
);
const DaytradeTraderPage = lazy(() =>
  import("./pages/DaytradeTraderPage").then((m) => ({
    default: m.DaytradeTraderPage,
  })),
);
const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);
const GuidePage = lazy(() =>
  import("./pages/GuidePage").then((m) => ({ default: m.GuidePage })),
);
const RunDetailPage = lazy(() =>
  import("./pages/RunDetailPage").then((m) => ({ default: m.RunDetailPage })),
);
const StubPage = lazy(() =>
  import("./pages/StubPage").then((m) => ({ default: m.StubPage })),
);

function RouteFallback() {
  return (
    <div className="page-loading" role="status" aria-live="polite">
      載入中…
    </div>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route element={<AppShell />}>
              {NAV_ITEMS.map((item) => {
                if (item.id === "overview") {
                  return (
                    <Route key={item.id} index element={<OverviewPage />} />
                  );
                }
                if (item.id === "strategies") {
                  return (
                    <Route
                      key={item.id}
                      path="strategies"
                      element={<StrategiesPage />}
                    />
                  );
                }
                if (item.id === "results") {
                  return (
                    <Route
                      key={item.id}
                      path="results"
                      element={<ResultsPage />}
                    />
                  );
                }
                if (item.id === "data") {
                  return (
                    <Route key={item.id} path="data" element={<DataPage />} />
                  );
                }
                if (item.id === "backtest") {
                  return (
                    <Route
                      key={item.id}
                      path="backtest"
                      element={<BacktestPage />}
                    />
                  );
                }
                if (item.id === "paper") {
                  return (
                    <Route key={item.id} path="paper" element={<PaperPage />} />
                  );
                }
                if (item.id === "daytrade") {
                  return (
                    <Route
                      key={item.id}
                      path="daytrade"
                      element={<DaytradePage />}
                    />
                  );
                }
                if (item.id === "settings") {
                  return (
                    <Route
                      key={item.id}
                      path="settings"
                      element={<SettingsPage />}
                    />
                  );
                }
                return (
                  <Route
                    key={item.id}
                    path={item.path === "/" ? undefined : item.path.slice(1)}
                    index={item.path === "/"}
                    element={<StubPage item={item} />}
                  />
                );
              })}
              <Route path="guide" element={<GuidePage />} />
              <Route path="results/:runId" element={<RunDetailPage />} />
              <Route
                path="daytrade/traders/:traderId"
                element={<DaytradeTraderPage />}
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ThemeProvider>
  );
}
