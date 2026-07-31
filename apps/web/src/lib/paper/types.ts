/**
 * P6 Stage A wire types.
 *
 * Authority:
 * - docs/superpowers/specs/2026-07-29-p6-isolated-creation-seam-design.md
 *   §6.1, §6.1a, §6.2, §6.3, §6.4, §6.5, §7;
 * - docs/superpowers/specs/2026-07-29-p6-contract-candidate-source-correction-design.md
 *   (approach C, commit 319911c);
 * - docs/ui/designs/p6-paper.html constraints #3b–#3k.
 *
 * Every key set below is exact and frozen. No optional key, alias, coercion or
 * convenience default is permitted anywhere in this batch.
 */

// --- selections -------------------------------------------------------------

/** Identity echoed by `paper_contract_list.v1`. */
export interface PaperStrategySelection {
  strategy_id: string;
  content_sha256: string;
}

/** Identity echoed by `paper_baseline_list.v1`. */
export interface PaperContractSelection extends PaperStrategySelection {
  contract_id: string;
}

/** Identity submitted to readiness / create and echoed back. */
export interface PaperSelection extends PaperContractSelection {
  baseline_run_id: string;
  baseline_result_sha256: string;
}

export const PAPER_STRATEGY_SELECTION_KEYS = [
  "strategy_id",
  "content_sha256",
] as const;

export const PAPER_CONTRACT_SELECTION_KEYS = [
  "strategy_id",
  "content_sha256",
  "contract_id",
] as const;

export const PAPER_SELECTION_KEYS = [
  "strategy_id",
  "content_sha256",
  "contract_id",
  "baseline_run_id",
  "baseline_result_sha256",
] as const;

// --- eligibility (§6.1, reused producer) ------------------------------------

export interface PaperSupportingDecision {
  decision_id: string;
  run_id: string;
}

export interface PaperEligibleStrategy {
  strategy_id: string;
  content_sha256: string;
  /** Display name from confirmed strategy store; optional for older fixtures. */
  name?: string;
  /**
   * Optional Owner history from PromotionDecision. Never a selection gate —
   * paper loads confirmed strategies from DB independently (docs/10).
   */
  supporting_decisions: PaperSupportingDecision[];
}

export interface PaperEligibleStrategyList {
  schema: "eligible_strategy_list.v1";
  count: number;
  strategies: PaperEligibleStrategy[];
}

// --- contract candidates (§6.1a, approach C) --------------------------------

export interface PaperContractRow {
  contract_id: string;
  symbol: string;
  display_name: string;
}

export interface PaperContractList {
  schema: "paper_contract_list.v1";
  selection: PaperStrategySelection;
  count: number;
  contracts: PaperContractRow[];
}

// --- baseline candidates (§6.2) ---------------------------------------------

/**
 * The approved contract documents exactly one integrity value. An unknown
 * value is an unknown enum and therefore fails closed; the frontend never
 * guesses a second member.
 */
export const PAPER_BASELINE_INTEGRITY_VALUES = ["verified"] as const;

export type PaperBaselineIntegrity =
  (typeof PAPER_BASELINE_INTEGRITY_VALUES)[number];

export interface PaperBaseline {
  run_id: string;
  result_sha256: string;
  range_start: string;
  range_end: string;
  currency: string;
  initial_capital: number;
  trade_count: number;
  net_r: number;
  validation_run: boolean;
  integrity: PaperBaselineIntegrity;
}

export interface PaperBaselineList {
  schema: "paper_baseline_list.v1";
  selection: PaperContractSelection;
  count: number;
  baselines: PaperBaseline[];
}

// --- readiness (§6.3) -------------------------------------------------------

/** Exact four checks, in exact order, no fifth member. */
export const PAPER_READINESS_CHECK_KEYS = [
  "ib_realtime",
  "exchange_calendar",
  "telegram",
  "baseline_integrity",
] as const;

