"""Explicit zero-write backtest preview endpoint for P2 strategy workbench."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner
from futures_research.backtest.scorecard import compute_opportunity_funnel
from futures_research.data.contracts import (
    ContractRegistry,
    ContractSpec,
    ExecutionCostSpec,
    SlippageTicks,
)
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.errors import StrategyValidationError, StrategyValidationIssue
from futures_research.strategy.parser import parse_strategy_document

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


class PreviewAssumptions(BaseModel):
    """The only preview assumptions; each maps to the standard execution domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_capital_usd: float = Field(gt=0)
    commission_per_side: float = Field(ge=0)
    slippage_ticks: int = Field(ge=0)

    @field_validator("initial_capital_usd", "commission_per_side")
    @classmethod
    def require_finite_number(cls, value: float) -> float:
        if not isfinite(value):
            msg = "preview assumptions must be finite"
            raise ValueError(msg)
        return value


class BacktestPreviewBody(BaseModel):
    """`backtest_preview_request.v1` as deliberately unpersisted strategy source."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["backtest_preview_request.v1"] = Field(
        alias="schema",
        serialization_alias="schema",
    )
    source_text: str = Field(min_length=1)
    filename: str | None = Field(default=None, max_length=512)
    contract_id: str = Field(min_length=1, max_length=128)
    range_start: datetime
    range_end: datetime
    assumptions: PreviewAssumptions

    @field_validator("range_start", "range_end")
    @classmethod
    def require_aware_utc_range(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "preview range timestamps must include an offset"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_nonempty_range(self) -> BacktestPreviewBody:
        if self.range_start >= self.range_end:
            msg = "range_start must be earlier than range_end after UTC normalization"
            raise ValueError(msg)
        return self


def _validation_document(
    issues: tuple[StrategyValidationIssue, ...] | list[StrategyValidationIssue] = (),
    *,
    name: str | None = None,
    universe: dict[str, object] | None = None,
) -> dict[str, object]:
    """Use the same four-layer terminal-AI report shape as strategy validation."""
    materialized = tuple(issues)
    return {
        "schema": "strategy_validation.v1",
        "valid": not materialized,
        "issue_count": len(materialized),
        "issues": [
            {
                "path": issue.path,
                "message": issue.message,
                "fix": issue.fix,
                "layer": issue.layer,
                "line": issue.format_line(),
            }
            for issue in materialized
        ],
        "report_text": "\n".join(issue.format_line() for issue in materialized),
        **({"name": name} if name is not None else {}),
        **({"universe": universe} if universe is not None else {}),
    }


def _fingerprint(body: BacktestPreviewBody) -> dict[str, str]:
    """Hash every explicit request field; changing any parameter changes the fingerprint."""
    payload = body.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"algorithm": "sha256", "digest": sha256(canonical.encode("utf-8")).hexdigest()}


def _raw_request_fingerprint(raw_body: bytes) -> dict[str, str]:
    """Fingerprint the exact request bytes before any coercion or validation.

    Preview is a dry-run artifact, so even a rejected request needs a stable
    identity that reflects what the caller actually sent.  Hashing raw bytes
    also means Pydantic never gets a chance to normalize a value before it is
    represented in the response.
    """
    return {"algorithm": "sha256", "digest": sha256(raw_body).hexdigest()}


def _request_validation_issues(exc: ValidationError) -> list[StrategyValidationIssue]:
    """Render transport/model failures in the same paste-back shape as YAML errors."""
    issues: list[StrategyValidationIssue] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        parts = ["schema" if item == "schema_version" else str(item) for item in loc]
        path = ".".join(parts) if parts else "(root)"
        message = str(error.get("msg") or "invalid preview request value")
        fix = _request_validation_fix(path, message=message)
        issues.append(
            StrategyValidationIssue(
                path=path,
                message=message,
                fix=fix,
                layer="format",
            )
        )
    return issues


def _request_validation_fix(path: str, *, message: str) -> str:
    """Give terminal callers a field-specific correction rather than a generic 422."""
    if path == "schema":
        return "set schema to backtest_preview_request.v1"
    if path == "source_text":
        return "provide non-empty strategy.v1 YAML in source_text"
    if path == "contract_id":
        return "provide one non-empty configured exact contract_id"
    if path in {"range_start", "range_end"}:
        return "provide ISO-8601 range timestamps with explicit UTC offsets and start before end"
    if path == "(root)":
        if "range_start" in message or "range_end" in message:
            return "provide range_start earlier than range_end with explicit UTC offsets"
        return "send one JSON object matching backtest_preview_request.v1"
    if path.startswith("assumptions."):
        return "provide finite non-negative costs and a positive finite initial_capital_usd"
    return "correct the field type/value to match backtest_preview_request.v1"


def _invalid_json_issue(exc: ValueError) -> StrategyValidationIssue:
    """Keep malformed JSON inside the preview envelope instead of FastAPI's default body error."""
    return StrategyValidationIssue(
        path="(root)",
        message=f"request body is not valid JSON ({exc})",
        fix="send one JSON object matching backtest_preview_request.v1",
        layer="format",
    )


