import { describe, expect, it } from "vitest";

import { parsePaperTradeLesson } from "./tradeLessonTypes";

const SAMPLE = {
  schema: "paper_trade_lesson.v1",
  trader_id: "trader-" + "a".repeat(32),
  as_of: "2026-07-31T12:00:00Z",
  selection: {
    strategy_id: "strategy-1",
    contract_id: "NQ-202609-CME",
  },
  lifecycle: { state: "running", version: 2, reason: "ok" },
  account: {
    cash: 100000,
    equity: 100100,
    realized_pnl: 0,
    unrealized_pnl: 100,
    realized_r: 0,
    unrealized_r: 0.5,
  },
  open_position: {
    side: "long",
    quantity: 1,
    average_entry_price: 21000,
    stop_price: 20950,
    target_price: 21100,
    last_price: 21050,
    unrealized_pnl: 100,
    unrealized_r: 0.5,
    risk_amount: 200,
    label: "好倉 1 張",
    instrument_label: "NQ-202609-CME",
    teach: "teach",
  },
  levels: {
    entry: 21000,
    stop: 20950,
    target: 21100,
    last: 21050,
  },
  bars: [
    {
      cursor: 1,
      event_at: "2026-07-31T12:00:00Z",
      open: 21000,
      high: 21010,
      low: 20990,
      close: 21005,
      volume: 10,
    },
  ],
  markers: [
    {
      kind: "ENTRY",
      ts: "2026-07-31T12:00:00Z",
      price: 21000,
      side: "long",
      quantity: 1,
      label: "買入開倉",
    },
  ],
  fills: [],
  trades: [],
  conditions: [
    {
      kind: "entry",
      at: "2026-07-31T12:00:00Z",
      title: "買入開倉",
      status: "filled",
      summary: "好倉 1 張 @ 21000",
      detail: "",
      teach: "teach",
      price: 21000,
    },
  ],
  counts: {
    bar_count: 1,
    fill_count: 0,
    trade_count: 0,
    condition_count: 1,
    decision_count: 1,
    pending_intent_count: 0,
  },
  teaching: {
    headline: "交易教學",
    intro: "intro",
    tips: [{ id: "a", title: "t", body: "b" }],
  },
  replay: { supported: true, bar_count: 1, hint: "hint" },
};

describe("parsePaperTradeLesson", () => {
  it("accepts the canonical lesson payload", () => {
    const parsed = parsePaperTradeLesson(SAMPLE);
    expect(parsed).not.toBeNull();
    expect(parsed?.schema).toBe("paper_trade_lesson.v1");
    expect(parsed?.open_position?.label).toBe("好倉 1 張");
    expect(parsed?.bars).toHaveLength(1);
    expect(parsed?.teaching.tips[0].id).toBe("a");
  });

  it("rejects wrong schema and missing arrays", () => {
    expect(parsePaperTradeLesson({ schema: "other" })).toBeNull();
    expect(parsePaperTradeLesson({ ...SAMPLE, schema: "x" })).toBeNull();
    expect(parsePaperTradeLesson({ ...SAMPLE, bars: null })).toBeNull();
    expect(parsePaperTradeLesson(null)).toBeNull();
  });
});