export type PaperReadinessCheckKey =
  (typeof PAPER_READINESS_CHECK_KEYS)[number];

export const PAPER_CHECK_STATUSES = ["ready", "blocked", "unknown"] as const;
export type PaperCheckStatus = (typeof PAPER_CHECK_STATUSES)[number];

export const PAPER_MARKET_SESSIONS = ["open", "closed", "unknown"] as const;
export type PaperMarketSession = (typeof PAPER_MARKET_SESSIONS)[number];

export const PAPER_OVERALL_STATES = ["ready", "blocked"] as const;
export type PaperOverallState = (typeof PAPER_OVERALL_STATES)[number];

export interface PaperReadinessCheck {
  key: PaperReadinessCheckKey;
  status: PaperCheckStatus;
  reason: string;
  checked_at: string;
}

export interface PaperReadiness {
  schema: "paper_readiness.v1";
  selection: PaperSelection;
  overall: PaperOverallState;
  market_session: PaperMarketSession;
  checked_at: string;
  checks: PaperReadinessCheck[];
}

/** Immutable recheck result stored inside the trader record (§6.4). */
export interface PaperReadinessSnapshot {
  schema: "paper_readiness_snapshot.v1";
  overall: PaperOverallState;
  market_session: PaperMarketSession;
  checked_at: string;
  checks: PaperReadinessCheck[];
}

export interface PaperReadinessRequest {
  schema: "paper_readiness_request.v1";
  selection: PaperSelection;
}

// --- provisioning authorization (Option A §7) -------------------------------

/*
 * Five authorities stay separate (Option A §5). These types describe exactly
 * one of them: whether one already-approved selection may persist a single
 * trader that is not running. They never describe runtime readiness, a right
 * to start the engine, or an IB session.
 */

export const PAPER_PROVISIONING_STATES = [
  "disabled",
  "armed",
  "claimed",
  "consumed",
  "expired",
] as const;

export type PaperProvisioningState =
  (typeof PAPER_PROVISIONING_STATES)[number];

/** §6.1: an authorized permit lives exactly thirty minutes. */
export const PAPER_PROVISIONING_PERMIT_MINUTES = 30;

export interface PaperProvisioningAuthorization {
  schema: "paper_provisioning_authorization.v1";
  state: PaperProvisioningState;
  /** `paper-provision-` plus 32 lowercase hex, or null while disabled. */
  operation_id: string | null;
  authorized_at: string | null;
  expires_at: string | null;
  reason: string;
}

export interface PaperProvisioningReadiness {
  schema: "paper_provisioning_readiness.v1";
  selection: PaperSelection;
  /** True only for an armed permit on this exact selection (§7.3). */
  can_provision: boolean;
  authorization: PaperProvisioningAuthorization;
  /** The unchanged four-check truth; never rewritten by authorization. */
  runtime_readiness: PaperReadiness;
}

export interface PaperProvisioningReadinessRequest {
  schema: "paper_provisioning_readiness_request.v1";
  selection: PaperSelection;
}

// --- trader record (§6.4, §6.5) ---------------------------------------------

/** Stage A honest lifecycle: identity provisioned, simulation not started. */
export const PAPER_LIFECYCLE_STATUSES = ["provisioned"] as const;
export type PaperLifecycleStatus = (typeof PAPER_LIFECYCLE_STATUSES)[number];

/**
 * Stage A safeguards are closed literals, not "any positive number": the
 * producer declares `Literal[8] / Literal[8] / Literal[5]` and the store
 * enforces the same values with CHECK constraints.
 */
export const PAPER_STAGE_A_SAFEGUARDS = {
  max_drawdown_r: 8,
  max_losing_streak: 8,
  blind_minutes: 5,
} as const;

export type PaperSafeguards = typeof PAPER_STAGE_A_SAFEGUARDS;

/** The producer declares this exact literal for every Stage A record. */
export const PAPER_STAGE_A_LIFECYCLE_REASON =
  "simulation runtime is not activated in Stage A";

