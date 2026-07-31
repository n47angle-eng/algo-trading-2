/**
 * Backend failures, said in the owner's language.
 *
 * The banned-vocabulary rule (docs/ui/designs/p4-backtest.html) applies to
 * everything the owner reads, and a raw backend string is the easiest way to
 * break it — `missing required IB environment variables: IB_HOST, IB_PORT` was
 * on screen before this file existed.
 *
 * So a failure becomes two parts: a `title` written for the owner, and the
 * verbatim `detail`, kept intact but folded away behind 詳情. Nothing is
 * discarded — an engineer debugging later still gets the original bytes.
 */

export interface HumanError {
  /** Owner-facing, free of technical vocabulary. */
  title: string;
  /** What to try next. Empty when there is nothing honest to suggest. */
  hint: string;
  /** Verbatim upstream text. Shown only when the owner opens 詳情. */
  detail: string;
}

/**
 * Recognised backend phrases, most specific first.
 * Matching is case-insensitive and substring-based on the raw text.
 */
const PHRASE_RULES: ReadonlyArray<{
  match: RegExp;
  title: string;
  hint: string;
}> = [
  {
    match: /missing required ib environment variables/i,
    title: "IB 連接未設定",
    hint: "喺 .env 填好 IB 連接資料再重開服務。",
  },
  {
    match: /connection refused|econnrefused/i,
    title: "連唔到資料服務",
    hint: "確認後台服務有冇開住。",
  },
  {
    match: /timed? ?out|etimedout/i,
    title: "等太耐冇回應",
    hint: "可以再試一次。",
  },
  {
    match: /is not json|unexpected token/i,
    title: "收到嘅資料格式唔啱",
    hint: "呢個唔正常，請保留詳情。",
  },
  {
    match: /unsafe .*identity|invalid_trader_id/i,
    title: "識別碼唔合法，已經攔住冇送出",
    hint: "",
  },
];

/** Status families. 4xx is the owner's input; 5xx is the service's problem. */
function titleForStatus(status: number): { title: string; hint: string } {
  if (status === 0) {
    return {
      title: "連唔到資料服務",
      hint: "檢查網絡，或者確認後台服務有冇開住。",
    };
  }
  if (status === 401 || status === 403) {
    return { title: "冇權限做呢個動作", hint: "" };
  }
  if (status === 404) {
    return { title: "搵唔到呢項資料", hint: "可能已經改咗名或者移除咗。" };
  }
  if (status === 409) {
    return {
      title: "同現有紀錄有衝突，冇改到",
      hint: "打開詳情睇下邊度撞。",
    };
  }
  if (status === 422) {
    return { title: "填入嘅內容唔合格式", hint: "睇下詳情列出嘅問題再改。" };
  }
  if (status === 429) {
    return { title: "太密，被暫時擋住", hint: "等陣再試。" };
  }
  if (status >= 500) {
    return { title: "資料服務出錯", hint: "可以再試一次；仲係咁就保留詳情。" };
  }
  if (status >= 400) {
    return { title: "呢個要求做唔到", hint: "睇下詳情。" };
  }
  return { title: "出咗問題", hint: "" };
}

/**
 * `status` 0 means no response ever arrived (transport failure).
 * `raw` is passed through untouched — never trimmed into the title.
 */
export function humanizeApiError(status: number, raw: string): HumanError {
  const detail = raw ?? "";
  for (const rule of PHRASE_RULES) {
    if (rule.match.test(detail)) {
      return { title: rule.title, hint: rule.hint, detail };
    }
  }
  const { title, hint } = titleForStatus(status);
  return { title, hint, detail };
}

/** Turn a thrown value into the same two-part shape. */
export function humanizeThrown(error: unknown): HumanError {
  const raw =
    error instanceof Error ? error.message : String(error ?? "unknown error");
  // An Error that carries an HTTP status in its text still deserves that path.
  const statusMatch = /^(\d{3})\s/.exec(raw);
  const status = statusMatch ? Number(statusMatch[1]) : 0;
  return humanizeApiError(status, raw);
}
