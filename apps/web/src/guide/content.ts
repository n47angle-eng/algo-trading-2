/**
 * 系統說明書 — the product manual, kept in the app so it ships with the build.
 *
 * MAINTENANCE RULE (binding on every future change, human or agent):
 * whenever a page, tab, control, file contract or journey step changes, the
 * matching chapter here MUST be updated in the same change. A feature without
 * a manual entry is an unfinished feature. Add a new chapter when a new page
 * appears; never delete a chapter without replacing what it explained.
 *
 * Wording rules inherited from the project: 廣東話 · 繁體中文, plain page names
 * (總覽／策略工作台／數據／回測／結果／模擬盤／日內模擬／設定), no internal
 * P1–P6 codes and no technical jargon the UI itself is forbidden to show.
 */

export interface GuideSection {
  id: string;
  heading: string;
  /** Paragraphs. */
  body: string[];
  /** Optional ordered steps — rendered as a numbered list. */
  steps?: string[];
  /** Optional term → meaning pairs. */
  terms?: { term: string; meaning: string }[];
  /** Optional cautions — rendered as a highlighted list. */
  notes?: string[];
}

export interface GuideChapter {
  id: string;
  /** Chapter number shown in the table of contents. */
  number: string;
  title: string;
  /** One line telling the reader whether this chapter is what they want. */
  summary: string;
  sections: GuideSection[];
}