export type PaperLifecycleReason = typeof PAPER_STAGE_A_LIFECYCLE_REASON;

export interface PaperTrader {
  schema: "paper_trader.v1";
  trader_id: string;
  request_id: string;
  strategy: {
    strategy_id: string;
    content_sha256: string;
  };
  contract_id: string;
  baseline: {
    run_id: string;
    result_sha256: string;
    range_start: string;
    range_end: string;
  };
  account: {
    account_id: string;
    currency: string;
    initial_capital: number;
  };
  safeguards: PaperSafeguards;
  lifecycle: {
    status: PaperLifecycleStatus;
    reason: PaperLifecycleReason;
    as_of: string;
  };
  readiness_snapshot: PaperReadinessSnapshot;
  created_at: string;
}

export interface PaperTraderCreateRequest {
  schema: "paper_trader_create_request.v1";
  request_id: string;
  selection: PaperSelection;
}

export const PAPER_REQUEST_STATUSES = ["completed"] as const;
export type PaperRequestStatus = (typeof PAPER_REQUEST_STATUSES)[number];

export interface PaperTraderRequestStatusRecord {
  schema: "paper_trader_request_status.v1";
  request_id: string;
  status: PaperRequestStatus;
  trader: PaperTrader;
}

export interface PaperTraderList {
  schema: "paper_trader_list.v1";
  count: number;
  traders: PaperTrader[];
}

// --- error contract (§7) ----------------------------------------------------

/**
 * Exact business error codes and the exact HTTP status each one is served
 * with. A known code arriving on a different status is a contract drift and
 * fails closed.
 */
export const PAPER_ERROR_STATUS = {
  eligible_strategy_required: 409,
  baseline_not_found: 404,
  selection_identity_mismatch: 409,
  baseline_integrity_failed: 503,
  readiness_blocked: 409,
  activation_not_authorized: 503,
  request_id_conflict: 409,
  request_not_found: 404,
  store_unavailable: 503,
  // Option A §7.5: additive provisioning outcomes on the same envelope.
  external_readiness_invalid: 503,
  provisioning_not_authorized: 503,
  provisioning_selection_mismatch: 409,
  provisioning_request_conflict: 409,
} as const;

export type PaperErrorCode = keyof typeof PAPER_ERROR_STATUS;

export interface PaperApiError {
  schema: "paper_api_error.v1";
  code: PaperErrorCode;
  message: string;
  /** v1 business errors are always non-retryable; `true` is not a v1 state. */
  retryable: false;
}

// ===========================================================================
// P6 Stage B wire types
//
// Authority:
// - docs/superpowers/specs/2026-07-29-p6-stage-b-ledger-review-bridge-design.md
//   §3.1 shared scalar boundary, §8 ledger, §9 review lifecycle, §10 error,
//   §11 provisioned profile, §12 closest three;
// - docs/work-orders/AGENT_Y_V55_P6_STAGE_B_WIRE_CONTRACTS.md.
//
// These are wire shapes only. `paper_trader.v1` above is deliberately NOT
// extended: Stage B evidence lives in its own resources.
// ===========================================================================

/** §8: one immutable ledger origin per provisioned trader. */
export const PAPER_LEDGER_LIFECYCLE_STATUS = "provisioned";
export const PAPER_LEDGER_ENGINE_STATUS = "not_enabled";
export const PAPER_LEDGER_SAFETY_STATE = "not_running";

/** §12: the only comparator the frontend may accept, and never recompute. */
export const PAPER_CLOSEST_ALGORITHM = "p5_structural_closest.v1";

/** §12: the comparator returns at most three refs. */
export const PAPER_CLOSEST_REF_LIMIT = 3;

