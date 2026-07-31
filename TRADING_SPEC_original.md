# Trading Framework 規格書 (累積版)
版本:v0.41 | 涵蓋:第一、二、三堂 | 業界 audit 修正 + 實作預設填齊(script-ready)

> 標記說明:
> - **[課程原文]**:老師教嘅規則,唔可以擅自更改。
> - **[實作預設]**:課程未講、由業界標準填嘅預設;第四至十堂若有講法,以老師為準覆蓋。
> - **[AUTO] / [SEMI] / [MANUAL]**:可全自動 / 自動偵測+人手確認 / 人手判斷。
> - parameter = 可調參數;初值見第 15 節。

## 0. 文件約定
- 廣東話 + 標準金融/技術術語英文。
- 「一個最小跳動單位」= 1 tick / point(同義),視乎市場。用詞:「上倉」與「下倉」。
- **EMA 配置**:每層 TF 計 18 / 50 / 90 EMA。交易邏輯(交叉、pullback、invalidation)只用 18 同 90;50 EMA = 軟性參考線 [MANUAL],不入任何條件。
- **觸發精度**:入市 / 殺倉 / LMR 作廢 / pullback 掂線 / OCO 取消 / 價格盒突破,一律 intrabar 觸碰即觸發;EMA 交叉狀態以 bar close 後重算判斷。
- **閉市 bar 規則**:較高 TF 嘅指標值/判斷(daily EMA、regime、方向 context)只可取自已收市嘅該層 bar —— 今日日內交易,daily 判斷用「琴日收市為止」嘅 daily bar。價格事件(觸發類)不受此限。
- **Pivot 確認滯後**:所有 pivot 類結構(上倉/下倉/swing 點)以「確認 bar」為生效時點。防 lookahead / repaint。
- **三層 TF(trio config)[實作預設]**:
  - Bias TF 固定 = Daily。
  - 中 TF ∈ {4H, 2H, 1H, 30m},預設 **1H**。
  - 入市 TF ∈ {5m, 3m, 1m},預設 **5m**。
  - 一個 config 只行一個 trio;其他組合留 backtest grid 對比。比例約 ÷20–30、÷5–10 [課程原文]。
  - 入市層數 = parameter(兩層 = 中層入市 / 三層 = 細層入市)。
- 每層各計 18/50/90 EMA 及該層 ATR。Stop / R / target 用實際入市 TF 嘅 bar 計。
- **方向硬規定 [課程原文]**:殺下倉 / 上升 → long;殺上倉 / 下降 → short。中 TF 與細 TF 方向必須一致。方向只跟最新一次殺倉;受 daily plan(第 10 節)單方向 override。
- Pending signal 日終一律清零,翌日重新判斷。

## 1. 三層分工與入市總流程
### 1.0 核心原則
Regime 判斷(第 11 節)係每層 TF 通用工具,喺該層內獨立判。Daily regime 唔禁止細層用 trend 工具;daily 只影響 take profit 保守度 + 方向 context。

### 1.1 Daily:判 regime + 方向 context(閉市 bar 規則適用)
| Daily regime | 中層必須條件 | 加分 | Take profit(暫一律 1R) |
|---|---|---|---|
| Range | 殺倉 | EMA | 將來保守(如 2-3R)[remark] |
| Trend | EMA 交叉 + 第一次 pullback | 殺倉 | 將來進取(可 >3R)[remark] |
| Congestion | 唔開新單 [實作預設,原話「幾乎唔做」];record layer tag 該啲日子 | — | — |

### 1.2 中 TF:方向決定層
- 按 daily regime 執行必須條件 + signal bar 三選一(Magic / Inside / LMR;LMR 非必須)[課程原文]。
- 兩層模式:喺中層 signal bar 直接入市。

### 1.3 細 TF:執行層(統一,三層模式)[課程原文]
- 無論 regime,一律需要:細 TF 自己嘅 18/90 交叉(啱方向)+ 第一次 pullback(價格掂該層 90 EMA,見 12.2)+ signal bar → 突破入市。
- 只入第一次 pullback(每層各自計);交叉反轉 invalidation 每層適用;細 TF 未交叉 → 等,唔入。
- 方向必須同中層一致(硬性)。

### 1.4 離場
1R target / stop / 收市強制平倉。另:daily plan 單方向(第 10 節)、時段 soft guidance(第 7 節)。

