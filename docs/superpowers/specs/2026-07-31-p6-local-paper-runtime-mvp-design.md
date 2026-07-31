# P6 local app-owned paper runtime MVP design

日期：2026-07-31
作者／設計 authority：Owner ＋ Agent C-v5
狀態：**Owner 已於 2026-07-31 批准完整書面規格；implementation 只可由
`AGENT_CHANNEL_W.md` 最新 one-shot 工作令啟動**
執行者：Agent W（P6 唯一 full-stack feature owner）

> 呢份文件係設計 authority，**唔係自行開工授權**。
> Agent W 只可喺 `AGENT_CHANNEL_W.md` 見到 C 後續完整工作令先開始。

---

## 0. 結論及成功定義

P6 MVP 要完成以下本機工程閉環：

```text
IBKR Gateway read-only market data
→ app 驗證／分類／保存行情
→ app 鎖定策略作決定
→ app 自家模擬 order／fill
→ 獨立虛擬帳戶、持倉、realized／unrealized PnL
→ 圖表、斷線處理、8R／8連敗安全控制
→ 同鎖定 baseline 比較
→ immutable paper-review.v2
→ Terminal 衍生新 strategy.v1
→ 重新走策略／回測／結果／PromotionDecision／P6
```

最高候選 verdict：

```text
TRUE E2E PASS — P6 local app-owned paper runtime
```

呢個 verdict 只可喺真 browser、真 registered backend、真 default P6 DB、
真 IBKR Gateway market-data session及完整 protected-facts evidence全部通過後聲稱。
隔離 replay PASS 唔可以冒充真 Gateway E2E；真 Gateway transport PASS亦唔可以冒充
strategy／fill／safety全部已被自然市場觸發。

---

## 1. Authority、precedence及舊文件退役

### 1.1 接受基線

```text
accepted product baseline
7575e66ce668c910ab109eecc7bb9b34373914ee

X final accepted correction
88b153d0d04e7a46d40a48cbc56787da4d570046
[X-147] COMPONENT PASS

Y final accepted correction
19ff5ff
[245] COMPONENT PASS

transition／old W activation commit
369ef838b8054084032a78ea4fa19da0fc882038
```

`[W-ACTIVATE]` A4 從未由 Owner 轉發、W 從未開始，現被本設計及後續
單一完整工作令取代。舊 A4 只保留喺 channel history作審計，唔再有執行權。

### 1.2 現行 authority次序

發現衝突時按以下次序：

1. Owner後於2026-07-31嘅直接書面裁決；
2. 本設計；
3. 後續由C寫入`AGENT_CHANNEL_W.md`嘅完整工作令；
4. accepted product source及tests；
5. 現行project contracts／journey docs；
6. 被取代P6 specs、舊X/Y work orders及舊視覺稿已從現行工作樹刪除；如C為
   事故審計從Git history精確取回，亦只作lineage，不作permission。

舊P6文件如包含以下內容，一律由本設計取代：

- Telegram係MVP prerequisite；
- 斷線超過5分鐘但冇可信價格仍「自動平倉」；
- 1m／30m係永久固定timeframe；
- A4／A5／A6要分開等C逐批review；
- IB／runtime／default P6 write仍未獲Owner批准；
- 舊`paper-review.v1` zero-runtime profile可以代表真runtime evidence。

### 1.3 文件清理原則

- 已完成／失效implementation plans從現行工作樹刪除；
- X/Y舊work orders從現行工作樹刪除；
- 舊P6 HTML／PNG及舊full-flow HTML從現行工作樹刪除；
- Git history保留原bytes，但future agent正常閱讀路徑唔會再見到第二份真相；
- W handover、PROJECT_STATE、journey、coverage及mapping只指向本設計；
- 真implementation通過後先重建current P6及full-flow HTML，避免設計稿冒充實作。

---

## 2. 永久產品邊界

### 2.1 IB只係market-data provider

```text
IB/IBKR Gateway
  allowed: historical／realtime／delayed market data
  forbidden: live order、IB paper-account order、cancel/modify order、
             account portfolio作app paper ledger authority
```