/** §8 and §11.2: the provisioned profile freezes every high-water mark. */
export const PAPER_LEDGER_HIGH_WATER_MARKS = {
  trades: 0,
  equity: 1,
  events: 4,
  expected_decisions: 0,
  positions: 0,
  orders: 0,
} as const;

export type PaperLedgerHighWaterMarks = typeof PAPER_LEDGER_HIGH_WATER_MARKS;

/** §8 and §11.2: zero-trade evidence is not the same as "no divergence". */
export const PAPER_EVALUATION_STATUS = "not_evaluable";
export const PAPER_EVALUATION_REASON = "engine_not_enabled";
export const PAPER_INTERPRETATION_CATEGORIES = ["unknown"] as const;

export interface PaperEvidenceMember {
  readonly path: string;
  readonly bytes: number;
  readonly sha256: string;
}

export interface PaperSupportingEvidenceRef {
  readonly path: string;
  readonly evidence_id: string;
}

export interface PaperLedgerOrigin {
  readonly schema: "paper_ledger_origin.v1";
  readonly ledger_origin_id: string;
  readonly trader_id: string;
  readonly origin_at: string;
  readonly lifecycle: {
    readonly status: typeof PAPER_LEDGER_LIFECYCLE_STATUS;
    readonly engine_status: typeof PAPER_LEDGER_ENGINE_STATUS;
  };
  readonly strategy: {
    readonly strategy_id: string;
    readonly name: string;
    readonly content_sha256: string;
  };
  readonly contract: {
    readonly contract_id: string;
    readonly exchange: string;
    readonly timezone: string;
  };
  readonly baseline: {
    readonly run_id: string;
    readonly result_sha256: string;
    readonly range_start: string;
    readonly range_end: string;
    readonly rejection_count: number;
    readonly closest_algorithm: typeof PAPER_CLOSEST_ALGORITHM;
    readonly closest_rejection_refs: readonly string[];
    readonly members: readonly PaperEvidenceMember[];
  };
  readonly account: {
    readonly account_id: string;
    readonly currency: string;
    readonly initial_capital: number;
    readonly independent_account: true;
  };
  readonly balances: {
    readonly cash: number;
    readonly equity: number;
    readonly realized_pnl: number;
    readonly unrealized_pnl: number;
  };
  readonly high_water_marks: PaperLedgerHighWaterMarks;
  readonly positions: readonly never[];
  readonly orders: readonly never[];
  readonly safety: {
    readonly state: typeof PAPER_LEDGER_SAFETY_STATE;
    readonly drawdown_r: 0;
    readonly loss_streak: 0;
    readonly max_drawdown_r: PaperSafeguards["max_drawdown_r"];
    readonly max_losing_streak: PaperSafeguards["max_losing_streak"];
    readonly blind_minutes: PaperSafeguards["blind_minutes"];
  };
  readonly readiness_snapshot: PaperReadinessSnapshot;
  readonly interpretation: {
    readonly evaluation_status: typeof PAPER_EVALUATION_STATUS;
    readonly reason: typeof PAPER_EVALUATION_REASON;
    readonly owner_view: string;
    readonly categories: readonly ["unknown"];
    readonly supporting_evidence_refs: readonly PaperSupportingEvidenceRef[];
  };
}

/** §9.1: the frontend submits an identity and nothing else. */
export interface PaperReviewCreateRequest {
  readonly schema: "paper_review_create_request.v1";
  readonly request_id: string;
}

/** §11.1: seven fixed member paths; three more depend on the baseline run. */
export const PAPER_REVIEW_FIXED_MEMBER_PATHS = [
  "paper-review.json",
  "paper/ledger-origin.json",
  "paper/trades.json",
  "paper/equity.json",
  "paper/events.json",
  "divergence/expected-actual.json",
  "baseline/result.json",
] as const;

export const PAPER_REVIEW_MEMBER_COUNT = 10;
export const PAPER_REVIEW_TOTAL_PARTS = 10;