def _failure(
    *,
    fingerprint: dict[str, str],
    validation: dict[str, object],
    errors: list[str],
) -> JSONResponse:
    """Return a complete dry-run response while guaranteeing no persistence happened."""
    return JSONResponse(
        status_code=422,
        content={
            "schema": "backtest_preview.v1",
            "run_scope": "dry_run",
            "request_fingerprint": fingerprint,
            "validation": validation,
            "funnel": None,
            "decision_evidence": [],
            "rejection_evidence": [],
            "evidence_summary": {
                "availability": "unavailable",
                "complete": False,
                "evaluation_count": 0,
                "rejection_count": 0,
                "layer_reached_counts": {},
                "blocking_condition_counts": {},
                "deepest_layer": None,
                "trade_count": 0,
            },
            "charts": {},
            "persisted": False,
            "warnings": [],
            "errors": errors,
        },
    )


def _preview_runner(request: Request) -> BacktestRunner:
    """Build a runner whose writer objects are present but never invoked by preview."""
    override = getattr(request.app.state, "backtest_preview_runner", None)
    if isinstance(override, BacktestRunner):
        return override
    data_root_value = getattr(request.app.state, "backtest_preview_data_root", None)
    data_root = Path(data_root_value) if data_root_value is not None else PROJECT_ROOT / "data"
    from futures_research.backtest.persistence import ResultExporter, SqliteRunStore

    return BacktestRunner(
        canonical_store=CanonicalStore(data_root / "market"),
        daily_canonical_store=CanonicalStore(data_root / "market-daily"),
        run_store=SqliteRunStore(data_root / "backtests" / "runs.sqlite3"),
        result_exporter=ResultExporter(data_root / "backtests" / "results"),
        quality_reports_root=None,
    )


def _contract_for_id(registry: ContractRegistry, contract_id: str) -> ContractSpec | None:
    return next(
        (
            contract
            for contract in registry.contracts.values()
            if contract.contract_id == contract_id
        ),
        None,
    )


def _preview_contract(contract: ContractSpec, assumptions: PreviewAssumptions) -> ContractSpec:
    """Apply request costs through the exact same ExecutionCostSpec domain as a real run."""
    costs = ExecutionCostSpec(
        commission_per_side=assumptions.commission_per_side,
        slippage_ticks=SlippageTicks(
            breakout_entry=assumptions.slippage_ticks,
            stop_exit=assumptions.slippage_ticks,
            target_exit=assumptions.slippage_ticks,
            day_end_exit=assumptions.slippage_ticks,
        ),
        target_requires_through=contract.execution_costs.target_requires_through,
    )
    return contract.model_copy(update={"execution_costs": costs})