App自己擁有：

- strategy decision；
- pending intent；
- simulated order及fill；
- virtual cash／equity；
- positions；
- realized／unrealized PnL；
- safety state；
- divergence及review evidence。

產品文案只可講：

```text
IB行情驅動嘅app自家模擬成交
```

禁止講「IB paper trading」或令人以為app會向IB paper account發單。

### 2.2 IBKR Gateway連線資料

Owner使用已登入嘅 **IBKR Gateway (Simulated Trading)**：

```text
IB_HOST=127.0.0.1
IB_PORT=7498
IB_CLIENT_ID=7
```

- Gateway API Settings保持`Read-Only API`；
- `Master API client ID`保持空白；
- client ID `7`由app連線時提交；
- app唔保存或讀取Owner嘅IB帳號、密碼或二次驗證資料；
- `8876`係過往temp FastAPI port，唔係IB port。

官方背景：
`https://interactivebrokers.github.io/tws-api/initial_setup.html`

Read-only API係第二層保護；第一層保護係產品code根本冇order transport path。

---

## 3. MVP scope及post-MVP boundary

### 3.1 今次必做

- 真Gateway handshake及market-data subscription；
- truthful `LIVE`／`TEST-DELAYED`分類；
- local persistent runtime；
- 多trader共享行情、獨立策略及帳戶；
- strategy decision；
- app simulated order／fill／trade；
- positions及PnL；
- configurable timeframe contract；
- runtime chart及timeline；
- pause／resume／permanent stop；
- disconnect／restart recovery；
- 8R／8連敗／5分鐘blind safety；
- runtime divergence；
- `paper-review.v2`；
- frontend、backend、contracts、tests、browser及default operational E2E。

### 3.2 今次明確唔做

- Supabase；
- cloud-hosted backend；
- public Internet exposure；
- Tailscale remote access；
- installable PWA；
- iPhone Web Push；
- Telegram；
- Windows service／automatic startup；
- IB live或paper orders；
- P3 available-data → P4 date handoff。

P3→P4係另一個獨立MVP缺口，唔准W順手擴scope。

### 3.3 Post-MVP backlog

1. iPhone installable PWA及Web Push；
2. Tailscale Serve私有HTTPS remote access；
3. optional cloud／Supabase研究；
4. Windows auto-start service；
5. 3m／5m／15m／1h及Owner自訂timeframe正式enable；
6. 更長市場時段soak／stress／chaos validation；
7. 本機paper DB及runtime evidence外置／雲端備份。

Telegram已從產品方向移除，唔列入backlog。

### 3.4 Telegram removal semantics

- normal P6 UI、runtime preflight、config及provider code唔再有Telegram；
- 唔新增token／chat ID env vars；
- runtime安全控制唔依賴通知；
- 舊immutable `paper_readiness_snapshot.v1`如包含Telegram欄，只可由明確legacy
  parser保留可讀，唔代表Telegram仍係產品功能；
- legacy compatibility code不得發network request、不得在normal UI顯示、不得
  阻擋create／start；
- W要刪除已無任何legacy read需要嘅dead Telegram product code及tests，保留嘅
  compatibility boundary要有命名及tests證明只讀。

---

## 4. User journey

```text
PromotionDecision = use
→ P6只列eligible immutable strategy version
→ Owner揀contract
→ Owner親手揀baseline run
→ Owner揀MVP已enable timeframe
→ 建立provisioned trader＋獨立帳戶
→ runtime preflight
→ Owner明確按「開始模擬交易」
→ market data／decision／simulated execution／PnL
→ pause／resume／permanent stop
→ paper-review.v2
→ Terminal opener
```

「建立trader」同「開始runtime」係兩個獨立動作。建立成功永遠唔代表：

- Gateway已連線；
- market session已開；
- runtime已開始；
- 已有order／fill；
- safety已ready；
- 可以向IB發單。

---

## 5. Timeframe-agnostic contract

### 5.1 核心模型

Timeframe係typed configurable value，唔係散落literal。最少分開：