export const PAPER_REVIEW_STATUSES = ["preparing", "ready", "failed"] as const;
export type PaperReviewStatusValue = (typeof PAPER_REVIEW_STATUSES)[number];

export interface PaperReviewProgress {
  readonly completed_parts: number;
  readonly total_parts: typeof PAPER_REVIEW_TOTAL_PARTS;
  readonly current_part: string | null;
}

export interface PaperReviewReady {
  readonly schema: "paper_review_ready.v1";
  readonly display_filename: string;
  readonly artifact_bytes: number;
  readonly artifact_sha256: string;
  readonly member_count: typeof PAPER_REVIEW_MEMBER_COUNT;
  readonly members: readonly PaperEvidenceMember[];
  readonly terminal_opener: {
    readonly bytes: number;
    readonly sha256: string;
  };
  readonly ready_at: string;
}

/** §10: Stage B keeps its own error contract; `paper_api_error.v1` is frozen. */
export const PAPER_REVIEW_ERROR_HTTP = {
  trader_not_found: 404,
  ledger_not_ready: 409,
  ledger_integrity_failed: 503,
  store_schema_upgrade_required: 503,
  request_not_found: 404,
  request_id_conflict: 409,
  snapshot_not_found: 404,
  snapshot_not_ready: 409,
  snapshot_failed: 409,
  snapshot_integrity_failed: 503,
  artifact_build_failed: 503,
  artifact_unavailable: 503,
  build_interrupted: 503,
} as const;

export type PaperReviewErrorCode = keyof typeof PAPER_REVIEW_ERROR_HTTP;

/**
 * §10: `retryable` means only that re-reading the same identity is safe.
 * It never authorises rebuilding the same request.
 */
export const PAPER_REVIEW_RETRYABLE_CODES = ["snapshot_not_ready"] as const;

export const PAPER_REVIEW_ISSUE_KINDS = [
  "missing_member",
  "unsafe_path",
  "unresolved_ref",
  "hash_mismatch",
  "identity_mismatch",
  "count_mismatch",
  "cutoff_unavailable",
  "artifact_build_failed",
  "artifact_unavailable",
  "build_interrupted",
] as const;

export type PaperReviewIssueKind = (typeof PAPER_REVIEW_ISSUE_KINDS)[number];

export interface PaperReviewIssue {
  readonly kind: PaperReviewIssueKind;
  readonly path: string | null;
  readonly source_ref: string | null;
  readonly expected_sha256: string | null;
  readonly actual_sha256: string | null;
  readonly ref_chain: readonly string[];
}

export interface PaperReviewError {
  readonly schema: "paper_review_error.v1";
  readonly code: PaperReviewErrorCode;
  readonly message: string;
  readonly retryable: boolean;
  readonly request_id: string | null;
  readonly snapshot_id: string | null;
  readonly progress: PaperReviewProgress | null;
  readonly issues: readonly PaperReviewIssue[];
}

/** §10: the HTTP wrapper is exactly one key. */
export interface PaperReviewErrorEnvelope {
  readonly detail: PaperReviewError;
}

export interface PaperReviewStatus {
  readonly schema: "paper_review_status.v1";
  readonly request_id: string;
  readonly snapshot_id: string;
  readonly trader_id: string;
  readonly status: PaperReviewStatusValue;
  readonly captured_at: string;
  readonly progress: PaperReviewProgress;
  readonly ready: PaperReviewReady | null;
  readonly error: PaperReviewError | null;
}

/**
 * §9.4. `bytes` and `sha256` are the producer claims about `text`. This batch
 * validates shape and identity only: confirming the digest needs async Web
 * Crypto and belongs to the later consumer batch, so the name must not suggest
 * the payload has already been cryptographically verified.
 */
export interface PaperReviewTerminalOpenerClaim {
  readonly schema: "paper_review_terminal_opener.v1";
  readonly snapshot_id: string;
  readonly text: string;
  readonly bytes: number;
  readonly sha256: string;
}
