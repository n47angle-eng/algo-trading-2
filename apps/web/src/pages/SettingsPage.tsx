import { useCallback, useEffect, useState } from "react";

import {
  DEFAULT_NOTIFICATION_PREFERENCES,
  NOTIFICATION_TYPE_DESCRIPTIONS,
  NOTIFICATION_TYPE_LABELS,
  NOTIFICATION_TYPES,
  getNotificationPermission,
  loadNotificationPreferences,
  requestNotificationPermission,
  saveNotificationPreferences,
  setMasterNotificationEnabled,
  setNotificationTypeEnabled,
  subscribeWebPush,
  type NotificationPermissionState,
  type NotificationPreferences,
  type NotificationType,
  type PushSubscribeResult,
} from "../lib/notifications";
import { InfoButton } from "../components/ui/InfoButton";
import { PageHeader } from "../components/ui/PageHeader";
import { PageTabBar } from "../components/ui/PageTabBar";

type SettingsTab = "notifications" | "install";

const SETTINGS_TABS: { id: SettingsTab; label: string }[] = [
  { id: "notifications", label: "通知" },
  { id: "install", label: "安裝 App" },
];

export function SettingsPage() {
  const [prefs, setPrefs] = useState<NotificationPreferences>(() =>
    loadNotificationPreferences(),
  );
  const [permission, setPermission] =
    useState<NotificationPermissionState>("default");
  const [pushStatus, setPushStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [tab, setTab] = useState<SettingsTab>("notifications");

  useEffect(() => {
    setPermission(getNotificationPermission());
  }, []);

  const persist = useCallback((next: NotificationPreferences) => {
    setPrefs(next);
    saveNotificationPreferences(next);
    setSavedFlash(true);
    window.setTimeout(() => setSavedFlash(false), 1200);
  }, []);

  async function enablePermission() {
    setBusy(true);
    try {
      const result = await requestNotificationPermission();
      setPermission(result);
      if (result === "granted") {
        const push = await subscribeWebPush();
        setPushStatus(describePush(push));
      }
    } finally {
      setBusy(false);
    }
  }

  async function enablePush() {
    setBusy(true);
    try {
      if (permission !== "granted") {
        const result = await requestNotificationPermission();
        setPermission(result);
        if (result !== "granted") {
          setPushStatus("需要先允許通知權限");
          return;
        }
      }
      const push = await subscribeWebPush();
      setPushStatus(describePush(push));
    } finally {
      setBusy(false);
    }
  }

  function toggleMaster() {
    persist(setMasterNotificationEnabled(prefs, !prefs.enabled));
  }

  function toggleType(type: NotificationType) {
    persist(
      setNotificationTypeEnabled(prefs, type, !prefs.types[type]),
    );
  }

  function resetDefaults() {
    persist({
      enabled: DEFAULT_NOTIFICATION_PREFERENCES.enabled,
      types: { ...DEFAULT_NOTIFICATION_PREFERENCES.types },
    });
  }

  return (
    <div className="settings-page">
      <PageHeader
        title="設定"
        actions={
          savedFlash ? <span className="chip chip--pass">已儲存</span> : null
        }
        info={
          <>
            通知偏好同「裝落手機主畫面」。裝完之後會冇咗瀏覽器嘅網址列，用起上嚟同原生
            app 差唔多。主題切換唔喺呢頁——喺「更多」入面。
          </>
        }
      />

      <PageTabBar
        ariaLabel="設定分頁"
        className="page-tabs--sticky"
        active={tab}
        onChange={setTab}
        tabs={SETTINGS_TABS}
      />

      {tab === "notifications" ? (
      <section
        className="settings-card"
        role="tabpanel"
        id="page-panel-notifications"
        aria-labelledby="page-tab-notifications settings-notif-title"
      >
        <div className="panel__head">
          <h2 id="settings-notif-title" className="settings-card__title">
            通知
          </h2>
          <InfoButton label="通知點用" align="end">
            裝咗落主畫面之後，模擬交易員同回測嘅事件可以推到你部機。要先允許通知權限，瀏覽器先會俾我哋發。冇允許嘅話，下面啲開關唔會生效。
          </InfoButton>
        </div>

        <div className="settings-row">
          <div className="settings-row__label">
            <span className="settings-row__name">通知權限</span>
            <span className="settings-row__desc">
              目前：{permissionLabel(permission)}
            </span>
          </div>
          <button
            type="button"
            className="settings-btn settings-btn--primary"
            disabled={busy || permission === "granted" || permission === "unsupported"}
            onClick={() => void enablePermission()}
          >
            {permission === "granted" ? "已允許" : "允許通知"}
          </button>
        </div>

        <div className="settings-row">
          <div className="settings-row__label">
            <span className="settings-row__name">背景推送（Web Push）</span>
            <span className="settings-row__desc">
              {pushStatus ??
                "App 關閉時仍可由伺服器推送（需 VAPID 金鑰）"}
            </span>
          </div>
          <button
            type="button"
            className="settings-btn"
            disabled={busy}
            onClick={() => void enablePush()}
          >
            訂閱推送
          </button>
        </div>

        <div className="settings-row">
          <div className="settings-row__label">
            <span className="settings-row__name">總開關</span>
            <span className="settings-row__desc">
              關閉後唔會發送任何通知
            </span>
          </div>
          <label className="settings-toggle">
            <input
              type="checkbox"
              checked={prefs.enabled}
              onChange={toggleMaster}
              aria-label="通知總開關"
            />
            <span className="settings-toggle__ui" aria-hidden="true" />
          </label>
        </div>

        <ul className="settings-type-list">
          {NOTIFICATION_TYPES.map((type) => (
            <li key={type} className="settings-row settings-row--type">
              <div className="settings-row__label">
                <span className="settings-row__name">
                  {NOTIFICATION_TYPE_LABELS[type]}
                </span>
                <span className="settings-row__desc">
                  {NOTIFICATION_TYPE_DESCRIPTIONS[type]}
                </span>
              </div>
              <label className="settings-toggle">
                <input
                  type="checkbox"
                  checked={prefs.types[type]}
                  disabled={!prefs.enabled}
                  onChange={() => toggleType(type)}
                  aria-label={NOTIFICATION_TYPE_LABELS[type]}
                />
                <span className="settings-toggle__ui" aria-hidden="true" />
              </label>
            </li>
          ))}
        </ul>

        <div className="settings-row">
          <button type="button" className="settings-btn" onClick={resetDefaults}>
            還原預設
          </button>
        </div>
      </section>
      ) : null}

      {tab === "install" ? (
      <section
        className="settings-card"
        role="tabpanel"
        id="page-panel-install"
        aria-labelledby="page-tab-install settings-pwa-title"
      >
        <div className="panel__head">
          <h2 id="settings-pwa-title" className="settings-card__title">
            安裝為 App
          </h2>
          <InfoButton label="安裝點做" align="end">
            用瀏覽器「加到主畫面」裝完之後，會用全螢幕獨立視窗行，有主畫面圖示，殼層仲會離線快取。下面按你部機型號列咗步驟。
          </InfoButton>
        </div>
        <ul className="settings-tips">
          <li>
            <strong>iPhone / iPad</strong>：Safari → 分享 →「加到主畫面」
          </li>
          <li>
            <strong>Android</strong>：Chrome 選單 →「安裝應用程式」或「加到主畫面」
          </li>
          <li>
            通知權限需在安裝後於本頁允許；iOS 僅主畫面 PWA 可穩定收推送
          </li>
        </ul>
      </section>
      ) : null}
    </div>
  );
}

function permissionLabel(state: NotificationPermissionState): string {
  switch (state) {
    case "granted":
      return "已允許";
    case "denied":
      return "已拒絕（請到系統設定重新開啟）";
    case "unsupported":
      return "此環境唔支援通知";
    default:
      return "尚未決定";
  }
}

function describePush(result: PushSubscribeResult): string {
  switch (result.reason) {
    case "subscribed":
    case "already":
      return "已訂閱背景推送";
    case "vapid_unavailable":
      return "伺服器未設定 VAPID；前景通知仍然可用";
    case "permission_denied":
      return "需要先允許通知權限";
    case "no_sw":
    case "no_push_manager":
      return "此瀏覽器暫不支援 Web Push";
    case "server_rejected":
      return "伺服器拒絕訂閱";
    default:
      return "訂閱失敗，請稍後再試";
  }
}