```text
market_input_timeframe
execution_timeframe
chart_display_timeframe
strategy_timeframe_profile（由 immutable strategy.v1 鎖定）
```

MVP capability registry按**角色**enable：

```text
market input       1m
paper execution    1m
chart display      1m／30m
strategy profile   由 strategy.v1 提供；strategy-0003 = D／1H／5m
```

呢個role-aware allowlist只可存在單一backend capability/config authority。
Frontend由API讀capabilities，唔自己維護第二份永久清單。

`1m／30m`係Owner批准嘅MVP runtime／display capability，**唔係改寫既有策略**
嘅時間框架。尤其`strategy-0003`嘅bias／mid／entry仍然係immutable
`D／1H／5m`；P6唔准將entry decision改成30m去換取較快測試。30m只係MVP可選
display interval；30日係deterministic replay測試範圍。

### 5.2 Identity及儲存

Timeframe必須進入：

- create selection；
- trader immutable identity；
- immutable strategy timeframe profile；
- baseline compatibility check；
- provider topic key；
- bar identity／dedupe key；
- processing cursor；
- chart cache key；
- runtime snapshot；
- review artifact。

禁止：

- unsupported value默認跌返1m或30m；
- frontend顯示30m但backend其實返回1m chart；
- client用runtime／display selection覆蓋strategy.v1嘅D／1H／5m；
- baseline timeframe mismatch後自動轉換；
- storage/cache漏timeframe造成cross-series污染。

Unsupported value回closed `unsupported_timeframe`。

### 5.3 聚合及future extension

- higher timeframe只由完整closed base bars聚合；
- 聚合器按`TimeframeSpec.duration`／calendar boundary運作，唔寫死「30條1m」；
- partial/forming bar只供畫面，唔入strategy decision或execution；
- 現有`ConservativeExecution.process_minute()`語義要由adapter／generalized boundary
  隔離，保持MVP 1m行為不變，但其他層唔可以永久依賴方法名或固定60秒；
- future interval仍要逐個驗證session boundary、gap policy及execution ambiguity，
  唔係加一個字串就自動聲稱已支援。

### 5.4 Tests

- 1m market input／execution及1m／30m display係正式positive cases；
- strategy-0003必須仍由D／1H／5m immutable profile驅動；
- temp capability config注入例如5m，證明frontend→API→runtime identity→storage key
  能完整傳遞；
- temp注入測試唔代表5m已在normal UI enable；
- unknown、disabled或不相容timeframe要fail closed。

---

## 6. 唯一runtime architecture

```text
IBKR Gateway read-only feed
→ MarketDataAdapter
→ provider/session/mode identity
→ forming display update ＋ closed canonical bar
→ append-only market input journal
→ MarketDataBus（每contract＋input timeframe一個topic）
→ TraderRuntime × N
→ shared strategy core
→ shared conservative execution core
→ atomic paper ledger transaction
→ REST snapshot/timeline/chart APIs
→ React polling consumer
```

### 6.1 `MarketDataAdapter`

責任：

- 建立唯一client ID 7 session；
- request market data only；
- classify provider data type；
- canonicalize timestamp及contract；
- 分開forming／closed bar；
- detect duplicate、out-of-order、gap及identity drift；
- append input journal後先broadcast。

任何order-related method、callback mapping或account-order state都唔屬此adapter。

### 6.2 `MarketDataBus`

- 每`contract × market_input_timeframe × provider mode`最多一個upstream feed；
- 多trader只收immutable broadcast；
- subscriber唔可以改共享bar；
- 一個trader failure唔拖死其他trader；
- provider disconnect只做一次重連，per-trader safety各自處理。

### 6.3 `TraderRuntime`

每個trader鎖定：

- trader／account／ledger origin IDs；
- strategy version及content identity；
- contract；
- baseline run及artifact identities；
- typed runtime／display timeframes及immutable strategy timeframe profile；
- data mode；
- initial capital；
- execution cost policy；
- safety parameters。

每個trader有獨立：

- strategy FSM；
- execution FSM；
- processing cursor；
- account；
- pending intent；
- position；
- trades；
- equity；
- safety及lifecycle state。

