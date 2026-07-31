import {
  PAPER_RUNTIME_LIFECYCLES,
  type PaperRuntimeCapabilities,
  type PaperRuntimeLifecycle,
  type PaperRuntimeSnapshot,
  type PaperSafetyLimits,
  type PaperTraderSelectionV2,
  type PaperTraderV2,
  type RuntimeTimeframeSelection,
  type StrategyTimeframeProfile,
} from "./runtimeTypes";

export type RuntimeParseResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

class RuntimeContractError extends Error {}

function parse<T>(resource: string, operation: () => T): RuntimeParseResult<T> {
  try {
    return { ok: true, value: operation() };
  } catch (error) {
    return {
      ok: false,
      error: `${resource}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }
}

function object(value: unknown, path: string): Record<string, unknown> {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new RuntimeContractError(`${path} must be a plain object`);
  }
  return value as Record<string, unknown>;
}

function exact(
  value: Record<string, unknown>,
  keys: readonly string[],
  path: string,
): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw new RuntimeContractError(`${path} has an unexpected key set`);
  }
}

function text(value: unknown, path: string): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value !== value.trim()
  ) {
    throw new RuntimeContractError(`${path} must be exact non-empty text`);
  }
  return value;
}

function literal<T extends string>(
  value: unknown,
  expected: T,
  path: string,
): T {
  if (value !== expected) {
    throw new RuntimeContractError(`${path} is not ${expected}`);
  }
  return expected;
}

function closed<T extends string>(
  value: unknown,
  values: readonly T[],
  path: string,
): T {
  if (typeof value !== "string" || !values.includes(value as T)) {
    throw new RuntimeContractError(`${path} is not a known enum member`);
  }
  return value as T;
}

function exactArray<T extends string>(
  value: unknown,
  expected: readonly T[],
  path: string,
): T[] {
  if (
    !Array.isArray(value) ||
    value.length !== expected.length ||
    value.some((item, index) => item !== expected[index])
  ) {
    throw new RuntimeContractError(`${path} is not in canonical order`);
  }
  return [...expected];
}

function stringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new RuntimeContractError(`${path} must be a non-empty array`);
  }
  const result = value.map((item, index) => text(item, `${path}[${index}]`));
  if (new Set(result).size !== result.length) {
    throw new RuntimeContractError(`${path} contains duplicates`);
  }
  return result;
}

function utc(value: unknown, path: string): string {
  const result = text(value, path);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(
      result,
    )
  ) {
    throw new RuntimeContractError(`${path} must be canonical UTC`);
  }
  return result;
}

function safeInteger(value: unknown, path: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    throw new RuntimeContractError(`${path} must be a safe integer`);
  }
  return value as number;
}

function sha(value: unknown, path: string): string {
  const result = text(value, path);
  if (!/^[0-9a-f]{64}$/.test(result)) {
    throw new RuntimeContractError(`${path} must be lowercase SHA-256`);
  }
  return result;
}

function enabled(value: unknown, path: string): { enabled: string[] } {
  const record = object(value, path);
  exact(record, ["enabled"], path);
  return { enabled: stringArray(record.enabled, `${path}.enabled`) };
}

function safety(value: unknown, path: string): PaperSafetyLimits {
  const record = object(value, path);
  exact(
    record,
    ["max_drawdown_r", "max_losing_streak", "blind_minutes"],
    path,
  );
  if (
    record.max_drawdown_r !== 8 ||
    record.max_losing_streak !== 8 ||
    record.blind_minutes !== 5
  ) {
    throw new RuntimeContractError(`${path} has unknown safety defaults`);
  }
  return {
    max_drawdown_r: 8,
    max_losing_streak: 8,
    blind_minutes: 5,
  };
}

function profile(value: unknown, path: string): StrategyTimeframeProfile {
  const record = object(value, path);
  exact(
    record,
    ["bias", "mid", "entry", "source", "client_override"],
    path,
  );
  if (record.client_override !== false) {
    throw new RuntimeContractError(`${path}.client_override must be false`);
  }
  return {
    bias: text(record.bias, `${path}.bias`),
    mid: text(record.mid, `${path}.mid`),
    entry: text(record.entry, `${path}.entry`),
    source: literal(record.source, "strategy.v1", `${path}.source`),
    client_override: false,
  };
}

function timeframes(
  value: unknown,
  path: string,
): RuntimeTimeframeSelection {
  const record = object(value, path);
  exact(record, ["market_input", "execution", "chart_display"], path);
  return {
    market_input: text(record.market_input, `${path}.market_input`),
    execution: text(record.execution, `${path}.execution`),
    chart_display: text(record.chart_display, `${path}.chart_display`),
  };
}

function selection(
  value: unknown,
  path: string,
): PaperTraderSelectionV2 {
  const record = object(value, path);
  exact(
    record,
    [
      "strategy_id",
      "content_sha256",
      "contract_id",
      "baseline_run_id",
      "baseline_result_sha256",
      "timeframes",
    ],
    path,
  );
  return {
    strategy_id: text(record.strategy_id, `${path}.strategy_id`),
    content_sha256: sha(record.content_sha256, `${path}.content_sha256`),
    contract_id: text(record.contract_id, `${path}.contract_id`),
    baseline_run_id: text(record.baseline_run_id, `${path}.baseline_run_id`),
    baseline_result_sha256: sha(
      record.baseline_result_sha256,
      `${path}.baseline_result_sha256`,
    ),
    timeframes: timeframes(record.timeframes, `${path}.timeframes`),
  };
}

export function parsePaperRuntimeCapabilities(
  value: unknown,
): RuntimeParseResult<PaperRuntimeCapabilities> {
  return parse("paper_runtime_capabilities.v1", () => {
    const record = object(value, "$");
    exact(
      record,
      [
        "schema",
        "timeframes",
        "market_modes",
        "safety_defaults",
        "lifecycle_states",
        "as_of",
      ],
      "$",
    );
    const timeframeRecord = object(record.timeframes, "$.timeframes");
    exact(
      timeframeRecord,
      [
        "market_input",
        "execution",
        "chart_display",
        "strategy_profile",
      ],
      "$.timeframes",
    );
    const strategyProfile = object(
      timeframeRecord.strategy_profile,
      "$.timeframes.strategy_profile",
    );
    exact(
      strategyProfile,
      ["source", "client_override"],
      "$.timeframes.strategy_profile",
    );
    if (strategyProfile.client_override !== false) {
      throw new RuntimeContractError(
        "$.timeframes.strategy_profile.client_override must be false",
      );
    }
    return {
      schema: literal(
        record.schema,
        "paper_runtime_capabilities.v1",
        "$.schema",
      ),
      timeframes: {
        market_input: enabled(
          timeframeRecord.market_input,
          "$.timeframes.market_input",
        ),
        execution: enabled(
          timeframeRecord.execution,
          "$.timeframes.execution",
        ),
        chart_display: enabled(
          timeframeRecord.chart_display,
          "$.timeframes.chart_display",
        ),
        strategy_profile: {
          source: literal(
            strategyProfile.source,
            "strategy.v1",
            "$.timeframes.strategy_profile.source",
          ),
          client_override: false,
        },
      },
      market_modes: exactArray(
        record.market_modes,
        ["live", "test_delayed"] as const,
        "$.market_modes",
      ) as ["live", "test_delayed"],
      safety_defaults: safety(record.safety_defaults, "$.safety_defaults"),
      lifecycle_states: exactArray(
        record.lifecycle_states,
        PAPER_RUNTIME_LIFECYCLES,
        "$.lifecycle_states",
      ) as unknown as typeof PAPER_RUNTIME_LIFECYCLES,
      as_of: utc(record.as_of, "$.as_of"),
    };
  });
}

export function parsePaperTraderV2(
  value: unknown,
): RuntimeParseResult<PaperTraderV2> {
  return parse("paper_trader.v2", () => {
    const record = object(value, "$");
    exact(
      record,
      [
        "schema",
        "trader_id",
        "request_id",
        "selection",
        "selection_fingerprint",
        "strategy_timeframe_profile",
        "lifecycle",
        "lifecycle_version",
        "lifecycle_reason",
        "account_id",
        "ledger_origin_id",
        "safety",
        "created_at",
      ],
      "$",
    );
    const lifecycle = closed(
      record.lifecycle,
      PAPER_RUNTIME_LIFECYCLES,
      "$.lifecycle",
    ) as PaperRuntimeLifecycle;
    return {
      schema: literal(record.schema, "paper_trader.v2", "$.schema"),
      trader_id: text(record.trader_id, "$.trader_id"),
      request_id: text(record.request_id, "$.request_id"),
      selection: selection(record.selection, "$.selection"),
      selection_fingerprint: sha(
        record.selection_fingerprint,
        "$.selection_fingerprint",
      ),
      strategy_timeframe_profile: profile(
        record.strategy_timeframe_profile,
        "$.strategy_timeframe_profile",
      ),
      lifecycle,
      lifecycle_version: safeInteger(
        record.lifecycle_version,
        "$.lifecycle_version",
        1,
      ),
      lifecycle_reason: text(
        record.lifecycle_reason,
        "$.lifecycle_reason",
      ),
      account_id: text(record.account_id, "$.account_id"),
      ledger_origin_id: text(
        record.ledger_origin_id,
        "$.ledger_origin_id",
      ),
      safety: safety(record.safety, "$.safety"),
      created_at: utc(record.created_at, "$.created_at"),
    };
  });
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RuntimeContractError(`${path} must be a finite number`);
  }
  return value;
}

export function parsePaperRuntimeSnapshot(
  value: unknown,
): RuntimeParseResult<PaperRuntimeSnapshot> {
  return parse("paper_runtime_snapshot.v1", () => {
    const record = object(value, "$");
    if (record.schema !== "paper_runtime_snapshot.v1") {
      throw new RuntimeContractError("$.schema is not paper_runtime_snapshot.v1");
    }
    const lifecycle = closed(
      record.lifecycle,
      PAPER_RUNTIME_LIFECYCLES,
      "$.lifecycle",
    ) as PaperRuntimeLifecycle;
    const safetyRecord = object(record.safety, "$.safety");
    return {
      schema: "paper_runtime_snapshot.v1",
      trader_id: text(record.trader_id, "$.trader_id"),
      selection_fingerprint: sha(
        record.selection_fingerprint,
        "$.selection_fingerprint",
      ),
      lifecycle,
      lifecycle_version: safeInteger(
        record.lifecycle_version,
        "$.lifecycle_version",
        1,
      ),
      lifecycle_reason: text(record.lifecycle_reason, "$.lifecycle_reason"),
      cash: finiteNumber(record.cash, "$.cash"),
      equity: finiteNumber(record.equity, "$.equity"),
      realized_pnl: finiteNumber(record.realized_pnl, "$.realized_pnl"),
      unrealized_pnl: finiteNumber(record.unrealized_pnl, "$.unrealized_pnl"),
      realized_r: finiteNumber(record.realized_r, "$.realized_r"),
      unrealized_r: finiteNumber(record.unrealized_r, "$.unrealized_r"),
      position_quantity: safeInteger(
        record.position_quantity,
        "$.position_quantity",
        -1_000_000,
      ),
      pending_intent_count: safeInteger(
        record.pending_intent_count,
        "$.pending_intent_count",
        0,
      ),
      decision_count: safeInteger(record.decision_count, "$.decision_count", 0),
      trade_count: safeInteger(record.trade_count, "$.trade_count", 0),
      safety: {
        max_drawdown_r: 8,
        max_losing_streak: 8,
        blind_minutes: 5,
        drawdown_r: finiteNumber(safetyRecord.drawdown_r, "$.safety.drawdown_r"),
        losing_streak: safeInteger(
          safetyRecord.losing_streak,
          "$.safety.losing_streak",
          0,
        ),
        equity_high_water_r: finiteNumber(
          safetyRecord.equity_high_water_r,
          "$.safety.equity_high_water_r",
        ),
      },
      as_of: utc(record.as_of, "$.as_of"),
    };
  });
}
