# 設計基線結論文件（Design Baseline Conclusions）

日期：2026-07-23 | 最後同步：2026-07-28 | 作者：Agent C（基礎討論與結論整理）| 狀態：**有效基線；D1–D21 已拍板**
性質：2026-07-23 項目重啟後，Owner 與 Agent C 基礎討論嘅正式結論。後續修訂已直接標喺相關決定；如本文同六份定稿設計稿或其「實作約束」表有衝突，**以定稿設計稿較新決定為準**。舊 GitHub 設計文件已全部作廢（僅存於 git history 備查）。

---

## 1. 項目定位

一個**個人用**嘅期貨算法交易研究平台，服務一個閉環：

```
同 AI 傾策略（terminal）→ 策略文件 → 回測 → 八維度評估 → Owner 拍板晉升 → 模擬盤（真實市場數據）→ 偏離對照 → 循環改良
```

單用戶（Owner），唔做真錢交易，唔對外發佈。

## 2. 核心決定一覽（連理由）

| # | 決定 | 內容 | 理由 |
|---|---|---|---|
| D1 | 數據來源 | **IB 獨家**（初階段）；日後開放其他來源，設計上保留可換數據源邊界 | 慳訂閱、單一接口；Owner 已有 IB |
| D2 | AI 模式 | **零 AI API**。AI 經 Owner 嘅 terminal agent（Claude/Grok…）以**雙向文件合約**參與：策略文件入、結果文件出 | 零 API 成本；文件即審計紀錄；app 同 AI 供應商解耦；夾 Owner 工作流程 |
| D3 | 市場 | 美國期貨：**NQ（納指）、YM（道指）、GC（黃金）**；micro 版（MNQ/MYM/MGC）留作將來合約大細參數 | Owner 指定；三市場互相驗證穩定性 |
| D4 | 引擎 | **NautilusTrader**（LGPL-3.0 免費、官方 IB adapter、回測/live 同一套代碼、event-driven 支持狀態機） | Spec 嘅狀態機同 intrabar 精度要求非 event-driven 唔得 |
| D5 | 模擬盤執行 | **App 自家模擬成交**，只攞 IB 實時數據，**唔用** IB paper account | Owner 要留 paper account 自己手動練習；模擬器同回測共用保守成交邏輯 → live/backtest 可比 |
| D6 | 轉倉處理 | 用**個別合約**回測；**轉倉窗口 3 日**列入不交易日名單，唔做連續合約縫合 | 避開跳空失真；MVP 消滅縫合難題 |
| D7 | 策略範本 | **兩層積木**：宣告式條件積木 + 具名狀態機積木（FSM 庫：pullback_lifecycle、position_levels、lmr…），策略文件「點名+傳參」引用狀態機 | TRADING_SPEC 評估結論：~95% 可積木化；狀態機集中一處寫到極穩 |
| D8 | 策略正典 | **TRADING_SPEC_v0.41.md** 係策略內容嘅唯一正典來源（[課程原文] 不可擅改） | Owner 指定 |
| D9 | 參數管理 | 參數溯源矩陣三渠道：**檔（策略文件）／UI／系統預設**；策略參數喺 UI **唯讀**，改參數必經 terminal AI 出新版文件 | 文件係唯一真相來源，溯源乾淨 |
| **D9 修訂**（2026-07-25，Owner 拍板） | 參數管理 | **UI 可以改數值，但一改就生成新版本**（原版本不可變，來源標「Owner UI 微調」）。`structures`／入市序列仍然 UI 唯讀——改到咁深要返 terminal | 保住「每個 run 追溯得返用咩參數」，同時免咗為改一個數字行足一轉 terminal。詳見 `docs/ui/designs/p2-strategy-workbench.html` 約束 #17／#18 |
| D10 | 盤前計劃 | **文件模式**：Owner 每日標註重要 S/R 位（來源 TF + 強度）同當日方向睇法，app 讀入 | Owner 主觀判斷正式成為系統輸入；殺倉只係 S/R 最基本定義，重要位有層級（D>4H>2H>1H>30m） |
| **D10 修訂**（2026-07-26，Owner 拍板） | 盤前計劃 | **MVP 明確不做 A3、獨立設定頁或市場狀態輸入**；策略喺市況唔適合時應自然唔觸發。盤前 level、方向同相關參數全部留待 MVP 完成後重新研究，唔係目前閉環輸入 | 避免用一層主觀市況設定重複或覆蓋策略本身條件；先完成「結果晉升 → 新增交易員 → 模擬 → 偏離」最窄閉環 |
| D11 | 評估框架 | **八維度記分卡**（見 §5）；A 類自動、B 類簡化版 MVP、C 類 Phase 2 | 業界研究：單一指標誤導；零售平台缺穩健性測試係其弱點 |
| D12 | 晉升機制 | **人手拍板**：系統顯示記分卡 + 警告，唔自動把關 | Owner 意向；MVP 單用戶無需硬闸 |
| D13 | 保守原則 | Stop 先於 target、作廢先於入市、跳空以開市價成交、日終平倉清 pending（spec §14 直接成為引擎適配層規格） | 唔俾回測自我美化 |
| D14 | 記錄層 | Spec §13 record layer **第一日全開**：逐筆 tag，先記錄後統計，證實先升級做核心閘 | 平、將來值錢；同「加分項」驗證哲學一致 |
| D17 | 運維韌性包（2026-07-24；2026-07-31 Owner修訂） | P6 MVP：① runtime state及input journal逐次寫SQLite，crash後由ledger重建但**必須Owner手動resume**；② 任何斷線即freeze新decision/fill，完整盲區≤5分鐘可補回真bars後自動繼續，>5分鐘／缺資料／身份漂移即`tripped`；冇可信價格時保留position，**禁止虛構平倉**；③ 最大回撤8R（由runtime equity high-water計）＋最長連敗8個completed losing trades，觸發後停止新decision、有可信價先平倉、Owner睇證據後手動re-arm；④ MVP冇外部通知prerequisite，Telegram永久移除，PWA Web Push／Tailscale／cloud及外置備份列post-MVP。交易所行事曆仍係runtime preflight及session authority。完整優先規格：`docs/superpowers/specs/2026-07-31-p6-local-paper-runtime-mvp-design.md` | 模擬盤證據必須可重建、唔重複成交、唔因斷線造價；通知失效唔可以取代backend安全控制 |
| D19 | 多策略並行（2026-07-24；UI 於 2026-07-26 修訂） | **回測**：批量由「三市場」升級做「多策略 × 多合約」矩陣提交，run 隊列執行（先順序後多核），P5 對比頁並排睇——P1。**模擬盤**：一條 IB 數據流餵 N 個獨立模擬器實例（各自策略版本／虛擬帳戶／斷路器／狀態持久化／偏離記錄，deployment_id 做鑰匙，共用 MTF 預計算）——模擬盤 WO 由第一日按多實例設計；P6 採交易員分頁，總覽＋每個交易員一個分頁，唔係部署列表。新增交易員鎖策略版本＋合約＋Owner 親手揀嘅 baseline，帳戶初始資金跟 baseline。**界線**：呢個係 N 個平行獨立帳戶嘅 A/B 對照，唔係一個帳戶行 N 個策略（組合管理／保證金互動照舊 Phase 3） | 自家模擬器（D5）令多實例零券商約束；同市同時對照係最乾淨嘅策略比較實驗 |
| D18 | 視覺確認迴路（2026-07-24；UI 位置於 2026-07-26 修訂） | **圖表係確認迴路嘅核心工具**：所有視覺元素服務一個目的——確認「Owner 同 AI 溝通嘅策略」同「AI 理解後實際執行嘅策略」一致；唔一致由圖表睇出→修正溝通→再調教。落地五件：① **策略工作台分頁 ①草圖**（唔係獨立頁）：≥4 張 TF 圖可畫趨勢線/水平位＋逐圖文字判斷→匯出 `sketch.v1` 草圖包（PNG×4＋meta.yaml）俾 terminal AI；strategy.yaml 以 `based_on_sketch` 溯源；分頁 ②確認時左草圖右參數對照。② **事件敘事面板**：每筆交易由事件日誌自動生成人話判斷鏈。③ **多 TF 圖格**：每筆交易 2×2 四框架圖。④ **結果頁／模擬盤共用「交易檢視套件」**：模擬盤有齊結果頁全套視覺（圖表/標記/敘事/列表），另加 paper 獨有（斷路器/偏離/degraded）——前提係模擬器發出同回測同一款事件 schema。**⑤ 市場洞察庫**：策略工作台分頁 ④，同款圖文機器；匯出 `kind: insight` 圖文包 → AI 返回 `insight.v1`（量化條件＋建議參數＋record layer 量度方法）→ 洞察庫版本化存放（驗證狀態：unverified→recording→supported→rejected）；**兩條鐵律保策略獨立**：洞察不可變（改＝新版）、策略文件永遠自包含（`based_on_insights` 純溯源、執行層永不查庫）；supported 先可升級做具名積木（P2）——spec §13「先記錄後證實」哲學嘅圖文載體 | Owner 係視覺思考者；圖係佢策略嘅原生語言，純文字係翻譯 |
| D20 | Primary instrument 閉環（2026-07-27） | **一個草圖包一個 primary instrument**，Owner 明確選、asset class/name/currency/session 由 canonical catalog 帶入；首圖後鎖 instrument，匯出後整包不可變。Terminal `strategy.v1` 可提出同 asset class universe 擴展，但每個新增 member 要有理由；P2 只可整份接受／退回，唔用 checkbox 改策略。P4 先揀已確認 universe ∩ 有資料嘅本次 run 子集；run manifest 鎖 exact expiry contract。Insight 同樣保留 instrument/class，但仍純記錄 | 保住由 Owner 圖像意圖 → strategy universe → executable contract → result evidence 嘅身份鏈；避免 NQ/GC 語境混淆，同避免 App 靜靜改 Terminal 原文 |
| D21 | 洞察可恢復歸檔（2026-07-28） | 洞察庫唔做永久刪除；「歸檔」一次處理完整 `origin + insight_id` 及全部不可變版本，保存 exact bytes、file/source SHA、manifest及UTC刪除時間到 `data/insights/_deleted/<origin>/<insight_id>/<archive_id>/`。正常庫隱藏archive；可原byte恢復，但active collision時fail-closed、零merge／overwrite；MVP冇永久purge。Exact contract見`docs/superpowers/specs/2026-07-28-insight-recoverable-archive-design.md` | 洞察係小型研究證據，永久刪除幾乎無容量收益，卻會令誤刪不可逆並斬斷`based_on_insights`純溯源；分區archive保持active清晰，同時保留查證及復原能力 |
| D16 | 前端設計語言（2026-07-24） | **Apple 式高科技精緻路線**：懸浮半透明（glassmorphism）、排版留白主導、Apple 系統色參考、無 AI 味；網站 + iPhone PWA 同一標準。**全局 token 系統（`tokens.css`）係唯一樣式真相來源，組件層零 hardcode CSS**（lint + review 把關）；**三主題：深色／淺色／自然（2026-07-24 擴充）**，全部喺 token 層切換，組件唔知情；JS 圖表讀 token 繪製。原則見 `docs/04-frontend-design-system.md`；實際值以 `apps/web/src/styles/tokens.css` 為準 | Owner 指定美學方向；token 化令主題一致、可維護、可全局改 |
| D15 | Tech stack（2026-07-24） | **Python 3.11+ + NautilusTrader**（引擎核心係 Rust 編譯，Python 只做策略層）；後端 **FastAPI**；前端 **React + TypeScript + Vite**（圖表用 TradingView Lightweight Charts）；市場數據 **Parquet**（逐合約分區）；交易紀錄/狀態 **SQLite**。**防慢守則（硬性）**：(1) 指標一律用編譯版預計算，Python callback 內禁止 pandas 逐 bar 計算；(2) 每 bar callback 只做狀態機轉換同落單判斷，重計算全部推去預計算層 | 速度靠 Rust 核心已解決，唔使自寫 Rust/C#；單用戶 SQLite 零運維；參數掃描慢用並行解決，唔係換語言；逐件優化後門：個別狀態機可單獨搬 Cython/Rust |