### 6.4 Shared strategy／execution authority

- runtime重用現有strategy core；
- runtime重用`ConservativeExecution`嘅fill、OCO、stop／target、cost及session-close語義；
- 唔准另寫「較簡單paper算法」；
- decision只食closed strategy timeframe bars；
- execution只食closed execution timeframe bars；
- same input identity只可產生exact一次decision／fill side effect；
- 回測與runtime如需共同修正，必須抽到同一helper及同一golden tests。

### 6.5 Frontend更新

MVP用bounded cursor polling，唔新增WebSocket：

- runtime snapshot低頻輪詢；
- timeline／chart用`after_cursor`增量讀；
- response帶trader、generation、timeframe、mode及as-of identity；
- stale／cross-trader response丟棄；
- browser關閉唔影響backend runtime。

---

## 7. Market-data truth

### 7.1 Closed modes

Public wire closed enum：

```text
live
test_delayed
replay_test
```

Owner-facing labels：

```text
LIVE
TEST-DELAYED
REPLAY-TEST（只限test harness，normal UI不可見）
```

`LIVE`只可由IB session明確報告即時行情。
Delayed／frozen／unknown唔可以被UI或artifact重命名為live。

`test_delayed`（UI：`TEST-DELAYED`）：

- Owner明確選擇先可啟動；
- 總覽、detail、chart及review全程顯示；
- 可驗MVP功能；
- 唔可成為「real-time performance」證據。

`replay_test`（test evidence label：`REPLAY-TEST`）：

- 只在OS-temp／test harness；
- 價格來自已保存真IB market data；
- test-only strategies可強制不同路徑；
- 唔進normal catalog、PromotionDecision、default P6 DB或Owner normal UI。

### 7.2 Journal及identity

每個closed input最少包含：

- provider session ID；
- contract identity；
- timeframe；
- provider data mode；
- event timestamp及received timestamp；
- canonical OHLCV；
- payload/content SHA；
- source kind（live／recovered）；
- unique monotonic input identity。

Unique constraint必須令duplicate callback或restart replay保持exact-once。

### 7.3 Forming bars

- forming bar可更新chart；
- 明確標示未完成；
- 唔入decision、fill、trade或persistent closed-bar cursor；
- 完成後以同一series identity轉closed，不可同時保留兩根重複bar。

---

## 8. Lifecycle及command model

Closed public lifecycle：

```text
provisioned
starting
running
pausing
paused
tripped
stopping
recovery_required
permanently_stopped
```

主要轉換：

```text
provisioned → starting → running
running → pausing → paused
running／pausing → tripped
running／paused／tripped／recovery_required → stopping
stopping → permanently_stopped
crash/restart from non-terminal active state → recovery_required
paused／tripped／recovery_required → starting（Owner手動resume）
```

### 8.1 Runtime command identity

每個state-changing command必須有：

- schema；
- request ID；
- trader ID；
- expected lifecycle version；
- exact immutable selection fingerprint。

同request＋同payload重試返回同一結果；同request＋不同payload回
`request_id_conflict`；stale expected version回closed conflict，零副作用。

### 8.2 Start preflight

新`paper_runtime_preflight.v1`取代Telegram-era runtime gate，至少檢查：

1. immutable strategy／contract／baseline identity；
2. baseline artifact integrity；
3. timeframe capability及compatibility；
4. Gateway API handshake＋read-only market-data capability；
5. truthful market-data mode；
6. exchange calendar／market session；
7. paper store schema／integrity；
8. single-writer runtime lease；
9. cursor／journal continuity；
10. lifecycle及safety eligibility。

Notification唔係check。歷史creation record內舊readiness snapshot保持immutable，
但唔再用作runtime start authority，亦唔在normal UI顯示Telegram。

### 8.3 Manual startup

- Owner手動啟動backend；
- browser可之後關閉；
- backend／電腦重開後絕不自動resume trader；
- startup先由ledger重建，再將未完成active lifecycle置為
  `recovery_required`；
- Owner睇完recovery evidence及新preflight先可resume。

