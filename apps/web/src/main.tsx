import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { registerAppServiceWorker } from "./pwa/registerSW";
import "./styles/global.css";
import "./styles/page-tabs.css";
import "./styles/results.css";
import "./styles/shell.css";
import "./styles/strategies.css";
import "./styles/backtest.css";
import "./styles/paper.css";
import "./styles/daytrade.css";
import "./styles/settings.css";
import "./styles/guide.css";
import "./styles/window.css";
import "./styles/overlays.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("root element missing");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Optimistic auto-update: SW + build-meta poll → brief banner → auto reload.
// registerSW also dispatches fr-sw-need-refresh for InstallPrompt UI.
void registerAppServiceWorker(
  (reload) => {
    window.dispatchEvent(
      new CustomEvent("fr-sw-need-refresh", { detail: { reload } }),
    );
  },
  undefined,
  { autoReload: true },
);
