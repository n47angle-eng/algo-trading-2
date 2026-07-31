import { Link } from "react-router-dom";

import { InfoButton } from "./InfoButton";

/**
 * Which manual chapter explains this page.
 *
 * Derived from the title rather than passed in, so a page never has to know the
 * manual exists — and a page whose title is dynamic (a run detail, a trader)
 * simply gets no link instead of a wrong one.
 */
const GUIDE_CHAPTER_BY_TITLE: Record<string, string> = {
  總覽: "overview",
  策略工作台: "strategies",
  數據: "data",
  回測: "backtest",
  結果: "results",
  模擬盤: "paper",
  日內模擬交易員: "daytrade",
  設定: "settings",
};

interface PageHeaderProps {
  title: string;
  /**
   * How to use this page. Lives behind the glyph at the far right of the title
   * row, not on the canvas — pass the help that used to be printed as prose.
   */
  info?: React.ReactNode;
  /** Primary + secondary entries for the page. */
  actions?: React.ReactNode;
}

/**
 * One page title, one action cluster, one help glyph pinned to the right edge.
 *
 * Every page uses this, so the eye lands in the same place on every screen and
 * no page needs a second-level heading just to introduce itself. The bubble is
 * the short answer; it ends with a link into the long one.
 */
export function PageHeader({ title, info, actions }: PageHeaderProps) {
  const chapter = GUIDE_CHAPTER_BY_TITLE[title];

  return (
    <header className="page-header">
      <h1 className="page-header__title">{title}</h1>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
      {info ? (
        <InfoButton
          label={`${title}點用`}
          align="end"
          className={actions ? "info--trail" : "info--corner"}
        >
          {info}
          {chapter ? (
            <Link className="info-pop__more" to={`/guide?c=${chapter}`}>
              喺說明書睇詳細 →
            </Link>
          ) : null}
        </InfoButton>
      ) : null}
    </header>
  );
}