Windows service／auto-start留post-MVP。

---

## 9. Safety model

Default locked parameters：

```text
max_drawdown_r = 8
max_losing_streak = 8 completed losing trades
blind_minutes = 5
```

三個值係typed config，建立trader時鎖定；MVP UI可唔開放修改，但唔准散落literal。

### 9.1 Drawdown

- 使用shared execution/result authority嘅`net_r`；
- completed trade累加realized R；
- open position用immutable entry-risk denominator計unrealized R；
- `equity_r = realized_r + unrealized_r`；
- high water係由trader開始後可信mark計到嘅最高`equity_r`；
- `drawdown_r = equity_r - equity_high_water_r`；
- `drawdown_r <= -8`觸發trip；
- 禁止runtime另創同backtest不一致嘅R公式。

### 9.2 Losing streak

- completed trade `net_pnl < 0`加一；
- completed trade `net_pnl >= 0`重設為0；
- open position、cancelled intent、zero-fill唔當一單；
- streak達8觸發trip。

### 9.3 Trigger action

Trip時原子地：

1. 禁止新decision；
2. cancel未成交intent；
3. append trigger evidence；
4. 如有可信行情，按shared conservative execution於下一eligible bar平倉；
5. 如冇可信行情，保留position及`flatten_pending`，絕不製造fill；
6. 進入`tripped`；
7. Owner睇過逐單／trigger evidence，明確建立新safety epoch先可resume。

歷史drawdown、streak及trip永不刪；新epoch唔改寫舊證據。

### 9.4 Pause及permanent stop

Manual pause：

- 即時禁新decision；
- cancel pending intent；
- 有可信行情先平倉；
- flat後先成為`paused`；
- 無行情保持`pausing＋flatten_pending`。

Permanent stop：

- 同樣只用可信行情平倉；
- flat後成為`permanently_stopped`；
- irreversible；
- trader、ledger及review永遠可讀／可再export。

---

## 10. Disconnect及gap recovery

### 10.1 所有disconnect即時規則

- freeze新decision；
- freeze新simulated fill；
- append data-hold event；
- position及最後可信mark保留；
- UI標示stale及blind duration；
- provider層嘗試reconnect及補缺口。

### 10.2 Blind interval `<= 5 minutes`

只喺以下全部成立先自動恢復：

- exact provider／contract／timeframe identity；
- 缺口所有closed bars完整補回；
- bars順序及內容通過canonical validation；
- journal cursor連續；
- trader本身未被其他safety rule trip。

補回bars按時間順序經同一strategy／execution core處理。由補回真bars產生嘅
decision／fill要標記`recovered_processing`，唔准冒充當時連線正常。

### 10.3 Blind interval `> 5 minutes`或證據不完整

- 進入`tripped`；
- 唔自動resume；
- open position原樣保留；
- 唔使用最後價／零價／估算價虛構離場；
- Owner可在完整診斷後選：
  - 補齊並手動resume；
  - 於下一個可信eligible price要求平倉；
  - permanent stop。

本規格明確取代舊「失明超過5分鐘但冇可信價格仍自動平倉」文案。

---

## 11. Persistence及single-writer authority

### 11.1 唯一正式可寫路徑

```text
data/paper/paper-traders.sqlite3
data/paper/paper-traders.sqlite3-wal     # runtime transient
data/paper/paper-traders.sqlite3-shm     # runtime transient
data/paper/review-artifacts/
```

除此之外，W真E2E不獲准寫任何default product data。

### 11.2 永久read-only protected data

- `data/backtests/promotion-decisions.sqlite3`；
- market history；
- strategies；
- runs及results；
- baseline artifacts；
- Owner `_to_delete/`；
- IB live／paper account state。

### 11.3 Store version

Runtime要使用新store schema version。現有provision-only／zero-ledger records語義
保持immutable；禁止原地改寫成「當時已運行」。

Default `data/paper/`目前absent，因此：

- 新default store直接用current runtime schema建立；
- 無真legacy P6 data要猜測migration；
- unknown／partial／older incompatible store fail closed；
- test-only older stores可用明確migration test或明確拒絕，唔silent backfill。