## 3. 系統形態

**六頁 App**（MVP）：

| 頁 | 職責 |
|---|---|
| P1 總覽 | 行動式只讀中樞：聚合其他頁真正要 Owner 處理嘅事項，掣只導航；IB 探測只放底部細字；**MVP 冇盤前計劃摘要** |
| P2 策略工作台 | 四分頁：草圖、量化確認、版本庫、市場洞察；匯入、驗證、Owner 確認同 lineage 全部住同一頁 |
| P3 數據 | 「夠唔夠用」、待裁決、體檢記錄、補數據四段；交易日同時刻嚴格分開 |
| P4 回測 | 策略版本 × 合約批量矩陣 + 日期 + 資金 + 成本 + 成交假設；每個組合獨立帳戶 |
| P5 結果 | 對比總表＋單 run 詳情；圖表、逐筆因果、0 成交最接近三次、記分卡、`result.v1` 匯出同不可變 PromotionDecision |
| P6 模擬盤 | 交易員分頁；新增時鎖策略版本＋合約＋baseline，按 baseline 資金建立獨立帳戶；實時持倉/PnL、live vs 回測偏離記錄 |

**文件合約**（app 嘅對外接口，schema 說明書兼任 AI 指令書）：
1. **策略文件**（入）：兩層積木 + 參數 + `rationale`（必填）+ `unquantified_notes`（AI 譯唔到嘅嘢必須記低）
2. **結果文件**（出）：指標 + 記分卡 + 逐筆 tag，俾 terminal AI 讀嚟分析迭代
3. **盤前計劃文件**（入）：**Post-MVP 候選，MVP 不產生亦不消費**；格式及用途留待 MVP 完成後重新拍板

