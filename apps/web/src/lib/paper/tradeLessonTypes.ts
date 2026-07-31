/** Wire shapes for GET /api/v1/paper/traders/{id}/trade-lesson */

export interface PaperLessonBar {
  cursor: number;
  event_at: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  mode?: string;
}

export interface PaperLessonMarker {
  kind: "ENTRY" | "EXIT" | "OPEN" | "TARGET" | "STOP" | string;
  ts: string | null;
  price: number;
  side?: string;
  quantity?: number;
  label: string;
}

export interface PaperLessonLevels {
  entry: number | null;
  stop: number | null;
  target: number | null;
  last: number | null;
}

export interface PaperLessonFill {
  fill_id: string;
  role: "entry" | "exit" | string;
  side: string;
  quantity: number;
  price: number;
  commission: number;
  slippage: number;
  event_at: string;
  created_at: string;
  label: string;
  teach: string;
}

export interface PaperLessonTrade {
  trade_id: string;
  side: string;
  quantity: number;
  entry_price: number;
  exit_price: number;
  entry_at: string;
  exit_at: string;
  gross_pnl: number;
  net_pnl: number;
  net_r: number;
  profitable: boolean;
  label: string;
  teach: string;
}

export interface PaperLessonCondition {
  kind: string;
  at: string;
  title: string;
  status: string;
  summary: string;
  detail: string;
  teach: string;
  price?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
}

export interface PaperLessonTip {
  id: string;
  title: string;
  body: string;
}

export interface PaperOpenPositionLesson {
  side: string;
  quantity: number;
  average_entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  last_price: number | null;
  unrealized_pnl: number;
  unrealized_r: number;
  risk_amount: number | null;
  label: string;
  instrument_label: string;
  teach: string;
}

export interface PaperTradeLesson {
  schema: "paper_trade_lesson.v1";
  trader_id: string;
  as_of: string;
  selection: {
    strategy_id: string;
    contract_id: string;
    baseline_run_id?: string | null;
  };
  lifecycle: {
    state: string;
    version: number;
    reason: string;
  };
  account: {
    cash: number;
    equity: number;
    realized_pnl: number;
    unrealized_pnl: number;
    realized_r: number;
    unrealized_r: number;
  };
  open_position: PaperOpenPositionLesson | null;
  levels: PaperLessonLevels;
  bars: PaperLessonBar[];
  markers: PaperLessonMarker[];
  fills: PaperLessonFill[];
  trades: PaperLessonTrade[];
  conditions: PaperLessonCondition[];
  counts: {
    bar_count: number;
    fill_count: number;
    trade_count: number;
    condition_count: number;
    decision_count: number;
    pending_intent_count: number;
  };
  teaching: {
    headline: string;
    intro: string;
    tips: PaperLessonTip[];
  };
  replay: {
    supported: boolean;
    bar_count: number;
    hint: string;
  };
}

export function parsePaperTradeLesson(body: unknown): PaperTradeLesson | null {
  if (!body || typeof body !== "object") {
    return null;
  }
  const record = body as Record<string, unknown>;
  if (record.schema !== "paper_trade_lesson.v1") {
    return null;
  }
  if (typeof record.trader_id !== "string") {
    return null;
  }
  if (!Array.isArray(record.bars) || !Array.isArray(record.markers)) {
    return null;
  }
  if (!Array.isArray(record.conditions) || !Array.isArray(record.fills)) {
    return null;
  }
  return body as PaperTradeLesson;
}