### 11.4 Append-only authority

至少以下係append-only／immutable authority：

- market input rows；
- strategy decisions；
- intents；
- simulated orders；
- fills；
- completed trades；
- equity points；
- position transitions；
- lifecycle commands及events；
- safety events／epochs；
- processing checkpoints；
- review request／status／artifact identity。

Current-state tables／views只係materialized projection。讀取時要用schema guards、
high-water marks及refs核對，唔可以frontend補零。

### 11.5 Atomic processing

每個trader處理一個closed input時，同一SQLite transaction保存：

1. input consumption identity；
2. decision／intent；
3. execution events／fill／trade；
4. position；
5. cash／equity／PnL；
6. safety state；
7. processing cursor及lifecycle version。

Crash只可令整個transaction存在或不存在，唔可出現「fill有咗但position未更新」。

### 11.6 Runtime lease

- 一個default store只可有一個active runtime writer；
- second process fail closed，唔做雙重處理；
- lease有process／instance identity及heartbeat；
- crash後唔靠過期heartbeat自動交易；只轉`recovery_required`等Owner。

---

## 12. API及strict contract families

保留現有：

- eligible strategies；
- contracts；
- baselines；
- trader create/list/detail；
- ledger origin；
- review create/status/download/opener。

新增runtime families：

- capabilities；
- runtime preflight；
- start；
- pause；
- resume；
- permanent stop；
- runtime snapshot；
- cursor timeline；
- chart series。

Exact paths及model names由implementation plan凍結，但必須遵守：

- versioned schema；
- exact top-level keys；
- closed enum；
- canonical UTC；
- safe integer；
- no NaN／Infinity／negative zero；
- request identity；
- cross-trader identity guard；
- unknown fail closed；
- zero automatic state-changing retry。

Runtime response最少攜帶：

- trader identity；
- lifecycle＋version；
- data mode；
- timeframe identities；
- provider/session identity；
- last trusted／received timestamps；
- stale／blind state；
- account及PnL；
- pending intent；
- position；
- safety values及limits；
- timeline／chart cursors。

Frontend唔自行計算balance、position、PnL、safety或mode。

---

## 13. Review artifact

### 13.1 Schema strategy

- `paper-review.v1`保留provision-only／zero-runtime historical profile；
- 真runtime snapshot用`paper-review.v2`；
- v1 bytes、SHA、member order及opener永不改寫；
- frontend按schema分流兩個strict parsers；
- unknown schema fail closed。

### 13.2 v2內容

`paper-review.v2`最少凍結：

- snapshot／request／trader identity；
- strategy／contract／baseline identity；
- runtime／display timeframes及immutable strategy timeframe profile；
- market-data mode及provider sessions；
- cutoff及high-water marks；
- account／cash／equity／PnL；
- open positions；
- decisions；
- simulated orders／fills／trades；
- equity curve；
- runtime／data／safety events；
- expected-vs-actual divergence；
- lifecycle及safety state；
- locked full baseline result members；
- all member paths／bytes／SHA；
- persisted Terminal opener text／bytes／SHA。

### 13.3 Snapshot rules

- 一個明確export intent建立一個immutable snapshot；
- 同request安全重試返同一snapshot；
- 新export先有新snapshot；
- snapshot只食cutoff前已commit證據；
- 唔重播、補算或用最新strategy／baseline；
- open position照實保存，唔為export平倉；
- missing ref／member／hash整包fail closed；
- export永遠唔改trader lifecycle。

---

## 14. Frontend truth

### 14.1 總覽

每個trader card顯示：

- lifecycle；
- strategy／contract／baseline；
- input／execution／display timeframe及locked strategy timeframe profile；
- LIVE或TEST-DELAYED；
- equity及realized／unrealized PnL；
- open position；
- safety state；
- last trusted market-data age。

### 14.2 Trader detail order

