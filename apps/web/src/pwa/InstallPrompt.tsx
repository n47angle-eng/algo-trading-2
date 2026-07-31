import { useEffect, useRef, useState } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

const DISMISS_KEY = "futures-research.pwa-install-dismissed";

/**
 * Native-style install banner for Chromium browsers that fire
 * beforeinstallprompt. iOS users see a short tip instead.
 *
 * Code updates are optimistic: when fr-sw-need-refresh fires we show a brief
 * "正在更新" banner and auto-reload (no 稍後 gate for deploys).
 */
export function InstallPrompt() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(
    null,
  );
  const [visible, setVisible] = useState(false);
  const [iosTip, setIosTip] = useState(false);
  const [updating, setUpdating] = useState(false);
  const reloadRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(DISMISS_KEY) === "1") {
        return;
      }
    } catch {
      /* ignore */
    }

    const isStandalone =
      window.matchMedia("(display-mode: standalone)").matches ||
      ("standalone" in navigator &&
        (navigator as Navigator & { standalone?: boolean }).standalone === true);

    if (isStandalone) {
      return;
    }

    const ua = window.navigator.userAgent;
    const isIos = /iphone|ipad|ipod/i.test(ua);
    const isSafari = /safari/i.test(ua) && !/crios|fxios|edgios/i.test(ua);
    if (isIos && isSafari) {
      setIosTip(true);
      setVisible(true);
      return;
    }

    const onBip = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setVisible(true);
    };
    window.addEventListener("beforeinstallprompt", onBip);
    return () => window.removeEventListener("beforeinstallprompt", onBip);
  }, []);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ reload: () => void }>).detail;
      if (detail?.reload) {
        reloadRef.current = detail.reload;
        setUpdating(true);
        // Safety: if registerSW autoReload path stalled, force after 1.5s
        window.setTimeout(() => {
          reloadRef.current?.();
        }, 1500);
      }
    };
    window.addEventListener("fr-sw-need-refresh", handler);
    return () => window.removeEventListener("fr-sw-need-refresh", handler);
  }, []);

  function dismiss() {
    setVisible(false);
    setIosTip(false);
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
  }

  async function install() {
    if (!deferred) {
      return;
    }
    await deferred.prompt();
    try {
      await deferred.userChoice;
    } catch {
      /* ignore */
    }
    setDeferred(null);
    setVisible(false);
  }

  if (updating) {
    return (
      <div
        className="pwa-banner pwa-banner--update pwa-banner--auto"
        role="status"
        aria-live="assertive"
      >
        <div className="pwa-banner__text">
          <strong>有新版本</strong>
          <span>正在自動更新…</span>
        </div>
        <div className="pwa-banner__actions">
          <button
            type="button"
            className="pwa-banner__btn pwa-banner__btn--primary"
            onClick={() => reloadRef.current?.()}
          >
            立即重新載入
          </button>
        </div>
      </div>
    );
  }

  if (!visible) {
    return null;
  }

  return (
    <div className="pwa-banner" role="dialog" aria-label="安裝應用程式">
      <div className="pwa-banner__text">
        <strong>加到主畫面</strong>
        <span>
          {iosTip
            ? "用 Safari 分享 →「加到主畫面」，即可像原生 App 使用並接收通知"
            : "安裝後像原生 App 全螢幕運行，並可接收模擬盤／回測通知"}
        </span>
      </div>
      <div className="pwa-banner__actions">
        {!iosTip && deferred ? (
          <button
            type="button"
            className="pwa-banner__btn pwa-banner__btn--primary"
            onClick={() => void install()}
          >
            安裝
          </button>
        ) : null}
        <button type="button" className="pwa-banner__btn" onClick={dismiss}>
          關閉
        </button>
      </div>
    </div>
  );
}
