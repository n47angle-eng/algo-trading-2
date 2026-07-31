"""In-process run queue for P4 multi-strategy × multi-contract batches (WO-006 / 6-4).

WO-006 / 6-5: a job now resolves its ``strategy_version`` to the confirmed A2
StrategyVersion and injects the parsed ``StrategySpec`` into the run, so picking
a different version actually produces a different run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from futures_research.api.backtest_admission import (
    AdmissionEvaluation,
    BacktestAdmissionService,
    P4BatchRequest,
    RunAdmissionPlan,
    default_backtest_admission_service,
)
from futures_research.api.strategy_resolution import (
    ResolvedStrategy,
    StrategyResolutionError,
    resolve_strategy,
)
from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.runner import (
    BacktestRunConfig,
    BacktestRunner,
    DayProgressSnapshot,
)
from futures_research.data.contracts import ContractRegistry
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT

_LOGGER = logging.getLogger(__name__)
_PUBLIC_WORKER_FAILURE_MESSAGE = (
    "worker_failed:internal_error; check server logs before resubmitting"
)
_PUBLIC_CHART_WARNING = (
    "chart_sidecar_unavailable: result is complete; "
    "open the chart to retry cache materialization"
)
_BATCH_STATE_UNAVAILABLE_DETAIL = "batch operational state is unavailable"
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\)[^\s<>'\"]+"
)
_POSIX_PATH_PATTERN = re.compile(r"(?<![\w:/])/(?!/)[^\s<>'\"]+")
_SOURCE_LINE_PATTERN = re.compile(r"\bline\s+\d+\b|:\d+(?=\s|$)", re.IGNORECASE)
_HTML_PATTERN = re.compile(r"<[^>\n]*>")

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
BatchStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "partial",
]
Clock = Callable[[], datetime]


class BatchOperationalStateError(RuntimeError):
    """Persisted mutable batch state is malformed or cannot be trusted."""


class BatchPersistenceError(RuntimeError):
    """An atomic operational-state transition could not be published."""


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Strict public progress facts for one running or terminal batch cell."""

    current_trading_date: str | None
    processed_trading_date_count: int
    total_trading_date_count: int
    trade_count: int
    realized_net_pnl_usd: float
    realized_net_r: float
    reported_at: str

    def __post_init__(self) -> None:
        if self.total_trading_date_count <= 0:
            msg = "progress total_trading_date_count must be positive"
            raise ValueError(msg)
        if not 0 <= self.processed_trading_date_count <= self.total_trading_date_count:
            msg = "progress processed count must be within the exact total"
            raise ValueError(msg)
        if self.current_trading_date is None:
            if self.processed_trading_date_count != 0:
                msg = "progress without a trading date must have processed count zero"
                raise ValueError(msg)
        elif self.processed_trading_date_count == 0:
            msg = "progress with a trading date must have a positive processed count"
            raise ValueError(msg)
        else:
            _parse_trading_date(self.current_trading_date)
        if self.processed_trading_date_count == 0 and (
            self.trade_count != 0
            or self.realized_net_pnl_usd != 0
            or self.realized_net_r != 0
        ):
            msg = "first-day progress baseline must have exact zero trade, PnL, and R facts"
            raise ValueError(msg)
        if self.trade_count < 0:
            msg = "progress trade_count must not be negative"
            raise ValueError(msg)
        if not isfinite(self.realized_net_pnl_usd) or not isfinite(self.realized_net_r):
            msg = "progress PnL and R must be finite"
            raise ValueError(msg)
        _parse_canonical_utc(self.reported_at, field_name="progress reported_at")

    @classmethod
    def baseline(cls, *, total: int, reported_at: str) -> JobProgress:
        return cls(
            current_trading_date=None,
            processed_trading_date_count=0,
            total_trading_date_count=total,
            trade_count=0,
            realized_net_pnl_usd=0.0,
            realized_net_r=0.0,
            reported_at=reported_at,
        )

    @classmethod
    def from_day_snapshot(cls, snapshot: DayProgressSnapshot) -> JobProgress:
        payload = snapshot.to_dict()
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> JobProgress:
        expected = {
            "current_trading_date",
            "processed_trading_date_count",
            "total_trading_date_count",
            "trade_count",
            "realized_net_pnl_usd",
            "realized_net_r",
            "reported_at",
        }
        if set(payload) != expected:
            msg = "progress fields do not match batch_job.v2"
            raise ValueError(msg)
        current = payload["current_trading_date"]
        if current is not None and not isinstance(current, str):
            msg = "progress current_trading_date must be a string or null"
            raise TypeError(msg)
        return cls(
            current_trading_date=current,
            processed_trading_date_count=_strict_int(
                payload["processed_trading_date_count"],
                field_name="processed_trading_date_count",
            ),
            total_trading_date_count=_strict_int(
                payload["total_trading_date_count"],
                field_name="total_trading_date_count",
            ),
            trade_count=_strict_int(payload["trade_count"], field_name="trade_count"),
            realized_net_pnl_usd=_strict_number(
                payload["realized_net_pnl_usd"],
                field_name="realized_net_pnl_usd",
            ),
            realized_net_r=_strict_number(
                payload["realized_net_r"],
                field_name="realized_net_r",
            ),
            reported_at=_strict_string(payload["reported_at"], field_name="reported_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_trading_date": self.current_trading_date,
            "processed_trading_date_count": self.processed_trading_date_count,
            "total_trading_date_count": self.total_trading_date_count,
            "trade_count": self.trade_count,
            "realized_net_pnl_usd": self.realized_net_pnl_usd,
            "realized_net_r": self.realized_net_r,
            "reported_at": self.reported_at,
        }


@dataclass
class RunJob:
    """One strategy × contract cell in a batch matrix."""

    job_id: str
    run_id: str
    symbol: str
    strategy_version: str
    status: JobStatus = "queued"
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    result_path: str | None = None
    #: ``strategy_file`` once a confirmed version drove the replay (6-5).
    strategy_source: str | None = None
    #: Non-fatal problems that still must not disappear (e.g. chart sidecar).
    warnings: tuple[str, ...] = ()
    #: P4-A per-cell immutable standard assumptions; absent on legacy validation jobs.
    execution_assumptions: dict[str, Any] | None = None
    progress: JobProgress | None = None
    error_summary: str | None = None
    error_full: str | None = None
    cancelled_at: str | None = None
    #: Submit-time plan truth used before the fresh job-start recheck.
    planned_trading_date_count: int | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "symbol": self.symbol,
            "strategy_version": self.strategy_version,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_path": self.result_path,
            "strategy_source": self.strategy_source,
            "warnings": list(self.warnings),
            "progress": self.progress.to_dict() if self.progress is not None else None,
            "error_summary": self.error_summary,
            "error_full": self.error_full,
            "cancelled_at": self.cancelled_at,
            "assumptions": _public_assumptions(self.execution_assumptions),
        }
        if self.execution_assumptions is not None:
            document["execution_assumptions"] = self.execution_assumptions
        return document