1. locked identity及runtime readiness；
2. market-data mode／health；
3. chart；
4. pending intent／position／account；
5. decisions／fills／trades timeline；
6. baseline divergence及system interpretation；
7. `paper-review.v2` handoff；
8. safety values；
9. pause／resume／permanent stop controls。

### 14.3 UI hard rules

- unknown／blocked／stale永遠唔畫綠；
- TEST-DELAYED喺card、detail、chart、review全部持續可見；
- create同start係兩個CTA；
- buttons講實際動作，唔用比喻；
- state-changing command in-flight鎖定所有會改identity嘅控制；
- generation／request／source／filename／mount identity guards全部保留；
- old response唔覆蓋new trader／tab；
- no Telegram／PWA／push／remote controls；
- test-only strategies唔入normal UI。

---

## 15. Deterministic replay acceptance

### 15.1 價格來源

- 使用已保存真IB NQ market data；
- 功能測試範圍預設30日；
- replay範圍預設30日；
- market input及execution保留MVP 1m profile；
- chart display預設30m以減少render成本；
- strategy decision仍按locked strategy.v1 profile；strategy-0003係D／1H／5m；
- 唔為見到成交而改真strategy或製造假price。

### 15.2 Test-only strategies

只在OS-temp harness：

1. long／profit path；
2. short／loss path；
3. no-signal／zero-trade path；
4. safety-trigger path。

佢哋不得寫入：

- normal strategy catalog；
- PromotionDecision DB；
- default P6 DB；
- Owner normal UI；
- product artifacts。

### 15.3 必驗

- multi-trader fan-out；
- independent account／cursor／position／PnL／safety；
- exact-once duplicate handling；
- out-of-order拒絕；
- short complete gap auto recovery；
- long／incomplete gap trip；
- process restart recovery-required；
- pause／resume／permanent stop；
- 8R及8-loss trip；
- open-position no-fabricated-exit；
- timeframe propagation；
- review v2 cutoff及ZIP integrity。

---

## 16. True IBKR Gateway及browser acceptance

### 16.1 Transport proof

真Gateway gate要證明：

- exact host／port／client ID；
- API handshake；
- provider-reported mode；
- exact contract identity；
- market-data callback；
- journal persistence；
- browser chart／health更新；
- reconnect cleanup；
- `placeOrder`／`cancelOrder`及所有order transport call exact zero。

如果只收到delayed data：

- 只可用TEST-DELAYED；
- UI及artifact不可聲稱LIVE；
- 功能閉環可通；
- real-time entitlement保持未證。

### 16.2 Default operational journey

Approved target：

```text
strategy
strategy-0003

contract
NQ-202609-CME

baseline
nq-20260728-standard-365adf
```

Journey：

```text
eligible selection
→ exact create
→ runtime preflight
→ exact start
→ true market-data update
→ browser reload同一trader
→ pause
→ flat verification
→ runtime paper-review.v2
→ download
→ persisted Terminal opener
```

真default target唔需要自然產生trade先算transport E2E；simulated execution及
safety由deterministic replay補足。兩種證據要並列，唔互相冒充。

### 16.3 Final state

驗收完成後：

```text
default trader = paused
pending intent = 0
open position = 0
W-owned backend/browser/client connection = stopped
Owner-opened Gateway = untouched
```

---

## 17. Evidence levels及test discipline

### 17.1 Four-level review

```text
COMPONENT PASS
CONTRACT PASS
INTEGRATION PASS
TRUE E2E PASS
```

每級只可聲稱已直接證明嘅範圍。

### 17.2 Mandatory verification

- focused backend tests；
- focused frontend tests；
- typecheck；
- lint；
- build；
- ruff；
- mypy；
- backend full一次；
- frontend full一次；
- registered cross-layer contract；
- real browser；
- deterministic replay；
- true Gateway transport；
- default DB journey；
- `git diff --check`；
- protected facts before／after；
- targeted mutation RED→byte-exact restore→GREEN。

Mutation最少覆蓋：

- duplicate input造成double fill；
- lookahead／forming bar入decision；
- timeframe被寫死或丟失；
- stale response覆蓋；
- safety threshold漏trip；
- missing trusted price仍造exit；
- request retry產生second side effect；
- order API path被調用；
- review cutoff／member／SHA驗證被移除。