**數據模組**：1 分鐘做基礎 bar（大 TF 合成）；分段下載避 IB pacing；**由第一日永久存底**（IB 細 bar 歷史深度有限，過期冇得補）；四類自動質量檢查（完整性／合理性／異常值／自洽性）出質量報告，可疑日 Owner 裁決。

## 4. MVP 分期（已同 Owner 確認）

原則：Phase 1 = **最窄但完整嘅縱切面**（一個策略配置全程行得通），唔係「少啲功能」。

| | Phase 1（MVP） | Phase 2 |
|---|---|---|
| 策略 | Trend 主流程：三層 D/1H/5m、regime 主閘、18×90 EMA 交叉+第一次回踩、Inside/Magic bar、1R/stop、日終平倉 | LMR + 上倉/下倉狀態機 → Range 流程、daily 雙指標、強弱標籤 |
| S/R | **唔要求盤前計劃輸入**；策略按自己鎖定條件自然決定有冇訊號 | MVP 完成後重新研究人手 level／方向、超展開、值博率及自動偵測 |
| 數據 | IB 下載+儲存+質量檢查+轉倉名單 | 其他數據來源 |
| 回測 | 單 run + 對比、保守成交、1 分鐘執行粒度 | 參數掃描、walk-forward（70/30、6 段）、Monte Carlo（1000 次） |
| 評估 | 記分卡 A 類（5 維自動）+ B 類簡化（成本 ×1/×1.5/×2、三市場批量、按年切割） | C 類（參數平原自動化） |
| 圖表 | 結果頁圖表檢視器：K 線 + 策略指標線 + 交易標記 + 止損/目標線 + 跳轉 + TF 切換 + **事件敘事面板（D18）**；P6 共用交易檢視套件 | Regime 著色、LMR/倉結構視覺化、逐 bar 回放、多圖同步、盤前 level 上圖 |
| **P1.5 功能增量（D18；唔係獨立頁）** | 策略工作台 `sketch.v1` 圖文包、草圖↔參數對照＋dry-run 策略預覽、多 TF 圖格（2×2） | 繪圖類型擴充（矩形/斐波等） |
| 模擬盤 | 自家模擬器 + 交易員分頁 + baseline／獨立帳戶鎖定 + readiness fail-closed + 偏離記錄 | 偏離自動統計 |
| AI 循環 | 完整雙向文件合約 | — |

