# UI 設計稿索引

> **權威位置。** 設計稿一律住喺呢度，**唔准只放 Desktop**——2026-07-25 已發生一次：Desktop 上五份設計稿（含一份寫好嘅 `INSTRUCTIONS.md` 樣本）被系統清理掉，要重寫。Desktop 只可以做「方便即刻睇」嘅副本。
>
> **實作者讀邊份**：P1–P5現行稿保留；舊P6及舊full-flow稿已於2026-07-31
> 從現行工作樹刪除，因為包含Telegram、固定timeframe及冇可信價仍自動平倉等
> 過時規則。
> P6現行authority係
> `docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md`。
> 真implementation通過後先重建current P6／full-flow HTML。
>
> **配套驗收**：[`../../03-frontend-component-coverage-matrix.md`](../../03-frontend-component-coverage-matrix.md) 由以下六份稿倒推，只用嚟防漏同收貨。矩陣同本目錄任何一份稿衝突時，修矩陣，唔准改稿。

## 背景：為什麼要重新設計

2026-07-25，Owner 親手逐頁試用之後嘅結論：

> 「我發覺每個頁面都有問題，例如 UI 唔清晰啦，用戶嗰個流程體驗完全唔理解啦，見面之間唔連貫啦，最少喺前端嚟講用戶好難懂。我決定而家每一個頁面同你討論……我哋執好咗前端先，然後再搵返相應嘅後端將佢哋連埋一齊。」

**根因（Agent C 承認，見渠道 [090]）**：之前用**舊版** `docs/03` 組件矩陣驅動前端——每頁對應矩陣邊幾行，所以每頁單獨睇都「符合規格」，但**從來冇寫過「Owner 開呢頁嗰刻想做咩、做完之後去邊一頁」**。舊矩陣係功能清單，唔係用戶流程。結果七頁各自正確、合埋一齊唔連貫。舊版已刪除；現行 `docs/03` 係由本目錄六稿倒推嘅驗收工具，唔係設計來源。

**所以呢批設計稿嘅性質同以前唔同**：唔係功能清單，係 mockup ＋ 逐項拍板決定。實作者唔需要做設計判斷。

## 狀態

| 頁 | 設計稿 | 狀態 |
|---|---|---|
| **P2 策略工作台**（原工房 ＋ 策略庫合併） | [`p2-strategy-workbench.html`](p2-strategy-workbench.html) | ✅ **定稿；36 條約束**。#28–#35 係 2026-07-27 instrument 閉環；#36 係 2026-07-28 Owner 批准嘅洞察 whole-identity 可恢復歸檔（MVP 冇永久清除），exact contract 見 [`../../superpowers/specs/2026-07-28-insight-recoverable-archive-design.md`](../../superpowers/specs/2026-07-28-insight-recoverable-archive-design.md)。instrument 批准時三態畫面附件：[`../design-discussions/p2-instrument-closed-loop-v2.html`](../design-discussions/p2-instrument-closed-loop-v2.html)／[`.png`](../design-discussions/p2-instrument-closed-loop-v2.png) |
| — 附件：指令書模板（**策略**） | [`instructions-v1-sample.md`](instructions-v1-sample.md) | ✅ 樣本已寫（實際文字，唔係框架）· 用喺 `kind: strategy` 嘅包 |
| — 附件：指令書模板（**洞察**） | [`instructions-v1-insight-sample.md`](instructions-v1-insight-sample.md) | ✅ **2026-07-26 新增**——用喺 `kind: insight` 嘅包。<br>🔴 **兩份唔可以互相代替**：`docs/05` §3.5.2 明文六段式模板「按 `kind` 套內容」，第 4 段要嵌**該 kind** 嘅 schema 精要。洞察版嵌 `insight.v1`（量化條件 ＋ 建議參數 ＋ 量度方法），**全文零次提及 `strategy.v1`**——套錯咗，Owner 匯出洞察包會收到一份策略文件返嚟。<br>實作要有測試斷言：洞察包嘅 `INSTRUCTIONS.md` **含 `insight.v1` 且唔含 `strategy.v1`** |
| **P4 回測** | [`p4-backtest.html`](p4-backtest.html) | ✅ **定稿修訂 2026-07-27**——設定／進度／歷史三段 ＋ 17 條實作約束 ＋ **技術詞禁用表**。第四步「資金與成交假設」採用摘要收起方案；每個策略 × 合約各自用完整初始資金起步。#6 暖機已對齊 no-lookahead engine truth：現行 Trend exact 95 個 run 前原生日線；不足只可回填更早資料或將開始日推後 |
| **P5 結果** | [`p5-results.html`](p5-results.html) | ✅ **定稿修訂 2026-07-26**——主頁／詳情兩層 ＋ 18 條正式約束列（`#1–#16`，另含 `#6b/#6c`）。核心係**逐筆決策嘅因果解釋**、**0 成交時顯示「最接近嘅三次」**，以及證據後、拍板前獨立匯出完整 `result.v1` 結果包俾 Terminal |
| **P1 總覽** | [`p1-overview.html`](p1-overview.html) | ✅ **定稿 2026-07-25（框架）**——行動式（答「我而家應該做咩」，唔係狀態板）· 七種事項嘅**來源／出現／消失表** ＋ **15 條約束** ＋ 後端缺口表。<br>🔴 **本稿定嘅係框架同規則，唔係內容**——總覽冇自己嘅數據，畫面上啲數字全部係示範。<br>🔴 **約束 #2：總覽只讀、零寫入，掣只做導航。**<br>⛔ **約束 #15：排最後做，唔喺 WO-010 窄路徑範圍，未收到工作令唔准開工** |
| **P3 數據** | [`p3-data.html`](p3-data.html) | ✅ **定稿 2026-07-25**——四段（夠唔夠用／等你裁決／體檢記錄／補數據）＋ **14 條約束** ＋ **技術詞翻譯表**（後端真實問題碼 → 人話）＋ 後端缺口表。<br>🔴 覆蓋要答「夠唔夠用」（交易日數／完整日／原生日線／**連續完整最長一段**），**唔准只顯示 bar 數**。<br>🔴 兩個裁決掣要逐個講後果同風險；**唔准要 Owner 自己打日期**。<br>⛔ **約束 #14：唔喺 WO-010 窄路徑範圍，未收到工作令唔准開工** |
| **P6 模擬盤** | [完整runtime設計](../../superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md) | 🟡 **完整書面規格及one-shot implementation plan已批准，等Owner轉發啟動W。** IB只供行情；app自家simulated execution；timeframe-agnostic；persistent multi-trader；disconnect／restart recovery；8R／8-loss safety；runtime `paper-review.v2`。舊v4 HTML／PNG已從現行工作樹刪除。 |

