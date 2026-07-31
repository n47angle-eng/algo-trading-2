/**
 * Human-readable parameter chips + long-form explanations for daytrade cards.
 * Keep copy short and plain Cantonese; values come from the trader config.
 */

export type DaytradeParamSource = {
  symbol?: string;
  strategy_id?: string;
  quantity?: number;
  or_minutes?: number;
  no_new_entry_after?: string;
  force_flat_time?: string;
  rth_start?: string;
  rth_end?: string;
  timezone?: string;
  max_daily_loss_r?: number;
  starting_equity?: number;
  notes?: string;
};

export type ParamChip = {
  key: string;
  label: string;
  value: string;
};

export type ParamExplainRow = {
  key: string;
  title: string;
  value: string;
  body: string;
};

function strategyLabel(id: string | undefined): string {
  if (!id) return "—";
  if (id === "opening_range_breakout_v1") return "開盤區間突破";
  return id;
}

function orQualityHint(minutes: number): string {
  if (minutes <= 3) {
    return "OR 好短，入場快、訊號多，假突破機會亦高啲——偏動能／高回報潛力。";
  }
  if (minutes <= 5) {
    return "經典 OR 長度，平衡訊號數量同質素。";
  }
  if (minutes <= 12) {
    return "中長 OR，突破線穩啲，假突破會少啲——偏質素。";
  }
  return "長 OR，只跟高信念突破，交易次數少、勝率傾向較高——偏穩定。";
}

function lossRHint(r: number): string {
  if (r <= 2) {
    return "日限好緊：一日輸到約 2R 就停手，回撤控制強，但可能提早冇咗反攻機會。";
  }
  if (r <= 3) {
    return "日限中等：留少少空間畀策略恢復，同時唔會無限放任。";
  }
  return "日限較寬：容許較多連敗，潛在回撤大啲，要靠其他條件（例如早停新單）補穩定。";
}

/** Compact chips for roster cards. */
export function buildParamChips(src: DaytradeParamSource): ParamChip[] {
  const chips: ParamChip[] = [];
  if (src.or_minutes != null) {
    chips.push({ key: "or", label: "OR", value: `${src.or_minutes} 分` });
  }
  if (src.no_new_entry_after) {
    chips.push({
      key: "cut",
      label: "停新單",
      value: src.no_new_entry_after,
    });
  }
  if (src.force_flat_time) {
    chips.push({
      key: "flat",
      label: "強平",
      value: src.force_flat_time,
    });
  }
  if (src.max_daily_loss_r != null) {
    chips.push({
      key: "r",
      label: "日限",
      value: `${src.max_daily_loss_r}R`,
    });
  }
  if (src.quantity != null) {
    chips.push({ key: "qty", label: "手數", value: String(src.quantity) });
  }
  return chips;
}

/** Long-form rows for the detail drawer. */
export function buildParamExplainRows(
  src: DaytradeParamSource,
): ParamExplainRow[] {
  const or = src.or_minutes ?? 5;
  const maxR = src.max_daily_loss_r ?? 4;
  const cut = src.no_new_entry_after ?? "14:30";
  const flat = src.force_flat_time ?? "14:45";
  const qty = src.quantity ?? 1;
  const rthStart = src.rth_start ?? "08:30";
  const rthEnd = src.rth_end ?? "15:00";
  const tz = src.timezone ?? "America/Chicago";

  return [
    {
      key: "strategy",
      title: "策略",
      value: strategyLabel(src.strategy_id),
      body:
        "開盤後用一段時間畫出高低區間（Opening Range）。價格突破上沿就做多、跌破下沿就做空；止蝕放喺區間另一邊，目標約 1 倍風險（1R）。同一根 bar 兩邊都觸到就唔入，避免曖昧訊號。",
    },
    {
      key: "or",
      title: "開盤區間（OR）",
      value: `${or} 分鐘`,
      body: orQualityHint(or),
    },
    {
      key: "session",
      title: "交易時段",
      value: `${rthStart}–${rthEnd}（${tz}）`,
      body: "只喺正規交易時段（RTH）入市同管理倉位。時鐘用芝加哥時間，同美股指數期貨對齊。",
    },
    {
      key: "cut",
      title: "停新單時間",
      value: cut,
      body: `過咗 ${cut} 就唔再開新倉，只管理已有持倉。提早停新單可以避開中午前／午後噪音，係穩定性嘅主要手段之一。`,
    },
    {
      key: "flat",
      title: "強制平倉",
      value: flat,
      body: `到 ${flat} 若仲有倉會強制平倉，避免隔夜。日內模擬要求當日清倉。`,
    },
    {
      key: "risk",
      title: "日損上限",
      value: `${maxR}R`,
      body: lossRHint(maxR),
    },
    {
      key: "size",
      title: "手數 / 標的",
      value: `${qty} 口 · ${src.symbol ?? "—"}`,
      body: "每位交易員獨立帳本；口數決定單筆風險同回報幅度。唔同標的（NQ / YM / GC）波動同點值唔同，同一套 OR 參數表現可以差好遠。",
    },
  ];
}

export function strategyShortLabel(strategyId: string | undefined): string {
  return strategyLabel(strategyId);
}