已知取捨（Owner 接受）：Phase 1 冇殺倉 → Daily=Range 日子唔開單，MVP 只喺 Trend 日交易。

## 5. 八維度記分卡

| 維度 | 計法 | 階段 |
|---|---|---|
| 1 樣本量 | 交易數（警告線 100）、交易數÷參數數（警告線 30:1）——自動 | P1 |
| 2 期望值結構 | 期望值/勝率/盈虧比/profit factor/最長連敗——自動 | P1 |
| 3 風險形狀 | 最大回撤、回撤持續時間、Calmar——自動 | P1 |
| 4 成本敏感度 | ×1/×1.5/×2 重定價——半自動 | P1 |
| 5 參數平原 | 掃描+WF+MC 自動化 | P2（P1 手動改參數用 run 對比頁替代） |
| 6 跨市場/跨時期 | 三市場批量 + 按年切割——半自動 | P1 |
| 7 簡潔度 | 由策略文件數規則/參數——自動 | P1 |
| 8 經濟原理 | `rationale` 文字欄 + Owner 晉升時自問——人手 | P1 |

**概率／長尾擴充（Owner 拍板 2026-07-24）**——全部係 record layer 只讀消費者，零新用戶輸入：

| 擴充 | 計法 | 階段 |
|---|---|---|
| 2b 好運依賴 | 利潤集中度（移除 Top-5/10 賺錢交易後淨利仲正唔正）、PSR（真 Sharpe>0 概率，閉式：樣本數+偏度+峰度） | P1 |
| 3b 長尾／分佈形狀 | 偏度、峰度、tail ratio、VaR95、CVaR95（尾部平均損失） | P1 |
| 5b 機率評估 | DSR（修正「試咗 N 組揀最靚」偏差，用 run 紀錄嘅試驗數）、MC 概率情境（P(回撤>X)、破產概率、模擬 1000 次）、bootstrap 置信區間 | P2 |