## 2. Inside Bar(signal bar 之一)[AUTO]
### 2.1 定義 [課程原文]
最新已 close bar,H < mother H 且 L > mother L(OHLC 全 within 由此自動保證);可連續(double/triple),多重用最新一支 inside bar,mother bar 維持最初嗰支。
### 2.2 觸發與 entry
- 預設(業界 classic)[實作預設]:Long = 升穿 **mother bar high**;Short = 跌穿 **mother bar low**(intrabar)。
- 變體(parameter,課程原版 aggressive)[課程原文]:Long = 升穿 inside bar high;Short = 跌穿 inside bar low。
- 日內有效直到觸發;日終清零。失效 = 方向條件失效 / LMR 作廢 / 交叉反轉 / OCO 取消(2.4)/ 日終。
### 2.3 Stop Loss
- 預設(業界 classic)[實作預設]:Long stop = mother bar low − 1 tick;Short = mother bar high + 1 tick。
- 變體(parameter,課程原版)[課程原文]:mother bar mid = (H+L)/2 揀基準 —— Long:inside low 喺 mid 上 → 基準 inside low;mid 下 → 基準 mother low;Short 鏡像;基準 ±1 tick。
### 2.4 OCO 取消 [實作補充]
Pending 期間,價格 intrabar 觸及該 signal 對應嘅 **stop 價位** → pending 即取消(當日之後可有新 signal bar 重新形成)。
### 2.5 Remark
多重 inside bar 突破成功率可能較高(純觀察)。Inside bar 相對前一支 = LH+HL,喺向上 LMR step2 期間貢獻 LH flag,不打斷流程。

## 3. Magic Bar(signal bar 之一)[AUTO]
### 3.1 定義 [課程原文]
自己 mid = (H+L)/2。Long magic bar:close 喺自己 mid 之上,且該支 low 低於前一支 low(兩者硬性)。Short 鏡像(close 喺 mid 下 + high 高於前一支 high)。
### 3.2 觸發 [課程原文]
Long 升穿 magic bar high;Short 跌穿 magic bar low(intrabar)。日內有效;日終清零;失效同 2.2(含 OCO)。
### 3.3 Stop [課程原文]
Long = magic bar low − 1 tick;Short = magic bar high + 1 tick(無 mid 揀法,故意)。
### 3.4 OCO 取消 [實作補充]
同 2.4。

## 4. Risk / Position Sizing 執行 / 離場 [AUTO]
- R = |entry − stop|;target = 1R(Long entry+R / Short entry−R)[課程原文,暫定]。
- 離場:掂 target 全平 / 掂 stop 全平 / 收市強制平倉 [課程原文]。
- **Backtest 慣例 [實作補充]**:
  - 同一支 bar 內 target 同 stop 都掂到 → 優先用更細一層數據判先後;無法判時保守假設 **stop 先**。
  - 開市跳空穿過 entry / stop level → 以**開市價**成交。
  - Slippage / 手續費模型:backtest 階段先定。
- **Record layer**:每單 tag MFE / MAE(最大浮盈/浮虧,R 單位)—— 為將來 trailing / take profit 研究儲數據 [實作預設]。
- Time stop(N 支 bar 未達標即走)= off(parameter 留位)[實作預設]。
- Remark(待議):將來按 daily regime 分 take profit 進取度(Trend 進取 / Range 保守);trailing / 分段止賺待後堂。

## 5. 上倉 / 下倉(殺倉)[AUTO]
### 5.1 上倉結構 [課程原文](組頭 = 形成新高嗰刻 bar)
1. 資格:組頭緊接前兩支 high 雙雙低過組頭 high。
2. 平頂組:組頭起一支或多支 bar,high 維持同一高位(可平頂),全部高過前兩支;組內 low 無限制。
3. 確認:之後出現一支 high 較低 bar → 成立(以此確認 bar 為生效時點)。
4. 成立前資格:成立前該 level 未被價格升穿過(單一普通高 bar、非緊接前兩支者不影響)。
→ Level = 平頂組 high。(確認 right=1;傳統 fractal right=2 —— 跟老師,record layer tag 觀察。)
### 5.2 有效/無效 [課程原文]
成立後價格升穿其 high 即無效(單支亦計)→ 殺上倉。(A) 價格升穿(任何 bar);(B) 後來更高有效倉位取代(A 之延伸)。只被後來更高者殺;後來較低者不殺前者、自身有效。可並存「左高+右低」;序列左→右遞減。
### 5.3 交易時用最接近現價嗰個有效上倉/下倉 [課程原文]。
### 5.4 殺上倉(intrabar 升穿生效上倉 high)→ short [課程原文]。原理(註):莊家於上倉派貨。業界同族 = 2B / Turtle Soup / liquidity sweep;呢族需「失敗確認」—— 本框架以 LMR / signal bar 內建確認 ✓。
### 5.5 下倉 = 上倉完整 mirror;殺下倉 → long [課程原文]。
### 5.6 雙重用途:倉位亦係客觀 swing 點(上倉 = swing high、下倉 = swing low),用於 11.5 強弱標籤。

