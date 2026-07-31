import {
  parseP4StandardRequest,
  type LiveBatchStatus,
  type LiveJobStatus,
  type P4Batch,
  type P4BatchJob,
  type P4Precheck,
  type P4StandardRequest,
} from "../lib/backtest/liveContract";

export function makeStandardRequest(
  overrides: Partial<P4StandardRequest> = {},
): P4StandardRequest {
  const raw = {
    strategy_versions: ["strategy-0001"],
    symbols: ["NQ"],
    range_start: "2026-05-01T00:00:00Z",
    range_end: "2026-07-22T21:00:00Z",
    execution_assumptions: {
      initial_capital_usd: 100_000,
      commission_per_side_by_symbol: { NQ: 2.5 },
      slippage_ticks: {
        breakout_entry: 1,
        stop_exit: 2,
        target_exit: 0,
        day_end_exit: 1,
      },
    },
    duplicate_acknowledgements: [],
    ...overrides,
  };
  const parsed = parseP4StandardRequest(raw);
  if (!parsed) {
    throw new Error("test request is invalid");
  }
  return parsed;
}

export function makePrecheck(
  request: P4StandardRequest,
  option: {
    duplicate?: "none" | "exact" | "acknowledged" | "stale";
    checkedAt?: string;
  } = {},
): P4Precheck {
  const duplicate = option.duplicate ?? "none";
  const units = request.strategy_versions.flatMap((strategy) =>
    request.symbols.map((symbol) => {
      const acknowledged = request.duplicate_acknowledgements.some(
        (item) =>
          item.strategy_version === strategy &&
          item.symbol === symbol &&
          item.range_start === request.range_start &&
          item.range_end === request.range_end,
      );
      const duplicateStatus =
        duplicate === "exact" || duplicate === "stale"
          ? "block"
          : duplicate === "acknowledged"
            ? "warn"
            : "pass";
      const duplicateReason =
        duplicate === "exact"
          ? "duplicate_exact_match"
          : duplicate === "acknowledged"
            ? "duplicate_acknowledged"
            : duplicate === "stale"
              ? "duplicate_acknowledgement_stale"
              : "duplicate_none";
      return {
        strategy_version: strategy,
        symbol,
        contract_id: `${symbol}-202609`,
        session_name: "eth",
        range_start: request.range_start,
        range_end: request.range_end,
        status: duplicateStatus,
        reason_codes:
          duplicate === "none" ? [] : [duplicateReason],
        coverage: {
          status: "pass",
          requested_trading_date_count: 1,
          admitted_trading_date_count: 1,
          complete_trading_dates: ["2026-05-04"],
          owner_trusted_problem_trading_dates: [],
          owner_excluded_trading_dates: [],
          roll_blackout_trading_dates: [],
          excluded_trading_dates: [],
          blocking_problem_trading_dates: [],
          missing_native_daily_trading_dates: [],
          reason_codes: ["coverage_complete"],
        },
        warmup: {
          status: "pass",
          required_prior_trading_date_count: 95,
          available_prior_trading_date_count: 100,
          evaluable_trading_date_count: 1,
          first_evaluable_trading_date: "2026-05-04",
          suggested_range_start: null,
          reason_codes: ["warmup_sufficient"],
        },
        duplicate: {
          status: duplicateStatus,
          count_known: true,
          exact_match_count: duplicate === "none" ? 0 : 1,
          prior_run_ids: duplicate === "none" ? [] : [`run-old-${symbol}`],
          unindexed_candidate_count: 0,
          acknowledged,
          reason_codes: [duplicateReason],
        },
      };
    }),
  );
  const statuses = new Set(units.map((unit) => unit.status));
  const overall = statuses.has("block")
    ? "block"
    : statuses.has("warn")
      ? "warn"
      : "pass";
  return {
    schema: "backtest_precheck.v1",
    checked_at: option.checkedAt ?? "2026-07-27T08:00:00Z",
    overall_status: overall,
    can_submit: overall === "pass" || overall === "warn",
    unit_count: units.length,
    units,
  } as P4Precheck;
}

