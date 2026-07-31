# Frontend 設計系統標準（Design System v1）

日期：2026-07-24 | 作者：Agent C | 狀態：Owner 方向拍板，細節隨 UI WO 深化
適用：網站版 + iPhone PWA，同一套標準。

## 1. 設計語言定位（Owner 2026-07-24）

- **Apple 式高科技精緻路線**：以排版同留白主導，克制用色，細節精工。
- **懸浮半透明元素**（glassmorphism）：浮層面板用磨砂玻璃效果（translucency + blur + hairline 邊線），營造層次深度。
- **無 AI 味**：禁止「AI 生成感」嘅套路——紫藍漸變大背景、無意義 emoji 裝飾、千篇一律嘅 generic 組件庫默認樣式、過度圓角卡片海。每個界面元素要有存在理由。
- 色彩參考 Apple 系統色：中性灰階為主 + 單一 accent（Apple 藍系）+ 語義色（升／跌／警告）。

## 2. 全局 Token 架構（唯一真相來源）

所有樣式值住喺 **一個 `tokens.css`**（CSS custom properties）。組件層**唔准出現任何 hardcode 值**——冇裸 hex、冇裸 px 字級、冇裸 rgba。

| Token 族 | 內容 | 例 |
|---|---|---|
| 色彩 | `--color-bg` `--color-surface` `--color-surface-glass` `--color-text-primary/-secondary` `--color-accent` `--color-positive/-negative/-warning` `--color-hairline` | accent 參考 Apple systemBlue |
| 圖表色 | `--chart-up` `--chart-down` `--chart-ema-18/-50/-90` `--chart-marker-*` | 圖表檢視器都由 token 出色 |
| 字體 | `--font-family`（`-apple-system, BlinkMacSystemFont, "SF Pro", "Segoe UI", sans-serif`）；`--text-xs…-3xl` 字級階；`--weight-*` | 排版主導 |
| 間距 | `--space-1…-12`（4px 基數階梯） | 冇裸 margin/padding |
| 圓角 | `--radius-s/m/l/xl` | Apple 式偏大圓角 |
| 玻璃 | `--glass-blur` `--glass-bg` `--glass-border` | 懸浮面板統一質感 |
| 陰影 | `--shadow-1/2/3`（多層柔和） | 忌硬黑影 |
| 動效 | `--duration-fast/base/slow`；`--ease-out-quint` 等 | Apple 式順滑減速 |
| 層級 | `--z-nav/-overlay/-modal/-toast` | 冇裸 z-index |

## 3. 硬性規則

1. **零 hardcode**：組件 CSS 只准 `var(--token)`。驗收用 lint（stylelint 規則：`tokens.css` 以外禁裸 hex/px 字級）+ C code review 把關。
2. **三主題（Owner 2026-07-24 定）：深色＋淺色＋自然**——三套 token 值，由根元素 `data-theme` 切換；**組件層完全唔知道主題存在**。自然主題＝暖鼠尾草底 `#EDEEE3`＋林綠 accent `#3E7C63`。實際 token 值以 `apps/web/src/styles/tokens.css` 為唯一真相；三套 EMA 圖表色已逐套過 CVD 驗證，唔准自行改色。JS 圖表必須經 `getComputedStyle` 讀 token 並喺主題切換時重繪——JS 內零 hardcode 色值。舊全站 mock已從現行工作樹刪除，**唔准從Git history取返嚟做頁面實作依據**。
3. **玻璃效果有降級**：`backdrop-filter` 唔支援時 fallback 做實色 surface；玻璃面板上文字必須過 WCAG AA 對比度。
4. **PWA 細節**：safe-area-inset 全部處理（劉海／Home indicator）；觸控目標 ≥44×44pt；動畫用 transform/opacity（低階裝置照順）。
5. **語義色唯一性**：升＝`--color-positive`、跌＝`--color-negative`，全 app（連圖表、PnL、記分卡警告）同一來源。
6. **組件庫選型**（Tailwind 映射 token／CSS Modules／vanilla-extract）留喺首個 UI WO 由 B 提案、C 批核——唯一唔可妥協嘅係第 1 條。

## 4. 同現有決定嘅關係

- D15 前端 React+TS+Vite 不變；本標準係佢上面嘅樣式層。
- 圖表檢視器（Lightweight Charts）嘅主題設定（背景、格線、蠟燭色）必須由 token 餵入，唔准用佢默認色。
- 淺色/深色偏好＝用戶偏好參數（UI 渠道，P1）。
