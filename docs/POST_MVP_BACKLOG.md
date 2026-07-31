# Post-MVP backlog

最後更新：2026-07-31
Authority：Owner＋Agent C-v5

> 本文件只記錄MVP完成後先研究嘅工作。**唔係work order或自行開工permission。**

## P6之後

1. **iPhone installable PWA＋Web Push**
   - service worker；
   - manifest；
   - notification permission UX；
   - VAPID；
   - push subscription store；
   - delivery idempotency／expiry／failure policy。

2. **Tailscale私有remote access**
   - Windows及iPhone加入Owner tailnet；
   - Tailscale Serve HTTPS；
   - 唔公開暴露IBKR Gateway或backend；
   - remote auth、lost-device及revocation演練。

3. **Optional cloud／Supabase研究**
   - 唔係remote access或Web Push嘅必要條件；
   - 另行比較local-first、hybrid及cloud ownership；
   - 禁止未設計就搬trading state上雲。

4. **Windows自動啟動服務**
   - process supervision；
   - log rotation；
   - upgrade／rollback；
   - reboot後仍維持Owner手動runtime resume policy。

5. **更多timeframe**
   - 3m／5m／15m／1h；
   - Owner自訂interval；
   - calendar boundary、aggregation、gap及execution ambiguity逐項驗證。

6. **長時間運行及備份**
   - multi-session soak；
   - disconnect／restart chaos；
   - resource及DB growth；
   - local paper DB／artifacts外置或雲端備份；
   - restore drill。

## 明確移除

Telegram唔再係產品方向，唔列入backlog。