研究脚注:回測 Sharpe 對 OOS 表現預測力極弱(R²<0.025);回撤/波動類指標更有預測力;勝率要同盈虧比一齊睇,佢真正影響嘅係連敗長度同心理承受。PSR/DSR 出處:López de Prado — Deflating the Sharpe Ratio。

## 6. 開放事項（全部有主，唔阻進度）

| 事項 | 負責 | 時點 |
|---|---|---|
| ~~NQ/YM/GC 手續費滑點初值建議~~ | **已定（2026-07-24，矩陣 §5）**：全包每邊 NQ/YM $2.50、GC $2.80；滑點按訂單類型 1/2/0/1 tick | ✅ 結案 |
| 三份文件 schema 詳細設計 | Agent C 起草 → Owner 過目 | 實作規劃第一步 |
| 超展開/值博率門檻初值 | Owner + Agent C | Phase 2 前 |
| Golden test cases（標註圖段驗狀態機） | 實作階段建立 | 實作時 |
| Spec 第四至十堂內容（position sizing 正式版、trailing 等） | 老師講到即覆蓋 [課程原文] 優先 | 隨堂 |
| TRADING_SPEC_original 檔案（Owner 指有異版，但現存兩檔 byte-identical） | Owner 搵到再補 | 隨時 |

## 7. 相關文件

- `TRADING_SPEC_v0.41.md` — 策略正典（項目根目錄）
- `docs/01-parameter-coverage-matrix.md` — 參數溯源矩陣（~60 參數 × 渠道 × 頁面 × 消費者 × 階段）
- `docs/02-mvp-closed-loop-user-journey.md` — 完整閉環 user journey（S1–S9、8 種 artifact、逐步 gate）
- `docs/03-frontend-component-coverage-matrix.md` — **由六份權威設計稿倒推**嘅畫面 coverage／驗收清單；唔係設計來源，衝突時修矩陣
- `docs/08-ui-backend-mapping.md` — **由已定稿 UI 倒推後端**（六頁 × 要嘅數據 × 已有／要擴／全新）
- `docs/09-journey-closed-loop-audit.md` — **閉環審計**（逐個接口驗條鏈接唔接得上）

舊版 `docs/03`（獨立 P2.5／四卡總覽／部署列表年代）已於 2026-07-26 刪除，只可由 git history 追查；唔准引用。

## 8. 討論過程研究來源（擇要）

NautilusTrader 授權/IB adapter（nautilustrader.io）；QuantConnect 部署流程、Composer symphony 結構、StrategyQuant 穩健性漏斗（build → filter → robustness → incubation）；回測過擬合研究（Bailey et al.、GT-Score）；TradeStation WF/MC 默認值；Ehlers robustness；swing/pivot S/R 算法（TradingView、Entreprenerdly）。詳細連結見對話紀錄。