## 6. Position Sizing [實作預設,待後堂覆蓋]
- Fixed fractional risk:每單風險 = equity × risk_pct;手數 = (equity × risk_pct) ÷ (R 距離 × 每點價值),向下取整。
- risk_pct 初值 1%(parameter,範圍 0.5–2%)。
- 每日最大虧損:累計 −3R 即日停開新單(parameter)。
- (註:課程第 6 節 position sizing 未講;「殺上/下倉」用詞與 position sizing 關係待老師釐清。)

## 7. 交易時段 Remark(soft,預設不啟用)[課程原文]
開市頭 2 小時最有效(parameter);只做日市朝早(例:納指約 9:15/9:30 起);收市/夜期效果差,傾向唔做。Record layer tag 時段。

## 8. LMR(LiverMore Reversal)[AUTO]
### 8.0 雙重角色 [課程原文]
Daily = 非常重要嘅方向指標 [SEMI];入市層 = signal bar 三選一之一(非必須)。業界對應:Trader Vic 1-2-3 反轉 / SMC CHoCH。
### 8.1 判斷機制 [課程原文]
HH:high>前一支 high;HL:low>前一支 low;LH:high<前一支 high;LL:low<前一支 low(全部對「緊接前一支」比)。一支 bar 可同時達成兩 flag;flag 跨支累積、可同支、次序不限;貫穿 step1/step2。
### 8.2 Step 0 與作廢 [課程原文]
- Step0 = baseline:向上 = 新低支;向下 = 新高支。
- 作廢(硬性,全程至入市前,intrabar):向上 = 任何 bar low 觸碰/跌穿 step0 low;向下 = 任何 bar high 觸碰/升穿 step0 high。
- Hard override 凌駕入市(同支兩者皆中 → 作廢優先)。作廢後由零重算,以觸碰支 low/high 做新 step0。
- 跨層(三層模式):中層 step0 被穿 → 整個 LMR(連細 TF)即時作廢,回中層重計。
- 推論:step0 必然全段最低/最高(作廢全程監察保證)。LMR 屬滾動結構,毋須另設 OCO(step0 作廢已覆蓋)。
### 8.3 向上 LMR [課程原文]
Step0 新低 → Step1 集齊 HH+HL → Step2 集齊 LH+LL。
- 入市(滾動):step2 形成後,逐支以「最近已 close bar 嘅 high」為 level,升穿(intrabar)→ long。Step2 期間後續仍 LH+LL 之 bar 屬 step2 延續,最近 bar 即當下 signal bar。Entry = 被升穿嗰支 high。
- Stop:預設 **A** = step2 段內最低 bar low − 1 tick(動態更新)[實作預設:A 為預設];B = step0 low − 1 tick(parameter 變體)。
### 8.4 向下 LMR [課程原文]
完整 mirror(Step0 新高;Step1 LL+LH;Step2 HH+HL;跌穿最近 bar low → short;Stop A = step2 段最高 high + 1 tick / B = step0 high + 1 tick)。
### 8.5 Record-layer tag [實作預設]
每個 LMR 記 step1 腿幅÷ATR、step2 腿幅÷ATR(唔擋單);樣本夠後統計驗證,先決定是否升級做 filter。

## 9. 進階 LMR(三層模式之 LMR signal)[課程原文][AUTO]
- 中層完成 step1 + 半 step2(向上只需 LL,唔使 LH)→ 落細 TF;細 TF 執行層統一規則適用(1.3)。
- 中層補出 full step2 仍可落細 TF 入市(較進取,parameter)。
- 跨層作廢見 8.2。方向各層一致。

## 10. Daily Trading Plan(單方向)
- 每日只做單一方向;所選方向出咗條件但冇 signal 入到市 → 當日不再 trade(不等下次、不反手)[課程原文]。Override 第 0 節反手機制。
- **方向預先揀定 [實作預設,待後堂覆蓋]**:daily bias = 第 11.8 節雙 indicator 輸出 —— 一致 → 該方向;背馳 → daily LMR 方向(需中層對應交叉 confirm 先入);daily congestion → 唔做。