### 🔴 跨頁共用：四格圖表組件

同一個「四格圖表」會喺至少三處出現——**P2 分頁 ② 試跑預覽**、**P5 詳情頁**、將來 **P6 模擬盤**。**唔准喺每頁各寫一次。**

組件要支援嘅能力（三個用途嘅並集）：2×2 grid ／ 每格自己嘅時間框架同角色標籤 ／ K 線 ＋ 成交量 ／ 任意條指標線 ／ **入市箭頭帶時間同價位標籤** ／ 止損同目標水平線 ／ 離場標記 ／ **入市時刻垂直線帶 #N 編號** ／ 趨勢日著色 ／ reject 位標記 ／ 四格 crosshair 同步 ／ 外部觸發「跳去某個時刻」。

**➕ 2026-07-25 追加三項（Owner 批准，來源：P6 模擬盤設計稿 v2）**——P6 要睇每個交易員嘅**實時** K 線：

1. **實時更新**：新 bar 到就 append／覆蓋最後一根，唔使重新載入全部（`lightweight-charts` 嘅 `series.update()`，唔係 `setData()`）
2. **未平倉持倉顯示**：入市箭頭 ＋ 止損目標線 ＋ **而家嘅浮動盈虧**（回測係已完成嘅單，模擬盤有「仲未平」呢個狀態）
3. **各已配置timeframe同步更新**：chart唔假設永久係D／5m；MVP display
   1m／30m由backend capability供應。策略本身仍跟immutable strategy.v1
   （strategy-0003係D／1H／5m），future interval唔需要重寫chart component

階段 1 用 fixture 餵就得，**但介面要一開始就照呢個並集設計**（包括預留「推新 bar 入去」嘅入口），否則 P5／P6 做嗰陣要拆返重寫。

### P4 兩個要特別留意嘅點

1. **兩個技術控制整個移除**：「validation_run（工程驗證，非策略主張）」改為只喺 CLI 用；「開啟參數 override」被 P2 嘅「改參數 → 生成新版本」取代。**Owner 撞到呢兩個字完全唔明，係正確反應——佢哋唔係為佢而設。**
2. **技術詞禁用表**（P4 稿內）：`batch`／`runs`／`nq-20260725-val-364e22`／`p50 閘`／`range_start`／`rth`／`eth` 全部唔准出現喺 UI，逐個列咗改成咩。**呢張表適用於全部頁面，唔止 P4。**

## 已被取代嘅稿（唔准用）

傾嘅過程中出過幾個中間稿，互相有矛盾嘅版本，**已全部被 `p2-strategy-workbench.html` 取代**：

- `futures-ui-02-workbench`（四分頁初稿）
- `futures-ui-03-tabs-234`（②③④ 修訂）
- `futures-ui-04-dryrun-preview`（② 再修訂）

呢三個只存在過喺 Desktop，已經被清理掉。**如果你手上有副本，唔准用。**

## 相關文件

| 要搵乜 | 去邊 |
|---|---|
| 三主題 token 實際值 | `../../../apps/web/src/styles/tokens.css`（唯一真相；舊全站 mock已刪除） |
| 圖文包格式權威規格 | `../../05-file-contract-schemas-draft.md` §3.5 |
| User journey | `../../02-mvp-closed-loop-user-journey.md` |
| 核心決定 D1–D21 | `../../00-design-baseline-conclusions.md` |

## 規則

1. **設計稿唔准由實作者改。** 發現 mockup 同「實作約束」矛盾，或者發現設計本身有問題——**喺 `AGENT_CHANNEL_W.md` 提出，Agent C 會修訂並更新本索引**。
2. **「實作約束」表優先於 mockup。** mockup 表達唔到嘅嘢（例如「離開頁面 = 當確認刪除」）全部喺約束表。
3. **每份定稿都要有版本日期。** 修訂時更新稿內 stamp 同本索引。
