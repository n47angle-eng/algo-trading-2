# Futures Research Web (PWA)

React 19 + Vite 前端，支援 **Progressive Web App** 安裝到手機主畫面。

## PWA

- Manifest：`public/manifest.webmanifest`（`display: standalone`、深色 monochrome theme）
- Service Worker：`public/sw.js`（離線殼層快取、Web Push 顯示、更新 skipWaiting）
- Icons：`public/icons/*`
- 註冊：production / preview 自動註冊；dev 預設關閉（`VITE_PWA_DEV=1` 可開）

### 通知

- 設定頁 `/settings`：總開關 + 各類型（買入機會、入市、獲利離場、止損離場、回測完成）
- 偏好存 `localStorage` key `futures-research.notification-prefs.v1`
- 模擬盤：timeline / position delta → 通知
- 回測：batch 進入 terminal status → 通知
- 前景：Notification API / `registration.showNotification`
- 背景：Web Push（需伺服器 VAPID）

### Web Push（可選）

後端路由：

- `GET /api/v1/push/vapid-public-key`
- `POST /api/v1/push/subscribe`
- `POST /api/v1/push/unsubscribe`
- `POST /api/v1/push/notify`

環境變數：

```bash
export FR_VAPID_PUBLIC_KEY=...
export FR_VAPID_PRIVATE_KEY=...
export FR_VAPID_SUBJECT=mailto:you@example.com
# optional for server send:
# pip install pywebpush
```

iOS：必須「加到主畫面」嘅 PWA 先可以穩定收推送；先喺設定頁允許通知。

## Scripts

```bash
npm run dev
npm run build
npm run preview
npm test
```