### 17.3 Resource discipline

- W唔開其他coding agents；
- backend full、frontend full、browser及replay heavy lane唔並行；
- 開發期focused tests；
- final full suites各只喺需要時跑一次；
- server、browser profile及temp harness用完即清；
- 唔用長時間live wait去等自然signal；
- 非P6而不阻MVP嘅bug只記backlog，唔擴scope。

---

## 18. Operational write authorization

Owner批准本設計時已明確批准W在後續正式工作令下：

```text
CREATE/WRITE
data/paper/paper-traders.sqlite3
data/paper/paper-traders.sqlite3-wal
data/paper/paper-traders.sqlite3-shm
data/paper/review-artifacts/**
```

批准內容包括：

- exact一個default trader；
- runtime market journal；
- simulated decisions／orders／fills／trades；
- account／position／PnL；
- lifecycle／safety events；
- review v2 artifacts。

呢個批准**唔包括**：

- 其他`data/**`；
- PromotionDecision append；
- strategy／run／result修改；
- IB order；
- Supabase／cloud；
- Telegram／PWA push；
- `_to_delete/`。

W開真寫入前必須記錄protected before facts；完成後逐項列expected delta及
unexpected delta exact zero。

---

## 19. Agent W execution model

W一口氣完成整個P6，唔分stage等C review：

- 可有atomic internal commits；
- 唔提交interim REPORT要求C收貨；
- 自行修正一般bug／test failure；
- 最後一次性交`[W-FINAL] REPORT`。

只可因以下情況HOLD：

1. Owner要處理Gateway登入／API confirmation；
2. accepted baseline／protected facts不一致；
3. 規格矛盾影響truth、安全或寫入；
4. 發現任何IB order possibility；
5. 要求超出§18 write scope。

最終REPORT必須：

- 確認required ancestors；
- 列atomic commits；
- 列exact file scope；
- 列四級verdict；
- 列全部commands及counts；
- 列mutation RED／restore／GREEN；
- 列真Gateway mode及market-data evidence；
- 列order API exact zero；
- 列default DB exact delta；
- 列protected before／after；
- 列ports／processes／temp cleanup；
- 披露所有不利事實；
- 進入EXPLICIT HOLD等C獨立review。

---

## 20. C final review及Owner deliverables

C收到`[W-FINAL]`後：

1. 先確認commit ancestry及scope；
2. 獨立讀code／tests／contracts；
3. 跑風險導向focused verification；
4. 做臨時mutation probes並byte-exact還原；
5. 核Gateway、browser、DB及protected facts；
6. 按四級裁決；
7. 每份REVIEW附correction／next work order／explicit hold；
8. 更新overall project percentage及journey status；
9. 通過後重建current P6 HTML及
   `docs/ui/designs/p1-p6-closed-loop-flow.html`；
10. 向Owner交逐頁manual test guide。

---

## 21. Explicit design decisions log

Owner於2026-07-31逐項批准：

1. W一個agent完成整個P6，最後先交C review；
2. IBKR Gateway (Simulated Trading)＋Read-Only API＋client ID 7，明確取代較早
   TWS字眼；
3. IB只供行情，app自己paper trade；
4. genuine Gateway transport＋real-data deterministic replay hybrid；
5. production multi-trader，default E2E一個approved target；
6. 四個test-only strategies、零normal catalog污染；
7. browser關閉backend繼續；
8. restart後Owner手動resume；
9. short complete gap可auto recover；long／incomplete gap trip；
10. no fabricated exit；
11. Telegram移除；
12. PWA Push／Tailscale／iPhone remote押後；
13. no Supabase／cloud for MVP；
14. delayed data只准TEST-DELAYED；
15. 1m／30m只係MVP capability，architecture timeframe-agnostic；
16. exact `data/paper/**` write authorization；
17. final default trader paused＋flat。

---

## 22. Open questions

```text
NONE
```

如implementation發現新ambiguity，只可按§19 stop conditions提出，唔可自行猜。
