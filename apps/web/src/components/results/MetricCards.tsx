import type { MetricCardModel } from "./metricModels";

interface MetricCardsProps {
  cards: MetricCardModel[];
}

/** docs/03 P5: 指標摘要卡 — values already formatted by caller from API metrics. */
export function MetricCards({ cards }: MetricCardsProps) {
  return (
    <div className="metrics-grid">
      {cards.map((card) => {
        const toneClass =
          card.tone === "pos"
            ? "metric-card__value metric-card__value--pos"
            : card.tone === "neg"
              ? "metric-card__value metric-card__value--neg"
              : card.tone === "muted"
                ? "metric-card__value metric-card__value--muted"
                : "metric-card__value";
        return (
          <article key={card.label} className="metric-card">
            <div className="metric-card__label">{card.label}</div>
            <div className={toneClass}>{card.value}</div>
          </article>
        );
      })}
    </div>
  );
}
