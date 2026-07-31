import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { PageHeader } from "../components/ui/PageHeader";
import {
  GUIDE_CHAPTERS,
  searchGuide,
  type GuideChapter,
} from "../guide/content";

/**
 * 系統說明書 — an e-book: contents on the left, one chapter at a time on the
 * right, and a search box that jumps straight to the section that mentions
 * what you typed.
 */
export function GuidePage() {
  /* `?c=` lets a page's help bubble open straight at its own chapter. */
  const [params] = useSearchParams();
  const requested = params.get("c");
  const [chapterId, setChapterId] = useState(() =>
    GUIDE_CHAPTERS.some((c) => c.id === requested)
      ? (requested as string)
      : GUIDE_CHAPTERS[0].id,
  );
  const [query, setQuery] = useState("");
  const [tocOpen, setTocOpen] = useState(false);

  const hits = useMemo(() => searchGuide(query), [query]);
  const chapter =
    GUIDE_CHAPTERS.find((c) => c.id === chapterId) ?? GUIDE_CHAPTERS[0];
  const index = GUIDE_CHAPTERS.indexOf(chapter);
  const prev = index > 0 ? GUIDE_CHAPTERS[index - 1] : null;
  const next =
    index < GUIDE_CHAPTERS.length - 1 ? GUIDE_CHAPTERS[index + 1] : null;

  const open = (id: string, sectionId?: string) => {
    setChapterId(id);
    setTocOpen(false);
    setQuery("");
    if (sectionId) {
      // Let the chapter paint before scrolling to the anchor.
      requestAnimationFrame(() => {
        document
          .getElementById(`guide-${sectionId}`)
          ?.scrollIntoView({ block: "start", behavior: "smooth" });
      });
    } else {
      window.scrollTo({ top: 0 });
    }
  };

  return (
    <div className="detail-stack">
      <PageHeader
        title="系統說明書"
        info={
          <>
            由目錄揀章節，或者用搜尋直接跳去講嗰件事嘅段落。每次系統加咗新功能，呢本書都會一齊更新。
          </>
        }
        actions={
          <button
            type="button"
            className="btn btn--sm guide__toc-toggle"
            aria-expanded={tocOpen}
            onClick={() => {
              setTocOpen((v) => !v);
            }}
          >
            目錄
          </button>
        }
      />

      <div className="guide">
        <aside
          className={tocOpen ? "guide__toc guide__toc--open" : "guide__toc"}
          aria-label="目錄"
        >
          <label className="guide__search">
            <Icon name="search" />
            <input
              type="search"
              value={query}
              placeholder="搵功能、名詞"
              aria-label="搜尋說明書"
              onChange={(event) => {
                setQuery(event.target.value);
              }}
            />
          </label>

          {query.trim().length > 0 ? (
            <div className="guide__hits">
              {hits.length === 0 ? (
                <p className="state-msg">搵唔到「{query}」。</p>
              ) : (
                hits.map((hit) => (
                  <button
                    key={`${hit.chapterId}-${hit.sectionId}`}
                    type="button"
                    className="guide__hit"
                    onClick={() => {
                      open(hit.chapterId, hit.sectionId);
                    }}
                  >
                    <span className="guide__hit-where">
                      {hit.chapterNumber} · {hit.chapterTitle}
                    </span>
                    <span className="guide__hit-head">{hit.heading}</span>
                    <span className="guide__hit-snippet">{hit.snippet}</span>
                  </button>
                ))
              )}
            </div>
          ) : (
            <ol className="guide__chapters">
              {GUIDE_CHAPTERS.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className={
                      c.id === chapter.id
                        ? "guide__chapter guide__chapter--on"
                        : "guide__chapter"
                    }
                    aria-current={c.id === chapter.id ? "true" : undefined}
                    onClick={() => {
                      open(c.id);
                    }}
                  >
                    <span className="guide__chapter-no">{c.number}</span>
                    <span className="guide__chapter-body">
                      <span className="guide__chapter-title">{c.title}</span>
                      <span className="guide__chapter-sum">{c.summary}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </aside>

        <article className="guide__reader" aria-label={chapter.title}>
          <ChapterBody chapter={chapter} />

          <nav className="guide__pager" aria-label="章節切換">
            {prev ? (
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => {
                  open(prev.id);
                }}
              >
                ← {prev.title}
              </button>
            ) : (
              <span />
            )}
            {next ? (
              <button
                type="button"
                className="btn btn--sm btn--primary"
                onClick={() => {
                  open(next.id);
                }}
              >
                {next.title} →
              </button>
            ) : null}
          </nav>
        </article>
      </div>
    </div>
  );
}

function ChapterBody({ chapter }: { chapter: GuideChapter }) {
  return (
    <>
      <header className="guide__chapter-head">
        <span className="guide__chapter-kicker">第 {chapter.number} 章</span>
        <h2 className="guide__h1">{chapter.title}</h2>
        <p className="guide__lead">{chapter.summary}</p>
      </header>

      {chapter.sections.map((section) => (
        <section
          key={section.id}
          id={`guide-${section.id}`}
          className="guide__section"
        >
          <h3 className="guide__h2">{section.heading}</h3>

          {section.body.map((p, i) => (
            <p key={i} className="guide__p">
              {p}
            </p>
          ))}

          {section.steps ? (
            <ol className="guide__steps">
              {section.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          ) : null}

          {section.terms ? (
            <dl className="guide__terms">
              {section.terms.map((t) => (
                <div key={t.term}>
                  <dt>{t.term}</dt>
                  <dd>{t.meaning}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {section.notes ? (
            <ul className="guide__notes">
              {section.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </>
  );
}