@router.post("/preview", response_model=None)
async def preview_backtest(request: Request) -> Any:
    """Run the canonical parser/engine/evidence path with fixed zero persistence."""
    raw_body = await request.body()
    fingerprint = _raw_request_fingerprint(raw_body)
    try:
        raw_request = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        issue = _invalid_json_issue(exc)
        validation = _validation_document([issue])
        return _failure(
            fingerprint=fingerprint,
            validation=validation,
            errors=[issue.format_line()],
        )
    try:
        body = BacktestPreviewBody.model_validate(raw_request)
    except ValidationError as exc:
        request_issues = _request_validation_issues(exc)
        validation = _validation_document(request_issues)
        return _failure(
            fingerprint=fingerprint,
            validation=validation,
            errors=[str(validation["report_text"])],
        )
    try:
        parsed = parse_strategy_document(body.source_text)
    except StrategyValidationError as exc:
        validation = _validation_document(exc.issues)
        report_text = validation["report_text"]
        return _failure(
            fingerprint=fingerprint,
            validation=validation,
            errors=[report_text if isinstance(report_text, str) else "validation failed"],
        )

    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract = _contract_for_id(registry, body.contract_id)
    issues: list[StrategyValidationIssue] = []
    if contract is None:
        issues.append(
            StrategyValidationIssue(
                path="contract_id",
                message="preview requires one configured exact contract_id",
                fix="set contract_id to an id from config/contracts.yaml",
                layer="references",
            )
        )
    elif contract.symbol not in parsed.document.universe.contracts:
        issues.append(
            StrategyValidationIssue(
                path="contract_id",
                message="preview contract is outside strategy universe.contracts",
                fix=(
                    "choose a contract declared in universe.contracts "
                    "or revise the strategy source"
                ),
                layer="references",
            )
        )
    elif contract.asset_class != parsed.document.universe.asset_class:
        issues.append(
            StrategyValidationIssue(
                path="contract_id",
                message="preview contract class does not match the strategy universe",
                fix="choose an exact contract_id whose root symbol is in the approved universe",
                layer="references",
            )
        )
    elif parsed.document.universe.session not in contract.sessions:
        issues.append(
            StrategyValidationIssue(
                path="contract_id",
                message="preview contract does not support the strategy universe session",
                fix="choose a configured contract that supports universe.session",
                layer="references",
            )
        )
    validation = _validation_document(
        issues,
        name=parsed.document.meta.name,
        universe={
            "primary_instrument": parsed.document.universe.primary_instrument,
            "asset_class": parsed.document.universe.asset_class,
            "contracts": list(parsed.document.universe.contracts),
            "expansion_rationale": dict(parsed.document.universe.expansion_rationale),
            "session": parsed.document.universe.session,
        },
    )
    if issues or contract is None:
        return _failure(
            fingerprint=fingerprint,
            validation=validation,
            errors=[str(validation["report_text"])],
        )

    try:
        preview = _preview_runner(request).preview(
            contract=_preview_contract(contract, body.assumptions),
            config=BacktestRunConfig(
                # Internal only.  It is never returned and is not allocated from
                # the durable run namespace.
                run_id="preview",
                strategy_version="preview_source",
                session_name=parsed.document.universe.session,
                range_start=body.range_start,
                range_end=body.range_end,
                initial_capital=body.assumptions.initial_capital_usd,
                quantity=1,
                verify_nautilus_replay=False,
                strategy_spec=parsed.spec,
            ),
        )
    except (LookupError, OSError, RuntimeError, ValueError) as exc:
        return _failure(
            fingerprint=fingerprint,
            validation=validation,
            errors=[f"{type(exc).__name__}: {exc}"],
        )

    events = [
        {
            "event_type": event.event_type.value,
            "details": dict(event.details),
            "timestamp": event.timestamp.isoformat().replace("+00:00", "Z"),
        }
        for event in preview.result.event_log
    ]
    funnel = compute_opportunity_funnel(events, fills=preview.result.metrics.trade_count).to_dict()
    assert preview.result.evidence_summary is not None
    return {
        "schema": "backtest_preview.v1",
        "run_scope": "dry_run",
        "request_fingerprint": fingerprint,
        "validation": validation,
        "funnel": funnel,
        "decision_evidence": [
            record.decision_evidence.to_dict()
            for record in preview.result.trade_records
            if record.decision_evidence is not None
        ],
        "rejection_evidence": [
            record.to_dict() for record in preview.result.rejection_evidence
        ],
        "evidence_summary": preview.result.evidence_summary.to_dict(),
        "charts": preview.charts,
        "persisted": False,
        "warnings": list(preview.result.warnings),
        "errors": [],
    }