function jobFor(
  request: P4StandardRequest,
  strategy: string,
  symbol: string,
  index: number,
  status: LiveJobStatus,
): P4BatchJob {
  const started =
    status === "running" || status === "completed" || status === "failed"
      ? "2026-07-27T08:00:01Z"
      : null;
  const finished =
    status === "completed" || status === "failed"
      ? "2026-07-27T08:00:05Z"
      : status === "cancelled"
        ? "2026-07-27T08:00:02Z"
        : null;
  const progress =
    status === "queued" || status === "cancelled"
      ? null
      : status === "running"
        ? {
            current_trading_date: null,
            processed_trading_date_count: 0,
            total_trading_date_count: 2,
            trade_count: 0,
            realized_net_pnl_usd: 0,
            realized_net_r: 0,
            reported_at: "2026-07-27T08:00:01Z",
          }
        : {
            current_trading_date: "2026-05-05",
            processed_trading_date_count:
              status === "completed" ? 2 : 1,
            total_trading_date_count: 2,
            trade_count: 3,
            realized_net_pnl_usd: 125.5,
            realized_net_r: 0.8,
            reported_at: "2026-07-27T08:00:05Z",
          };
  const internal = {
    initial_capital_usd:
      request.execution_assumptions.initial_capital_usd,
    commission_per_side:
      request.execution_assumptions.commission_per_side_by_symbol[symbol],
    slippage_ticks: {
      ...request.execution_assumptions.slippage_ticks,
    },
    target_requires_through: true,
    fill_model: "conservative" as const,
    simulation_precision: "one_minute" as const,
    quantity: 1,
  };
  return {
    job_id: `job-${index + 1}`,
    run_id: `run-${index + 1}`,
    symbol,
    strategy_version: strategy,
    status,
    message: status === "completed" ? "ok" : "",
    started_at: started,
    finished_at: finished,
    result_path: status === "completed" ? `runs/run-${index + 1}` : null,
    strategy_source:
      status === "completed" ? "strategy_file" : null,
    warnings: [],
    progress,
    error_summary:
      status === "failed" ? "回測執行失敗" : null,
    error_full:
      status === "failed"
        ? `RuntimeError: ${symbol} minute coverage unavailable`
        : null,
    cancelled_at:
      status === "cancelled" ? "2026-07-27T08:00:02Z" : null,
    assumptions: {
      initial_capital_usd: internal.initial_capital_usd,
      commission_per_side: internal.commission_per_side,
      slippage_ticks: { ...internal.slippage_ticks },
      target_requires_through: true,
      fill_model: "conservative",
      bar_precision: "1m",
    },
    execution_assumptions: internal,
  };
}

function reduceStatus(statuses: readonly LiveJobStatus[]): LiveBatchStatus {
  const unique = new Set(statuses);
  if (unique.size === 1 && unique.has("queued")) {
    return "queued";
  }
  if (unique.has("queued") || unique.has("running")) {
    return "running";
  }
  if (unique.size === 1 && unique.has("completed")) {
    return "completed";
  }
  if (unique.size === 1 && unique.has("failed")) {
    return "failed";
  }
  if (unique.size === 1 && unique.has("cancelled")) {
    return "cancelled";
  }
  return "partial";
}

export function makeBatch(
  request: P4StandardRequest,
  statuses: LiveJobStatus[] = ["queued"],
  batchId = "batch-1",
): P4Batch {
  const cells = request.strategy_versions.flatMap((strategy) =>
    request.symbols.map((symbol) => ({ strategy, symbol })),
  );
  if (statuses.length !== cells.length) {
    throw new Error("one test status is required per matrix cell");
  }
  const jobs = cells.map((cell, index) =>
    jobFor(
      request,
      cell.strategy,
      cell.symbol,
      index,
      statuses[index],
    ),
  );
  const summary = {
    total: jobs.length,
    queued: jobs.filter((job) => job.status === "queued").length,
    running: jobs.filter((job) => job.status === "running").length,
    completed: jobs.filter((job) => job.status === "completed").length,
    failed: jobs.filter((job) => job.status === "failed").length,
    cancelled: jobs.filter((job) => job.status === "cancelled").length,
  };
  return {
    schema: "batch_job.v2",
    batch_id: batchId,
    status: reduceStatus(statuses),
    created_at: "2026-07-27T08:00:00Z",
    updated_at:
      statuses.every((status) => status === "queued")
        ? "2026-07-27T08:00:00Z"
        : statuses.some(
              (status) => status === "completed" || status === "failed",
            )
          ? "2026-07-27T08:00:05Z"
          : statuses.some((status) => status === "cancelled")
            ? "2026-07-27T08:00:02Z"
            : statuses.some((status) => status === "running")
              ? "2026-07-27T08:00:01Z"
            : "2026-07-27T08:00:02Z",
    request: request as unknown as Record<string, unknown>,
    jobs,
    summary,
  };
}