## 11. Market Regime 判斷(逐層 TF 通用)
### 11.1 概念
同一套判斷喺任何 TF 內獨立行(用該層 EMA / ATR / bar);Daily 判斷遵守閉市 bar 規則。三種 regime:Trend / Range / Congestion [課程原文:質性定義;以下量化為實作版]。
### 11.2 主閘 [實作版,經用戶確認]
Trend ⟺ 分開 AND 有斜度(缺一即非 trend):
- 分開:|EMA18 − EMA90| ÷ ATR ≥ sep_mult。
- 斜度:|EMA90(now) − EMA90(N 支前)| ÷ (N × ATR) ≥ flat_mult。
- sep_mult / flat_mult 校準 [實作預設]:用該指標自身歷史分佈 percentile 定界(初值:60–70 百分位),避免絕對魔術數字。
### 11.3 非 trend 分 Range / Congestion
recent K 支平均 TR < baseline ATR × shrink_mult → Congestion;否則 Range。
### 11.4 Daily 確認證據(價格盒 / Donchian containment)[實作補充]
最近 N 日價格未突破之前高低盒(intrabar 觸穿即算突破)→ range 證據。
### 11.5 強弱標籤(trend 之下,零參數)[課程原文]
倉位序列:最少 2 上倉 + 2 下倉;齊齊遞升(升 trend)= 強;缺任一 / 矛盾(如上倉升、下倉跌)= 弱。強 → 傾向入市/坐耐;弱 → 傾向唔入/坐短(暫 1R 下作 record layer 記錄)。
### 11.6 波幅擴張 Remark [課程原文觀察]
Daily=range 但波幅擴張 → 細 TF trend 入市機會增多(描述性;record layer tag ATR 比率;唔做觸發)。
### 11.7 方向雙 indicator [課程原文]
LMR / 倉位 + EMA 交叉狀態(黃金交叉緊 = 當前向上;死亡交叉緊 = 當前向下)。一致 → 行對應 flow;背馳(死亡交叉+向上 LMR 或 mirror;daily LMR 較重要)→ 等中層對應交叉 + 第一次 pullback confirm 先入。EMA 斜度判讀 = soft [MANUAL],不入邏輯。
### 11.8 保留註記(均已被取代,留底備查)
課程原版 Range %(相鄰日高低差 ≤0.2–0.3%)/ ER+ATR 方案 / 斜率衰減比 / 「快線掂慢線」pullback 界定 + 結構式取消選項 C(經業界 audit 由 12.2 現版取代)。

## 12. EMA 方法(逐層通用)
### 12.1 概念 [課程原文]
18/90 為邏輯線(50 = 參考線)。Range / Trend 都用得(必須/加分身份見 1.1;分別亦在將來 take profit 進取度)。上升 = 黃金交叉(18 升穿 90);下降 = 死亡交叉(bar close 後判斷)。
### 12.2 第一次 pullback [課程原文,經 audit 還原]
黃金交叉後,**價格回踩掂到該層 90 EMA**(bar low ≤ EMA90,intrabar 觸碰即算)→ pullback 到位 → 搵 signal bar → 突破入市。死亡交叉 mirror(bar high ≥ EMA90)。
- Parameter 變體:掂 90(預設)/ 掂 18 / 兩線之間 value zone。
### 12.3 第一次 pullback 起終點 [實作補充]
起 = 交叉後價格首次掂 90 EMA;終(任一):(i) 成功入市;(ii) 價格升穿「pullback 開始前嘅 swing high」(= 交叉後至首掂之間最高 high)→ 完結,唔再入;(iii) 交叉反轉 → 取消。每個交叉只有一次入市機會 [課程原文:只入第一次,之後力度弱];每層各自計。
### 12.4 Invalidation(硬性,入市前,每層適用)[課程原文]
- 交叉反轉:18 EMA 收市重新穿返 90 EMA → 取消(連第一次 pullback 都唔入)。
- 反向殺倉:即使 EMA 未反轉,殺咗反向倉 → pending 即失效。
- 入市後不受此限(交由 stop / target)。
### 12.5 趨勢有效期 [課程原文]
交叉後持續至反向交叉;入市機會只限第一次 pullback。