@dataclass
class BatchRecord:
    """Submitted batch with progress."""

    batch_id: str
    status: BatchStatus
    created_at: str
    updated_at: str
    request: dict[str, Any]
    jobs: list[RunJob] = field(default_factory=list)
    source_schema: Literal["batch_job.v1", "batch_job.v2"] = field(
        default="batch_job.v2",
        repr=False,
    )
    worker_eligible: bool = field(default=True, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "batch_job.v2",
            "batch_id": self.batch_id,
            "status": _batch_status(self.jobs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": self.request,
            "jobs": [job.to_dict() for job in self.jobs],
            "summary": _job_summary(self.jobs),
        }


class AdmissionRejected(RuntimeError):
    """A queued standard cell no longer passes its exact job-start recheck."""


@dataclass(frozen=True, slots=True)
class _JobExecutionOutcome:
    result_path: str | None = None
    strategy_source: str | None = None
    warnings: tuple[str, ...] = ()


def _chart_materialization_warning(job_id: str, error: object) -> str:
    """Record full diagnostics in logs while keeping the public warning stable."""
    _LOGGER.warning(
        "chart sidecar materialization failed for job %s: %s",
        job_id,
        error,
    )
    return _PUBLIC_CHART_WARNING


class BatchQueue:
    """Thread-backed sequential executor for validation batches."""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        admission_service: BacktestAdmissionService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._data_root = data_root or (PROJECT_ROOT / "data")
        self._batches_root = self._data_root / "jobs" / "batches"
        self._lock = threading.Lock()
        self._batches: dict[str, BatchRecord] = {}
        self._worker: threading.Thread | None = None
        self._default_admission_service = admission_service
        self._batch_admission_services: dict[str, BacktestAdmissionService] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def submit(self, request: dict[str, Any]) -> BatchRecord:
        """Expand strategy×symbol matrix into jobs and start the worker if idle.

        Every cell is resolved up front so an unconfirmed version, an
        unauthorized contract, or a non-validation override fails at submit time
        with a message the Owner can act on — not halfway through the queue.
        """
        symbols = list(request.get("symbols") or [])
        strategy_versions = list(request.get("strategy_versions") or ["trend-v0"])
        if not symbols:
            msg = "symbols must be non-empty"
            raise ValueError(msg)
        if not strategy_versions:
            msg = "strategy_versions must be non-empty"
            raise ValueError(msg)
        for strategy_version in strategy_versions:
            for symbol in symbols:
                _resolve_for_request(request, strategy_version=strategy_version, symbol=symbol)

        now = self._now_iso()
        stamp = _parse_canonical_utc(now, field_name="batch created_at")
        batch_id = f"batch-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        jobs: list[RunJob] = []
        for strategy_version in strategy_versions:
            for symbol in symbols:
                run_id = (
                    f"{symbol.lower()}-{stamp.strftime('%Y%m%d')}"
                    f"-val-{uuid4().hex[:6]}"
                )
                jobs.append(
                    RunJob(
                        job_id=f"{batch_id}-{symbol}-{strategy_version}-{uuid4().hex[:4]}",
                        run_id=run_id,
                        symbol=str(symbol).upper(),
                        strategy_version=str(strategy_version),
                    )
                )
        record = BatchRecord(
            batch_id=batch_id,
            status="queued",
            created_at=now,
            updated_at=now,
            request=dict(request),
            jobs=jobs,
        )
        self._publish_new_record(record)
        return record

    def submit_standard(
        self,
        request: P4BatchRequest,
        evaluation: AdmissionEvaluation,
        *,
        admission_service: BacktestAdmissionService | None = None,
    ) -> BatchRecord:
        """Atomically publish one already-rechecked P4 standard matrix."""
        if evaluation.document.get("can_submit") is not True:
            msg = "standard batch cannot be published from a blocked admission"
            raise ValueError(msg)
        now = self._now_iso()
        stamp = _parse_canonical_utc(now, field_name="batch created_at")
        batch_id = f"batch-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        jobs: list[RunJob] = []
        for strategy_version in request.strategy_versions:
            for symbol in request.symbols:
                plan = evaluation.plan_for(strategy_version, symbol)
                run_id = (
                    f"{symbol.lower()}-{stamp.strftime('%Y%m%d')}"
                    f"-standard-{uuid4().hex[:6]}"
                )
                jobs.append(
                    RunJob(
                        job_id=(
                            f"{batch_id}-{symbol}-{strategy_version}-{uuid4().hex[:4]}"
                        ),
                        run_id=run_id,
                        symbol=symbol,
                        strategy_version=strategy_version,
                        execution_assumptions=_plan_assumptions(plan),
                        planned_trading_date_count=len(plan.admitted_trading_dates),
                    )
                )
        record = BatchRecord(
            batch_id=batch_id,
            status="queued",
            created_at=now,
            updated_at=now,
            request=cast(dict[str, Any], request.to_storage_dict()),
            jobs=jobs,
        )
        service = admission_service or self._default_admission_service
        with self._lock:
            # Disk publication succeeds before either the queue or worker can see
            # this batch. A failed replace therefore leaves zero visible jobs.
            self._persist_unlocked(record)
            self._batches[batch_id] = record
            if service is not None:
                self._batch_admission_services[batch_id] = service
            self._ensure_worker_unlocked()
        return record

    def _publish_new_record(self, record: BatchRecord) -> None:
        """Persist before exposing a newly-submitted legacy or standard batch."""
        with self._lock:
            try:
                self._persist_unlocked(record)
            except BatchPersistenceError:
                raise
            except (
                OSError,
                UnicodeError,
                KeyError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                raise BatchPersistenceError(
                    "new batch operational state could not be persisted atomically"
                ) from exc
            self._batches[record.batch_id] = record
            self._ensure_worker_unlocked()

    def get(self, batch_id: str) -> BatchRecord:
        with self._lock:
            if batch_id in self._batches:
                return self._batches[batch_id]
            record = self._load_record_unlocked(batch_id)
            self._batches[batch_id] = record
            return record

    def list_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            live = [b.to_dict() for b in self._batches.values()]
            if self._batches_root.is_dir():
                live_ids = {str(item["batch_id"]) for item in live}
                for path in sorted(self._batches_root.glob("*.json"), reverse=True):
                    if path.stem in live_ids:
                        continue
                    record = self._read_record(path, expected_batch_id=path.stem)
                    live.append(record.to_dict())
        live.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return live

    def cancel_queued(self, batch_id: str) -> BatchRecord:
        """Atomically cancel only jobs that have not been claimed by the worker."""
        with self._lock:
            record = self._batches.get(batch_id)
            if record is None:
                record = self._load_record_unlocked(batch_id)
            queued = [job for job in record.jobs if job.status == "queued"]
            if not queued:
                self._batches.setdefault(batch_id, record)
                return record
            if record.source_schema == "batch_job.v1":
                raise BatchOperationalStateError(
                    "legacy batch_job.v1 operational state is read-only"
                )
            self._batches.setdefault(batch_id, record)
            timestamp = self._now_iso()

            def cancel(candidate: BatchRecord) -> None:
                for job in candidate.jobs:
                    if job.status == "queued":
                        job.status = "cancelled"
                        job.finished_at = timestamp
                        job.cancelled_at = timestamp
                        job.progress = None
                        job.error_summary = None
                        job.error_full = None
                candidate.updated_at = timestamp
                candidate.status = _batch_status(candidate.jobs)

            return self._publish_transition_unlocked(record, cancel)

    def _load_record_unlocked(self, batch_id: str) -> BatchRecord:
        path = self._batches_root / f"{batch_id}.json"
        if not path.is_file():
            msg = f"unknown batch_id: {batch_id}"
            raise LookupError(msg)
        return self._read_record(path, expected_batch_id=batch_id)

    @staticmethod
    def _read_record(path: Path, *, expected_batch_id: str) -> BatchRecord:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_object_pairs,
                parse_constant=_reject_non_finite_json_constant,
            )
            if not isinstance(payload, dict):
                raise TypeError("batch operational document must be an object")
            return _record_from_dict(payload, expected_batch_id=expected_batch_id)
        except BatchOperationalStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BatchOperationalStateError(_BATCH_STATE_UNAVAILABLE_DETAIL) from exc

    def _ensure_worker_unlocked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_loop, name="batch-queue", daemon=True)
        self._worker.start()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                claimed = self._claim_next_job_unlocked()
                if claimed is None:
                    self._worker = None
                    return
                batch, job = claimed

            try:
                outcome = self._execute_job(batch, job)
            except AdmissionRejected as exc:
                self._finalize_job(
                    batch.batch_id,
                    job.job_id,
                    status="failed",
                    message=str(exc),
                    error_summary=str(exc),
                    error_full=_sanitized_exception_chain(exc),
                )
            except Exception as exc:  # noqa: BLE001 — log privately and keep queue alive
                _LOGGER.exception(
                    "unexpected batch worker failure for batch %s job %s",
                    batch.batch_id,
                    job.job_id,
                )
                self._finalize_job(
                    batch.batch_id,
                    job.job_id,
                    status="failed",
                    message=_PUBLIC_WORKER_FAILURE_MESSAGE,
                    error_summary="worker_failed:internal_error",
                    error_full=_sanitized_exception_chain(exc),
                )
            else:
                self._finalize_job(
                    batch.batch_id,
                    job.job_id,
                    status="completed",
                    message="ok",
                    outcome=outcome,
                )

    def _claim_next_job_unlocked(self) -> tuple[BatchRecord, RunJob] | None:
        for record in self._batches.values():
            if not record.worker_eligible:
                continue
            queued = next((job for job in record.jobs if job.status == "queued"), None)
            if queued is None:
                continue
            queued_job_id = queued.job_id
            timestamp = self._now_iso()

            def claim(
                candidate: BatchRecord,
                *,
                job_id: str = queued_job_id,
                claim_at: str = timestamp,
            ) -> None:
                job = _job_by_id(candidate, job_id)
                job.status = "running"
                job.started_at = claim_at
                job.finished_at = None
                job.cancelled_at = None
                job.error_summary = None
                job.error_full = None
                if job.planned_trading_date_count is not None:
                    job.progress = JobProgress.baseline(
                        total=job.planned_trading_date_count,
                        reported_at=claim_at,
                    )
                candidate.updated_at = claim_at
                candidate.status = _batch_status(candidate.jobs)

            claimed = self._publish_transition_unlocked(record, claim)
            return claimed, _job_by_id(claimed, queued.job_id)
        return None

    def _finalize_job(
        self,
        batch_id: str,
        job_id: str,
        *,
        status: Literal["completed", "failed"],
        message: str,
        outcome: _JobExecutionOutcome | None = None,
        error_summary: str | None = None,
        error_full: str | None = None,
    ) -> BatchRecord:
        with self._lock:
            record = self._batches[batch_id]
            timestamp = self._now_iso()

            def finalize(candidate: BatchRecord) -> None:
                job = _job_by_id(candidate, job_id)
                if job.status != "running":
                    msg = "only a running job can be finalized"
                    raise BatchOperationalStateError(msg)
                job.status = status
                job.message = message
                job.finished_at = timestamp
                if status == "completed":
                    if (
                        job.progress is None
                        or job.progress.processed_trading_date_count
                        != job.progress.total_trading_date_count
                    ):
                        msg = "completed job is missing exact final day progress"
                        raise BatchOperationalStateError(msg)
                    execution = outcome or _JobExecutionOutcome()
                    job.result_path = execution.result_path
                    job.strategy_source = execution.strategy_source
                    job.warnings = (*job.warnings, *execution.warnings)
                    job.error_summary = None
                    job.error_full = None
                else:
                    job.error_summary = error_summary
                    job.error_full = error_full
                candidate.updated_at = timestamp
                candidate.status = _batch_status(candidate.jobs)

            finalized = self._publish_transition_unlocked(record, finalize)
            # Fan-out Web Push when the batch itself becomes terminal.
            try:
                terminal = {"completed", "failed", "cancelled", "partial"}
                if finalized.status in terminal:
                    summary = _job_summary(finalized.jobs)
                    from futures_research.api.push_fanout import (
                        fanout_backtest_complete,
                    )

                    fanout_backtest_complete(
                        batch_id=finalized.batch_id,
                        status=str(finalized.status),
                        completed=summary.get("completed"),
                        failed=summary.get("failed"),
                        total=summary.get("total"),
                    )
            except Exception:  # noqa: BLE001 — never break the worker
                pass
            return finalized

    def _refresh_running_baseline(
        self,
        batch_id: str,
        job_id: str,
        *,
        total_trading_date_count: int,
    ) -> None:
        with self._lock:
            record = self._batches.get(batch_id)
            if record is None:
                msg = "running batch disappeared before its fresh plan recheck"
                raise BatchOperationalStateError(msg)
            current = _job_by_id(record, job_id)
            if current.status != "running":
                msg = "fresh plan baseline can only update a running job"
                raise BatchOperationalStateError(msg)
            timestamp = self._now_iso()

            def refresh(candidate: BatchRecord) -> None:
                job = _job_by_id(candidate, job_id)
                job.planned_trading_date_count = total_trading_date_count
                job.progress = JobProgress.baseline(
                    total=total_trading_date_count,
                    reported_at=timestamp,
                )
                candidate.updated_at = timestamp
                candidate.status = _batch_status(candidate.jobs)

            self._publish_transition_unlocked(record, refresh)

    def _record_day_progress(
        self,
        batch_id: str,
        job_id: str,
        snapshot: DayProgressSnapshot,
    ) -> None:
        progress = JobProgress.from_day_snapshot(snapshot)
        with self._lock:
            record = self._batches[batch_id]
            current = _job_by_id(record, job_id)
            if current.status != "running":
                msg = "day progress can only update a running job"
                raise BatchOperationalStateError(msg)
            previous = current.progress
            if previous is None:
                if progress.processed_trading_date_count != 1:
                    msg = "first persisted day progress must have processed count one"
                    raise BatchOperationalStateError(msg)
            elif (
                progress.total_trading_date_count
                != previous.total_trading_date_count
                or progress.processed_trading_date_count
                != previous.processed_trading_date_count + 1
            ):
                msg = "day progress is not monotonic against persisted truth"
                raise BatchOperationalStateError(msg)
            if previous is not None and (
                _parse_canonical_utc(
                    progress.reported_at,
                    field_name="progress reported_at",
                )
                < _parse_canonical_utc(
                    previous.reported_at,
                    field_name="previous progress reported_at",
                )
                or progress.trade_count < previous.trade_count
                or (
                    previous.current_trading_date is not None
                    and progress.current_trading_date is not None
                    and _parse_trading_date(progress.current_trading_date)
                    <= _parse_trading_date(previous.current_trading_date)
                )
            ):
                msg = "day progress facts regressed against persisted truth"
                raise BatchOperationalStateError(msg)

            def update(candidate: BatchRecord) -> None:
                job = _job_by_id(candidate, job_id)
                job.progress = progress
                candidate.updated_at = progress.reported_at
                candidate.status = _batch_status(candidate.jobs)

            self._publish_transition_unlocked(record, update)

    def _publish_transition_unlocked(
        self,
        record: BatchRecord,
        mutate: Callable[[BatchRecord], None],
    ) -> BatchRecord:
        candidate = deepcopy(record)
        mutate(candidate)
        try:
            self._persist_unlocked(candidate)
        except BatchPersistenceError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise BatchPersistenceError(
                "batch operational transition could not be persisted atomically"
            ) from exc
        self._batches[record.batch_id] = candidate
        return candidate

    def _execute_job(
        self,
        batch: BatchRecord,
        job: RunJob,
    ) -> _JobExecutionOutcome | None:
        request = batch.request
        if "execution_assumptions" in request:
            return self._execute_standard_job(batch, job)

        registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
        contract = registry.by_symbol(job.symbol)
        range_start = _parse_iso(str(request["range_start"]))
        range_end = _parse_iso(str(request["range_end"]))
        validation_run = bool(request.get("validation_run", True))
        capital = float(request.get("initial_capital") or 100_000.0)
        quantity = int(request.get("quantity") or 1)
        skip_nautilus = bool(request.get("skip_nautilus_replay", True))
        resolved = _resolve_for_request(
            request,
            strategy_version=job.strategy_version,
            symbol=job.symbol,
        )

        results_root = self._data_root / "backtests" / "results"
        runs_db = self._data_root / "backtests" / "runs.sqlite3"
        # quality_gate disabled for UI validation batches (holiday templates etc.)
        runner = BacktestRunner(
            canonical_store=CanonicalStore(self._data_root / "market"),
            daily_canonical_store=CanonicalStore(self._data_root / "market-daily"),
            run_store=SqliteRunStore(runs_db),
            result_exporter=ResultExporter(results_root),
            quality_reports_root=None,
        )
        artifacts = runner.run(
            contract=contract,
            config=BacktestRunConfig(
                run_id=job.run_id,
                strategy_version=job.strategy_version,
                session_name=resolved.session_name,
                range_start=range_start,
                range_end=range_end,
                initial_capital=capital,
                quantity=quantity,
                verify_nautilus_replay=not skip_nautilus,
                validation_run=validation_run,
                regime_separation_percentile=resolved.regime_separation_percentile,
                regime_slope_percentile=resolved.regime_slope_percentile,
                pullback_ema_period=resolved.pullback_ema_period,
                strategy_spec=resolved.spec,
                strategy_binding=resolved.binding,
            ),
            day_observer=(
                lambda snapshot: self._record_day_progress(
                    batch.batch_id,
                    job.job_id,
                    snapshot,
                )
            ),
            progress_clock=self._clock,
        )
        # Scorecard enrich (optional, best-effort)
        try:
            from futures_research.backtest.scorecard import enrich_result_file

            enrich_result_file(artifacts.exported.result_path, write=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        warnings: tuple[str, ...] = ()
        # Advisory 1 (6-5): a soft chart-cache failure must reach the operator.
        if artifacts.chart_materialization_error is not None:
            warnings = (
                _chart_materialization_warning(
                    job.job_id,
                    artifacts.chart_materialization_error,
                ),
            )
        return _JobExecutionOutcome(
            result_path=str(artifacts.exported.result_path),
            strategy_source=resolved.binding.source,
            warnings=warnings,
        )

    def _execute_standard_job(
        self,
        batch: BatchRecord,
        job: RunJob,
    ) -> _JobExecutionOutcome:
        """Recheck one exact queued cell, then let the runner consume only its plan."""
        request = P4BatchRequest.model_validate(batch.request)
        service = (
            self._batch_admission_services.get(batch.batch_id)
            or self._default_admission_service
            or default_backtest_admission_service()
        )
        evaluation = service.evaluate(
            request,
            only_cells=((job.strategy_version, job.symbol),),
        )
        unit = cast(list[dict[str, Any]], evaluation.document["units"])[0]
        if unit["status"] not in {"pass", "warn"}:
            reasons = ",".join(cast(list[str], unit["reason_codes"]))
            raise AdmissionRejected(f"admission_rejected:{reasons or unit['status']}")
        plan = evaluation.plan_for(job.strategy_version, job.symbol)
        if job.execution_assumptions != _plan_assumptions(plan):
            raise AdmissionRejected("admission_rejected:execution_assumptions_drift")
        self._refresh_running_baseline(
            batch.batch_id,
            job.job_id,
            total_trading_date_count=len(plan.admitted_trading_dates),
        )

        results_root = self._data_root / "backtests" / "results"
        runs_db = self._data_root / "backtests" / "runs.sqlite3"
        runner = BacktestRunner(
            canonical_store=CanonicalStore(self._data_root / "market"),
            daily_canonical_store=CanonicalStore(self._data_root / "market-daily"),
            run_store=SqliteRunStore(runs_db),
            result_exporter=ResultExporter(results_root),
            quality_reports_root=None,
        )
        resolved = plan.resolved_strategy
        artifacts = runner.run(
            contract=plan.contract,
            config=BacktestRunConfig(
                run_id=job.run_id,
                strategy_version=job.strategy_version,
                session_name=resolved.session_name,
                range_start=plan.range_start,
                range_end=plan.range_end,
                initial_capital=plan.initial_capital_usd,
                quantity=plan.quantity,
                costs=plan.costs,
                admitted_trading_dates=plan.admitted_trading_dates,
                excluded_trading_dates=plan.excluded_trading_dates,
                calibration_excluded_trading_dates=(
                    plan.calibration_excluded_trading_dates
                ),
                verify_nautilus_replay=True,
                validation_run=False,
                regime_separation_percentile=resolved.regime_separation_percentile,
                regime_slope_percentile=resolved.regime_slope_percentile,
                pullback_ema_period=resolved.pullback_ema_period,
                strategy_spec=resolved.spec,
                strategy_binding=resolved.binding,
            ),
            day_observer=(
                lambda snapshot: self._record_day_progress(
                    batch.batch_id,
                    job.job_id,
                    snapshot,
                )
            ),
            progress_clock=self._clock,
        )
        try:
            from futures_research.backtest.scorecard import enrich_result_file

            enrich_result_file(artifacts.exported.result_path, write=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        warnings: tuple[str, ...] = ()
        if artifacts.chart_materialization_error is not None:
            warnings = (
                _chart_materialization_warning(
                    job.job_id,
                    artifacts.chart_materialization_error,
                ),
            )
        return _JobExecutionOutcome(
            result_path=str(artifacts.exported.result_path),
            strategy_source=resolved.binding.source,
            warnings=warnings,
        )

    def _now_iso(self) -> str:
        return _canonical_utc_text(self._clock(), field_name="batch clock")

    def _persist_unlocked(self, record: BatchRecord) -> None:
        path = self._batches_root / f"{record.batch_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        document = record.to_dict()
        if record.source_schema != "batch_job.v2":
            msg = "legacy batch_job.v1 operational state is read-only"
            raise BatchOperationalStateError(msg)
        if record.status != _batch_status(record.jobs):
            msg = "in-memory batch aggregate diverged before persistence"
            raise BatchOperationalStateError(msg)
        _record_from_dict(
            deepcopy(document),
            expected_batch_id=record.batch_id,
        )
        try:
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)


_GLOBAL_QUEUE: BatchQueue | None = None
_GLOBAL_LOCK = threading.Lock()


def get_batch_queue() -> BatchQueue:
    global _GLOBAL_QUEUE
    with _GLOBAL_LOCK:
        if _GLOBAL_QUEUE is None:
            _GLOBAL_QUEUE = BatchQueue()
        return _GLOBAL_QUEUE


def _resolve_for_request(
    request: dict[str, Any],
    *,
    strategy_version: str,
    symbol: str,
) -> ResolvedStrategy:
    """Resolve one matrix cell from a submit payload.

    ``None`` on a knob means "use the strategy document's value"; a value means
    the Owner is explicitly requesting an override, which only validation runs
    may apply (channel [083] Q1).
    """
    try:
        return resolve_strategy(
            strategy_version=str(strategy_version),
            symbol=str(symbol).upper(),
            validation_run=bool(request.get("validation_run", True)),
            requested_session=(
                str(request["session_name"]) if request.get("session_name") else None
            ),
            override_separation_percentile=_optional_float(
                request.get("regime_separation_percentile")
            ),
            override_slope_percentile=_optional_float(request.get("regime_slope_percentile")),
            override_pullback_ema_period=_optional_int(request.get("pullback_ema_period")),
        )
    except StrategyResolutionError as exc:
        raise ValueError(str(exc)) from exc


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _strict_string(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        msg = f"{field_name} must be a string"
        raise TypeError(msg)
    if not allow_empty and (not value or value.strip() != value):
        msg = f"{field_name} must be a non-empty canonical string"
        raise ValueError(msg)
    return value


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            msg = f"duplicate JSON key: {key}"
            raise ValueError(msg)
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> None:
    msg = f"non-finite JSON constant is forbidden: {value}"
    raise ValueError(msg)


def _strict_nullable_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_string(value, field_name=field_name)


def _strict_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{field_name} must be an integer"
        raise TypeError(msg)
    return cast(int, value)


def _strict_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{field_name} must be a number"
        raise TypeError(msg)
    number = float(value)
    if not isfinite(number):
        msg = f"{field_name} must be finite"
        raise ValueError(msg)
    return number


def _parse_canonical_utc(value: str, *, field_name: str) -> datetime:
    if not value.endswith("Z") or value.count("Z") != 1:
        msg = f"{field_name} must be canonical UTC"
        raise ValueError(msg)
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        msg = f"{field_name} must be canonical UTC"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        msg = f"{field_name} must be canonical UTC"
        raise ValueError(msg)
    if parsed.isoformat().replace("+00:00", "Z") != value:
        msg = f"{field_name} must be canonical UTC"
        raise ValueError(msg)
    return parsed


def _canonical_utc_text(value: datetime, *, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        msg = f"{field_name} must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_trading_date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        msg = "current_trading_date must be an ASCII ISO date"
        raise ValueError(msg)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        msg = "current_trading_date must be a real calendar date"
        raise ValueError(msg) from exc
    if parsed.isoformat() != value:
        msg = "current_trading_date must be canonical"
        raise ValueError(msg)
    return parsed


def _job_summary(jobs: list[RunJob]) -> dict[str, int]:
    summary = {
        "total": len(jobs),
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for job in jobs:
        summary[job.status] += 1
    status_keys = ("queued", "running", "completed", "failed", "cancelled")
    if sum(summary[key] for key in status_keys) != summary["total"]:
        msg = "batch summary does not conserve every job"
        raise BatchOperationalStateError(msg)
    return summary


def _batch_status(jobs: list[RunJob]) -> BatchStatus:
    if not jobs:
        msg = "batch must contain at least one job"
        raise BatchOperationalStateError(msg)
    statuses = {job.status for job in jobs}
    if statuses == {"queued"}:
        return "queued"
    if "queued" in statuses or "running" in statuses:
        return "running"
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"failed"}:
        return "failed"
    if statuses == {"cancelled"}:
        return "cancelled"
    return "partial"


def _job_by_id(record: BatchRecord, job_id: str) -> RunJob:
    matches = [job for job in record.jobs if job.job_id == job_id]
    if len(matches) != 1:
        msg = "batch job identity is missing or duplicated"
        raise BatchOperationalStateError(msg)
    return matches[0]


def _public_assumptions(source: dict[str, Any] | None) -> dict[str, Any] | None:
    if source is None:
        return None
    expected = {
        "initial_capital_usd",
        "commission_per_side",
        "slippage_ticks",
        "target_requires_through",
        "fill_model",
        "simulation_precision",
        "quantity",
    }
    if set(source) != expected:
        msg = "internal execution assumptions do not match the approved snapshot"
        raise BatchOperationalStateError(msg)
    slippage = source["slippage_ticks"]
    if not isinstance(slippage, dict) or set(slippage) != {
        "breakout_entry",
        "stop_exit",
        "target_exit",
        "day_end_exit",
    }:
        msg = "internal slippage assumptions are malformed"
        raise BatchOperationalStateError(msg)
    target_requires_through = source["target_requires_through"]
    if not isinstance(target_requires_through, bool):
        msg = "target_requires_through must be boolean"
        raise BatchOperationalStateError(msg)
    if source["fill_model"] != "conservative":
        msg = "fill_model is not the approved conservative model"
        raise BatchOperationalStateError(msg)
    if source["simulation_precision"] != "one_minute":
        msg = "simulation_precision is not the approved one-minute model"
        raise BatchOperationalStateError(msg)
    quantity = _strict_int(source["quantity"], field_name="quantity")
    if quantity <= 0:
        msg = "quantity must be positive"
        raise BatchOperationalStateError(msg)
    capital = _strict_number(
        source["initial_capital_usd"],
        field_name="initial_capital_usd",
    )
    commission = _strict_number(
        source["commission_per_side"],
        field_name="commission_per_side",
    )
    if capital <= 0 or commission < 0:
        msg = "capital must be positive and commission must not be negative"
        raise BatchOperationalStateError(msg)
    slippage_public = {
        key: _strict_int(slippage[key], field_name=f"slippage_ticks.{key}")
        for key in (
            "breakout_entry",
            "stop_exit",
            "target_exit",
            "day_end_exit",
        )
    }
    if any(value < 0 for value in slippage_public.values()):
        msg = "slippage tick assumptions must not be negative"
        raise BatchOperationalStateError(msg)
    return {
        "initial_capital_usd": capital,
        "commission_per_side": commission,
        "slippage_ticks": slippage_public,
        "target_requires_through": target_requires_through,
        "fill_model": "conservative",
        "bar_precision": "1m",
    }


def _try_public_assumptions(
    source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        return _public_assumptions(source)
    except (BatchOperationalStateError, TypeError, ValueError):
        return None


def _sanitize_error_text(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"Traceback\s*\([^)]*\):?", "", text, flags=re.IGNORECASE)
    text = _HTML_PATTERN.sub("", text)
    text = _WINDOWS_PATH_PATTERN.sub("[path]", text)
    text = _POSIX_PATH_PATTERN.sub("[path]", text)
    text = _SOURCE_LINE_PATTERN.sub("", text)
    text = re.sub(r"(?i)\b(?:ui|frontend)\s*:\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" :;-")
    return text


def _sanitized_exception_chain(error: BaseException) -> str:
    ordered: list[BaseException] = []
    seen: set[int] = set()

    def visit(current: BaseException | None) -> None:
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        ordered.append(current)
        visit(current.__cause__)
        if current.__context__ is not current.__cause__:
            visit(current.__context__)

    visit(error)
    parts: list[str] = []
    for current in ordered:
        kind = type(current).__name__
        message = _sanitize_error_text(str(current))
        parts.append(f"{kind}: {message}" if message else kind)
    return "\nCaused by: ".join(parts)


def _record_from_dict(
    payload: dict[str, Any],
    *,
    expected_batch_id: str,
) -> BatchRecord:
    schema = _strict_string(payload.get("schema"), field_name="schema")
    if schema not in {"batch_job.v1", "batch_job.v2"}:
        msg = "unknown batch operational schema"
        raise BatchOperationalStateError(msg)
    expected_top = {
        "schema",
        "batch_id",
        "status",
        "created_at",
        "updated_at",
        "request",
        "jobs",
        "summary",
    }
    if set(payload) != expected_top:
        msg = "batch operational fields do not match the declared schema"
        raise BatchOperationalStateError(msg)
    batch_id = _strict_string(payload["batch_id"], field_name="batch_id")
    if batch_id != expected_batch_id:
        msg = "persisted batch identity does not match its filename"
        raise BatchOperationalStateError(msg)
    created_at = _strict_string(payload["created_at"], field_name="created_at")
    updated_at = _strict_string(payload["updated_at"], field_name="updated_at")
    created = _parse_canonical_utc(created_at, field_name="created_at")
    updated = _parse_canonical_utc(updated_at, field_name="updated_at")
    if updated < created:
        msg = "batch updated_at precedes created_at"
        raise BatchOperationalStateError(msg)
    request = payload["request"]
    if not isinstance(request, dict):
        msg = "batch request must be an object"
        raise TypeError(msg)
    jobs_raw = payload["jobs"]
    if not isinstance(jobs_raw, list) or not jobs_raw:
        msg = "batch jobs must be a non-empty list"
        raise BatchOperationalStateError(msg)
    jobs = [
        _job_from_dict(item, schema=schema)
        for item in jobs_raw
    ]
    if len({job.job_id for job in jobs}) != len(jobs):
        msg = "batch job_id values must be unique"
        raise BatchOperationalStateError(msg)
    if len({job.run_id for job in jobs}) != len(jobs):
        msg = "batch run_id values must be unique"
        raise BatchOperationalStateError(msg)
    _validate_job_timelines(
        jobs,
        created_at=created,
        updated_at=updated,
    )

    calculated_status = _batch_status(jobs)
    stored_status = _strict_string(payload["status"], field_name="status")
    allowed_statuses = (
        {"queued", "running", "completed", "failed", "partial"}
        if schema == "batch_job.v1"
        else {"queued", "running", "completed", "failed", "cancelled", "partial"}
    )
    if stored_status not in allowed_statuses or stored_status != calculated_status:
        msg = "batch status does not match strict aggregate truth"
        raise BatchOperationalStateError(msg)

    summary = payload["summary"]
    if not isinstance(summary, dict):
        msg = "batch summary must be an object"
        raise TypeError(msg)
    calculated_summary = _job_summary(jobs)
    expected_summary_keys = (
        {"total", "queued", "running", "completed", "failed"}
        if schema == "batch_job.v1"
        else set(calculated_summary)
    )
    if set(summary) != expected_summary_keys:
        msg = "batch summary fields do not match the declared schema"
        raise BatchOperationalStateError(msg)
    for key in expected_summary_keys:
        if _strict_int(summary[key], field_name=f"summary.{key}") != calculated_summary[key]:
            msg = "batch summary does not match job truth"
            raise BatchOperationalStateError(msg)

    return BatchRecord(
        batch_id=batch_id,
        status=calculated_status,
        created_at=created_at,
        updated_at=updated_at,
        request=cast(dict[str, Any], deepcopy(request)),
        jobs=jobs,
        source_schema=cast(Literal["batch_job.v1", "batch_job.v2"], schema),
        worker_eligible=False,
    )


def _validate_job_timelines(
    jobs: list[RunJob],
    *,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    for job in jobs:
        started = (
            _parse_canonical_utc(job.started_at, field_name="job.started_at")
            if job.started_at is not None
            else None
        )
        finished = (
            _parse_canonical_utc(job.finished_at, field_name="job.finished_at")
            if job.finished_at is not None
            else None
        )
        reported = (
            _parse_canonical_utc(
                job.progress.reported_at,
                field_name="progress.reported_at",
            )
            if job.progress is not None
            else None
        )
        if started is not None and not created_at <= started <= updated_at:
            msg = "job started_at falls outside the batch operational timeline"
            raise BatchOperationalStateError(msg)
        if finished is not None and not created_at <= finished <= updated_at:
            msg = "job finished_at falls outside the batch operational timeline"
            raise BatchOperationalStateError(msg)
        if started is not None and finished is not None and finished < started:
            msg = "job finished_at precedes started_at"
            raise BatchOperationalStateError(msg)
        if reported is not None and not created_at <= reported <= updated_at:
            msg = "job progress falls outside the batch operational timeline"
            raise BatchOperationalStateError(msg)
        if started is not None and reported is not None and reported < started:
            msg = "job progress precedes its running claim"
            raise BatchOperationalStateError(msg)


def _job_from_dict(
    payload: Any,
    *,
    schema: str,
) -> RunJob:
    if not isinstance(payload, dict):
        msg = "batch job must be an object"
        raise TypeError(msg)
    v1_required = {
        "job_id",
        "run_id",
        "symbol",
        "strategy_version",
        "status",
        "message",
        "started_at",
        "finished_at",
        "result_path",
    }
    v1_optional = {"strategy_source", "warnings", "execution_assumptions"}
    v2_required = v1_required | {
        "strategy_source",
        "warnings",
        "progress",
        "error_summary",
        "error_full",
        "cancelled_at",
        "assumptions",
    }
    allowed = (v1_required | v1_optional) if schema == "batch_job.v1" else (
        v2_required | {"execution_assumptions"}
    )
    required = v1_required if schema == "batch_job.v1" else v2_required
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        msg = "batch job fields do not match the declared schema"
        raise BatchOperationalStateError(msg)

    status_text = _strict_string(payload["status"], field_name="job.status")
    allowed_jobs = (
        {"queued", "running", "completed", "failed"}
        if schema == "batch_job.v1"
        else {"queued", "running", "completed", "failed", "cancelled"}
    )
    if status_text not in allowed_jobs:
        msg = "unknown batch job status"
        raise BatchOperationalStateError(msg)
    status = cast(JobStatus, status_text)
    started_at = _nullable_timestamp(payload["started_at"], field_name="job.started_at")
    finished_at = _nullable_timestamp(payload["finished_at"], field_name="job.finished_at")
    execution_raw = payload.get("execution_assumptions")
    if (
        schema == "batch_job.v2"
        and execution_raw is not None
        and not isinstance(execution_raw, dict)
    ):
        msg = "execution_assumptions must be an object or null"
        raise TypeError(msg)
    execution = (
        cast(dict[str, Any], deepcopy(execution_raw))
        if isinstance(execution_raw, dict)
        else None
    )
    if schema == "batch_job.v1" and _try_public_assumptions(execution) is None:
        execution = None
    if schema == "batch_job.v2" and execution is not None:
        expected_public = _public_assumptions(execution)
        if payload["assumptions"] != expected_public:
            msg = "public assumptions do not match the immutable internal snapshot"
            raise BatchOperationalStateError(msg)
    elif schema == "batch_job.v2":
        if payload["assumptions"] is not None:
            msg = "unproven public assumptions must be null"
            raise BatchOperationalStateError(msg)

    warnings_raw = payload.get("warnings", [])
    if not isinstance(warnings_raw, list):
        msg = "job warnings must be a list"
        raise TypeError(msg)
    warnings = tuple(
        _strict_string(value, field_name="job warning")
        for value in warnings_raw
    )
    progress: JobProgress | None = None
    error_summary: str | None = None
    error_full: str | None = None
    cancelled_at: str | None = None
    if schema == "batch_job.v2":
        progress_raw = payload["progress"]
        if progress_raw is not None:
            if not isinstance(progress_raw, dict):
                msg = "job progress must be an object or null"
                raise TypeError(msg)
            progress = JobProgress.from_dict(progress_raw)
        error_summary = _strict_nullable_string(
            payload["error_summary"],
            field_name="job.error_summary",
        )
        error_full = _strict_nullable_string(
            payload["error_full"],
            field_name="job.error_full",
        )
        cancelled_at = _nullable_timestamp(
            payload["cancelled_at"],
            field_name="job.cancelled_at",
        )

    _validate_job_state(
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        progress=progress,
        error_summary=error_summary,
        error_full=error_full,
        cancelled_at=cancelled_at,
        schema=schema,
    )
    return RunJob(
        job_id=_strict_string(payload["job_id"], field_name="job_id"),
        run_id=_strict_string(payload["run_id"], field_name="run_id"),
        symbol=_strict_string(payload["symbol"], field_name="symbol"),
        strategy_version=_strict_string(
            payload["strategy_version"],
            field_name="strategy_version",
        ),
        status=status,
        message=_strict_string(
            payload["message"],
            field_name="job.message",
            allow_empty=True,
        ),
        started_at=started_at,
        finished_at=finished_at,
        result_path=_strict_nullable_string(
            payload["result_path"],
            field_name="job.result_path",
        ),
        strategy_source=_strict_nullable_string(
            payload.get("strategy_source"),
            field_name="job.strategy_source",
        ),
        warnings=warnings,
        execution_assumptions=execution,
        progress=progress,
        error_summary=error_summary,
        error_full=error_full,
        cancelled_at=cancelled_at,
    )


def _nullable_timestamp(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    timestamp = _strict_string(value, field_name=field_name)
    _parse_canonical_utc(timestamp, field_name=field_name)
    return timestamp


def _validate_job_state(
    *,
    status: JobStatus,
    started_at: str | None,
    finished_at: str | None,
    progress: JobProgress | None,
    error_summary: str | None,
    error_full: str | None,
    cancelled_at: str | None,
    schema: str,
) -> None:
    if status == "queued":
        if any(
            value is not None
            for value in (
                started_at,
                finished_at,
                progress,
                error_summary,
                error_full,
                cancelled_at,
            )
        ):
            msg = "queued job contains facts from a later state"
            raise BatchOperationalStateError(msg)
        return
    if status == "running":
        if started_at is None or any(
            value is not None
            for value in (finished_at, error_summary, error_full, cancelled_at)
        ):
            msg = "running job timestamps or errors are inconsistent"
            raise BatchOperationalStateError(msg)
        return
    if status == "cancelled":
        if (
            started_at is not None
            or finished_at is None
            or cancelled_at is None
            or finished_at != cancelled_at
            or progress is not None
            or error_summary is not None
            or error_full is not None
        ):
            msg = "cancelled job facts are inconsistent"
            raise BatchOperationalStateError(msg)
        return
    if started_at is None or finished_at is None or cancelled_at is not None:
        msg = "terminal job timestamps are inconsistent"
        raise BatchOperationalStateError(msg)
    if status == "failed":
        if schema == "batch_job.v2" and (error_summary is None or error_full is None):
            msg = "failed v2 job must retain both error fields"
            raise BatchOperationalStateError(msg)
    else:
        if error_summary is not None or error_full is not None:
            msg = "completed job must not contain failure facts"
            raise BatchOperationalStateError(msg)
        if (
            schema == "batch_job.v2"
            and (
                progress is None
                or progress.processed_trading_date_count
                != progress.total_trading_date_count
            )
        ):
            msg = "completed v2 job must retain exact final progress"
            raise BatchOperationalStateError(msg)


def _plan_assumptions(plan: RunAdmissionPlan) -> dict[str, Any]:
    return {
        "initial_capital_usd": plan.initial_capital_usd,
        "commission_per_side": plan.costs.commission_per_side,
        "slippage_ticks": plan.costs.slippage_ticks.model_dump(),
        "target_requires_through": plan.costs.target_requires_through,
        "fill_model": "conservative",
        "simulation_precision": "one_minute",
        "quantity": plan.quantity,
    }
