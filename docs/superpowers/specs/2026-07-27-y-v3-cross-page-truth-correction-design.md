# Y-v3 Cross-page Truth Correction — Approved Design

日期：2026-07-27

狀態：**Owner 已批准**

性質：現有權威設計／合約嘅窄修正，**唔係新頁面、唔改已批准 layout**

## 1. 問題同裁決

Y-v3 read-only onboarding 揭出四項真實 drift。C 已由現行設計、合約、前端代碼
同 backend config 逐項覆核；Owner 已批准以下處理。

### 1.1 P2 owner-review 版本庫

現況：

- P2 owner-review banner 聲明 fixture 隔離、唔碰真業務資料；
- 但分頁③ `LibraryTab` mount 後仍可打真 strategy／run API，亦可能寫 normal
  localStorage tombstone；
- 所以「整條 P2 owner-review route 隔離」目前唔成立。

Owner 採用方案 A：

- 分頁③仍可見，避免改四分頁 layout；
- owner-review 內顯示一個誠實、唯讀、隔離狀態；
- 文案講明本 fixture 唔載入真版本庫，要查真版本請用正常模式；
- 唔砌假版本庫、唔打任何 business API、唔讀寫 normal localStorage、唔起刪除
  timer、唔提供過目／編輯／刪除／確認等動作；
- normal mode 版本庫行為完全不變。

呢個係 review harness 隔離修正，唔係產品頁面新設計。

### 1.2 P4 成本／滑點預設

權威值：

| 項 | 值 |
|---|---:|
| NQ 手續費（每邊每張） | USD 2.50 |
| YM 手續費（每邊每張） | USD 2.50 |
| GC 手續費（每邊每張） | USD 2.80 |
| 突破入市滑點 | 1 tick |
| 止蝕離場滑點 | 2 ticks |
| 目標限價滑點 | 0 tick |
| 日終平倉滑點 | 1 tick |

呢組值已同時存在於 `docs/ui/designs/p4-backtest.html`、`docs/00` 同
`config/contracts.yaml`。前端 `DEFAULT_ASSUMPTIONS` 嘅
`2.25/2.25/2.50`＋全部 `1 tick` 係 drift，唔係批准例外。

修正後：

- 畫面、normal unchanged-default guard、owner-review snapshot 同 backend config
  要講同一組真相；
- 正常模式保持現有能力邊界：未改動上述 default 可以提交；Owner 改成本／滑點
  後，在 backend 尚未支援完整 locked snapshot 前仍誠實阻擋；
- 每個策略 × 合約仍各自用完整 USD 100,000 起步，唔共同攤分。

### 1.3 `INSTRUCTIONS.md` canonical 成員大小寫

`docs/05` 同真 ZIP 合約係：

```text
chart-D.png
chart-1H.png
chart-30m.png
chart-5m.png
```

strategy 指令模板內嘅 `chart-d.png`／`chart-1h.png` 係錯誤引用。只修模板內檔案樹
同 example，唔增加 lowercase alias，亦唔改 canonical ZIP 成員。

驗收要由真 package 證明：`INSTRUCTIONS.md` 每個圖檔引用，以 exact case 對應 ZIP
內一個實際成員。

### 1.4 P5 約束數 metadata

P5 正式約束係 18 條：`#1–#16` 加 `#6b/#6c`。HTML 頂部 stamp 寫 17 只係
metadata typo；C 直接更正為 18。**約束表內容、編號、畫面同 layout 零改動。**

## 2. Y-v3 onboarding 口徑更正

`[164] SYNC` 有三項要更正，唔改寫舊訊息，由下一條 REVIEW 留審計記錄：

1. P3 正式約束數係 **14**，唔係 12；
2. P6 正式約束數係 **27**，唔係 21；
3. 衝突判讀權威次序係：

```text
最新 Owner 明確決定
→ 該頁權威設計稿最後「實作約束」
→ docs/05 artifact contract
→ 最新渠道正式工作令／REVIEW
→ briefing／PROJECT_STATE
```

`docs/03`、`docs/08`、`docs/09` 係驗收／mapping／閉環審計工具，發現同高層權威
衝突時修工具，唔反改設計。

## 3. 非目標

本裁決唔授權：

- P2 catalog `view=catalog`／shared request（等 X `[X-041]` 收貨後另批）；
- preview、Insight persistence、derive、backend archive；
- P4 backend precheck／progress／cancel／exact rerun；
- P5 normal export／PromotionDecision seam；
- P1、P3 final frontend 或 P6；
- IB Gateway、migration、真 artifact 寫入；
- 重構已凍結 P2/P4/P5 UX。

## 4. 驗收原則

1. owner-review Library：business fetch 0、normal storage bytes不變、timer 0；
2. normal Library：既有真 API／守衛行為不變；
3. P4 defaults：UI、guard、fixture、config exact一致；
4. template：所有 exact-case member引用可由同一個 ZIP 解出；
5. 每項有 mounted／contract test同一個能令指定測試轉紅嘅 mutation；
6. full web、typecheck、lint、build全綠；
7. 只可改 `apps/web/` 產品／測試；C 嘅 metadata 文件修正唔交俾 Y。