export const GUIDE_CHAPTERS: readonly GuideChapter[] = [
  {
    id: "start",
    number: "01",
    title: "由零開始",
    summary: "第一次打開系統應該做咩、成個流程行一次係點。",
    sections: [
      {
        id: "start-what",
        heading: "呢個系統做咩",
        body: [
          "呢個係一個期貨研究平台。你喺度寫低自己嘅交易諗法，將佢變成一份可以重複執行嘅策略，攞歷史數據行回測，睇返成績，再決定要唔要放上模擬盤跟住睇。",
          "全程唔會落真錢單。模擬盤只係攞 IB 嘅行情做輸入，成交、倉位、盈虧全部由 app 自己模擬出嚟。",
        ],
      },
      {
        id: "start-loop",
        heading: "一個完整循環",
        body: ["由諗法到決定，正常會行呢六步："],
        steps: [
          "總覽 — 開工先睇連接同有冇嘢行緊。",
          "策略工作台 — 寫低草圖，再逐項量化確認，變成一個版本。",
          "數據 — 確認你要用嘅合約同時間範圍有齊數據。",
          "回測 — 揀策略版本同合約，開始跑。",
          "結果 — 睇成績表、逐筆成交同因果，然後做決定。",
          "模擬盤 — 通過嘅版本先放上去，用實時行情繼續睇表現。",
        ],
      },
      {
        id: "start-nav",
        heading: "介面點行",
        body: [
          "電腦版：左邊係主選單，所有頁面都喺度。頁面入面嘅分頁（例如「連接狀態／行緊 run」）全部搬咗上最頂嘅橫欄。",
          "手機版：最頂係橫欄，帶住品牌同該頁嘅分頁；最底係主選單。放唔落底欄嘅頁面、主題切換同呢本說明書，全部收喺「更多」。",
        ],
        notes: [
          "見到標題或者卡片右邊有個圓形「i」掣，撳一撳就會彈出嗰一格嘅用法說明。唔想睇就唔會阻住你。",
        ],
      },
    ],
  },
  {
    id: "overview",
    number: "02",
    title: "總覽",
    summary: "開工第一頁：連接、行緊嘅回測、模擬盤、盤前計劃。",
    sections: [
      {
        id: "overview-use",
        heading: "點用",
        body: [
          "呢頁答四條問題：接唔接到、有冇嘢行緊、模擬盤點、今日有冇計劃。四個分頁喺頂欄切換。",
        ],
        steps: [
          "連接狀態 — 睇 IB 同計算引擎。撳「重新探測」可以即刻再試一次。",
          "行緊 run — 有回測行緊就會見到進度條同已完成／總數。",
          "模擬盤 — 一個入口，直接跳去模擬盤頁。",
          "盤前計劃 — 今日嘅方向同波動判斷（未接通時會誠實顯示空白）。",
        ],
      },
      {
        id: "overview-ib",
        heading: "IB 狀態要點讀",
        body: [
          "IB 欄只會講「port 通唔通」。TCP 通唔代表 API session 已經行得，所以系統唔會寫「已連接」。",
        ],
        notes: [
          "見到「未設定」= 未讀到 .env 嘅連接設定，唔係故障。",
          "IB 喺呢個系統入面淨係做行情來源，唔會替你落單。",
        ],
      },
    ],
  },
  {
    id: "strategies",
    number: "03",
    title: "策略工作台",
    summary: "由手寫草圖，到可以攞去回測嘅版本。",
    sections: [
      {
        id: "strategies-flow",
        heading: "四個分頁點行",
        body: ["由左至右行一次，每一步都要做完先去下一步。"],
        steps: [
          "草圖 — 用日常講法寫低你嘅諗法，揀交易市場同時間框架，可以上載圖表截圖做參考。",
          "量化確認 — 逐項將文字變成明確條件。呢步就係「你講嘅嘢，機器點理解」，唔確認就唔可以回測。",
          "版本庫 — 確認完會生成一個版本。版本一經確認就唔會再改，要改就開新版本。",
          "市場洞察 — 記低同呢個策略有關嘅市場觀察，方便日後翻查。",
        ],
      },
      {
        id: "strategies-why",
        heading: "點解要分草圖同版本",
        body: [
          "草圖係可以隨時改嘅。版本係唔可以改嘅——因為回測成績一定要對得返一個固定嘅策略定義，否則你就唔知究竟成績係邊個版本行出嚟。",
        ],
        notes: [
          "草圖未填齊必填項（標題、交易市場、資產類別）就唔會俾你確認，呢個係故意嘅。",
        ],
      },
    ],
  },
  {
    id: "data",
    number: "04",
    title: "數據",
    summary: "睇你手上有咩數據、邊度有窿、點樣補。",
    sections: [
      {
        id: "data-use",
        heading: "點用",
        body: ["回測之前嚟呢頁確認一次，可以慳返好多「行完先發現冇數據」。"],
        steps: [
          "覆蓋 — 逐個合約睇有數據嘅日期範圍。",
          "體檢 — 睇數據質量報告同已知有問題嘅日子。",
          "補數據 — 系統會出一條指令俾你自己喺終端機跑，唔會靜靜雞喺背後下載。",
        ],
        notes: [
          "「補數據」只會出指令，唔會自動執行。呢個係刻意嘅設計，因為下載會寫入真數據。",
        ],
      },
    ],
  },
  {
    id: "backtest",
    number: "05",
    title: "回測",
    summary: "揀策略同合約、跑、睇進度。",
    sections: [
      {
        id: "backtest-use",
        heading: "點用",
        body: ["三個分頁：設定、進度、最近。"],
        steps: [
          "設定 — 揀已確認嘅策略版本（未確認嘅唔會出現），再揀一個或多個合約。",
          "揀日期範圍。畫面用你本地時間顯示，下面會標明送去後台嘅世界時間，兩個唔會撈亂。",
          "撳開始。系統會先做檢查，有問題會列出原因，唔會靜靜哋跑一個冇意義嘅回測。",
          "進度 — 睇住行緊嘅百分比，可以中途取消。",
          "最近 — 之前跑過嘅一覽，撳入去就係結果頁。",
        ],
      },
      {
        id: "backtest-time",
        heading: "時間點睇",
        body: [
          "系統將時間分三類，唔會混：時刻（可以轉時區）、交易日（永遠唔轉時區）、你揀嘅範圍（用本地 picker，送出前一律轉做世界時間）。",
        ],
        notes: ["交易時段係由策略文件決定嘅，唔可以喺呢頁改。"],
      },
    ],
  },
  {
    id: "results",
    number: "06",
    title: "結果",
    summary: "成績表、逐筆成交、因果，同最後嘅決定。",
    sections: [
      {
        id: "results-read",
        heading: "點讀成績",
        body: [
          "上面係幾個大數（總盈虧、勝率、最大回撤等）。下面係逐筆成交，可以撳入去睇當時嘅圖同判斷理由。",
        ],
      },
      {
        id: "results-zero",
        heading: "零成交唔一定係壞事",
        body: [
          "有啲歷史回測係零成交嘅。呢個唔係故障——係策略條件喺嗰段時間真係冇滿足過。因果面板會話你聽卡喺邊一關。",
        ],
        notes: ["唔好因為見到零成交就去改鬆條件；先睇因果，再決定改咩。"],
      },
      {
        id: "results-decide",
        heading: "做決定",
        body: [
          "睇完之後喺呢頁落一個決定：留低、淘汰、或者放上模擬盤。決定一經記錄就唔會改寫，方便日後翻查你當時點諗。",
        ],
      },
    ],
  },
  {
    id: "paper",
    number: "07",
    title: "模擬盤",
    summary: "用實時行情跟住睇，但唔會落真錢單。",
    sections: [
      {
        id: "paper-what",
        heading: "呢度發生緊咩事",
        body: [
          "模擬盤攞 IB 嘅行情做輸入，成交、虛擬帳戶、持倉、盈虧、斷路器全部係 app 自己計。唔會向任何 IB 帳戶（真倉或者 paper 倉）發單。",
        ],
        notes: [
          "見到「IB 行情驅動嘅 app 自家模擬成交」就係呢個意思。系統唔會用「IB 模擬成交」呢種寫法，因為會誤導。",
        ],
      },
      {
        id: "paper-use",
        heading: "點用",
        steps: [
          "喺頂欄揀交易員分頁，或者撳「＋ 新增交易員」開一個新嘅。",
          "睇佢嘅倉位、今日盈虧同事件紀錄。",
          "有需要就導出檢視檔，留低紀錄。",
        ],
        body: [],
      },
    ],
  },
  {
    id: "daytrade",
    number: "08",
    title: "日內模擬",
    summary: "模擬交易員名單、個人檔案、成績表。",
    sections: [
      {
        id: "daytrade-use",
        heading: "點用",
        body: [
          "呢頁係一班模擬交易員嘅名單。撳入去睇個人檔案、持倉同成績表，行情用每分鐘收市價推動。",
        ],
      },
    ],
  },
  {
    id: "settings",
    number: "09",
    title: "設定同裝落手機",
    summary: "通知偏好，同點樣將系統裝上手機主畫面。",
    sections: [
      {
        id: "settings-pwa",
        heading: "裝落手機",
        body: [
          "呢個系統可以當 app 咁裝落手機主畫面，裝完會冇瀏覽器嘅網址列，用起上嚟同原生 app 差唔多。",
        ],
        steps: [
          "用手機瀏覽器打開系統。",
          "去「更多」→「設定」→「安裝 App」。",
          "跟提示加去主畫面。iPhone 要喺分享選單揀「加至主畫面」。",
        ],
      },
      {
        id: "settings-theme",
        heading: "轉主題",
        body: [
          "主題切換收咗喺「更多」入面。三個選擇：深色、淺色、自然。揀完會記住，下次打開一樣。",
        ],
      },
    ],
  },
  {
    id: "glossary",
    number: "10",
    title: "名詞對照",
    summary: "畫面上見到嘅字，實際係咩意思。",
    sections: [
      {
        id: "glossary-terms",
        heading: "常見字眼",
        body: [],
        terms: [
          { term: "草圖", meaning: "你手寫、隨時可以改嘅策略諗法。" },
          { term: "版本", meaning: "確認完、唔會再改嘅策略定義。回測同模擬盤只認版本。" },
          { term: "回測", meaning: "攞歷史數據行一次策略，睇當時會點。" },
          { term: "成績表", meaning: "一份策略表現嘅多角度評分。" },
          { term: "因果", meaning: "解釋點解某一刻冇入場／出場，卡喺邊一關。" },
          { term: "模擬盤", meaning: "用實時行情跟住睇，成交由 app 自己模擬。" },
          { term: "覆蓋", meaning: "你手上實際有數據嘅日期範圍。" },
        ],
      },
    ],
  },
  {
    id: "faq",
    number: "11",
    title: "常見情況",
    summary: "見到呢啲畫面唔使驚，係正常嘅。",
    sections: [
      {
        id: "faq-list",
        heading: "唔係故障嘅情況",
        body: [],
        terms: [
          {
            term: "回測零成交",
            meaning: "條件真係冇滿足過。睇因果面板搵原因，唔好即刻改鬆條件。",
          },
          {
            term: "IB 寫「未設定」",
            meaning: "未讀到連接設定。填好 .env 再撳重新探測。",
          },
          {
            term: "某格係空白",
            meaning: "嗰項功能未接通。系統寧願留白，都唔會填個假數俾你。",
          },
          {
            term: "策略揀唔到",
            meaning: "回測只列已確認嘅版本。去策略工作台確認咗先。",
          },
        ],
      },
    ],
  },
];

/** Flat index used by the manual's search box. */
export interface GuideHit {
  chapterId: string;
  chapterTitle: string;
  chapterNumber: string;
  sectionId: string;
  heading: string;
  snippet: string;
}

export function searchGuide(query: string): GuideHit[] {
  const q = query.trim().toLowerCase();
  if (q.length === 0) {
    return [];
  }
  const hits: GuideHit[] = [];
  for (const chapter of GUIDE_CHAPTERS) {
    for (const section of chapter.sections) {
      const parts = [
        section.heading,
        ...section.body,
        ...(section.steps ?? []),
        ...(section.notes ?? []),
        ...(section.terms ?? []).flatMap((t) => [t.term, t.meaning]),
      ];
      const found = parts.find((p) => p.toLowerCase().includes(q));
      if (found !== undefined) {
        hits.push({
          chapterId: chapter.id,
          chapterTitle: chapter.title,
          chapterNumber: chapter.number,
          sectionId: section.id,
          heading: section.heading,
          snippet: found,
        });
      }
    }
  }
  return hits;
}