## 13. 實作策略(分層實作;規則本體不變)
- **核心閘(擋單)**:regime 必須項、signal bar + 突破、風控作廢(step0 / 交叉反轉 / 反向殺倉 / OCO)、日終清零 / 收市平倉、日虧損限額。
- **記錄層(唔擋單,每單 tag)**:殺倉加分有無、signal 類型、regime 強弱、時段、兩層/三層、多重 inside、LMR 腿幅÷ATR、ATR 擴張比率、MFE/MAE。
- 事後分組統計驗證「加分」類 claim;證實先升級做核心閘。

## 14. 事件優先次序(逐支 bar 處理次序)[實作補充]
1. **Bar 開市**:處理跳空 —— 開市價已穿 entry/stop level → 以開市價成交。
2. **Intrabar 事件**(同支 bar 多事件):
   a. 作廢類優先於入市類:LMR step0 作廢、OCO 取消、反向殺倉 → 先處理;entry 觸發 → 後。
   b. 持倉中同支 bar 掂 stop 又掂 target → 用更細數據判先後;無法判 → stop 先(保守)。
   c. 任何同類衝突 → 一律取消/保守方優先。
3. **Bar close**:重算 EMA / 交叉狀態 / 交叉反轉 invalidation / pivot 確認 / regime 更新(閉市 bar 規則)。
4. **日終**:強制平倉 + 全部 pending 清零。

## 15. Parameter 初值表 [實作預設;全部可調,backtest sensitivity]
| Parameter | 初值 | 備註 |
|---|---|---|
| EMA periods | 18 / 50 / 90 | 邏輯用 18/90;50 參考 |
| ATR period | 14 | Wilder 預設 |
| TF trio | D / 1H / 5m | 中 TF ∈{4H,2H,1H,30m};入市 ∈{5m,3m,1m} |
| 入市層數 | 三層 | 兩層 = 變體 |
| sep_mult / flat_mult | 自身分佈 60–70 percentile | 免絕對數 |
| 斜率窗 N | Daily 5;intraday 10–20 | 業界慣例 |
| shrink_mult | 0.6 | 範圍 0.5–0.7 |
| Congestion 觀察 K | D 2–3;4H 5–6;1H 8–10;30m 12–15;5m 20–30 | 課程比例推導 |
| 價格盒窗口 | 20 日 | Donchian 經典;10 為變體 |
| risk_pct | 1% | 範圍 0.5–2% |
| 日虧損限額 | 3R | prop firm 慣例 |
| Take profit | 1R | 暫定;將來按 regime 分 |
| Inside bar entry/stop | mother bar 版 | 課程 aggressive 版為變體 |
| LMR stop | A(step2 段)| B(step0)為變體 |
| Pullback 掂線 | 90 EMA | 18 / value zone 為變體 |
| Time stop | off | 留位 |
| 時段 filter | off(record tag on)| 開市頭 2 小時 remark |

## 附錄 A:Indicator / 公式對應(寫 script 用)
| 概念 | 對應 | 類別 |
|---|---|---|
| 上倉/下倉 | Pivot High/Low(left=2, right=1, 平頂延伸 + 未被穿 filter)| 現成改造 |
| 殺倉→反向 | 2B / Turtle Soup / CHoCH(sweep + 內建確認)| 經典 pattern |
| LMR | Trader Vic 1-2-3(HH/HL/LH/LL flag 狀態機)| pattern + 狀態機 |
| Inside bar | H<H[1] AND L>L[1];entry = mother bar 突破 | 公式 |
| Magic bar | CLV>0(close>mid)AND L<L[1];pin-bar 式 entry/stop | 公式 |
| 交叉 / pullback | crossover(EMA18, EMA90);pullback = bar low ≤ EMA90 | 現成 |
| Regime 主閘 | normalized MA spread + slope(÷ATR,percentile 校準)| 公式 |
| Congestion | TR 收縮(TTM Squeeze 族)| 公式 |
| 價格盒 | Donchian containment | 現成 |
| 強弱 | 倉位序列 HH/HL(零參數)| 結構 |
| 波幅 | ATR / ATR 比率(非 ADX)| 現成 |
| Pullback 生命週期 / 作廢 / 跨層 / daily plan / OCO | 狀態機(finite state machine)| 標準寫法 |

## Open Items(全部已有預設,唔阻 script;老師後堂講到即覆蓋)
- 第四至十堂:position sizing 正式版、trailing / 分段止賺、坐倉時間、daily plan 細節等。
- 用詞:「殺上/下倉」與 position sizing 是否同概念(後堂)。
- Golden test cases(驗收用標註圖段):喺 implementation conversation 建立。
