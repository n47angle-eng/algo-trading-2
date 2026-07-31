"""P4-A HTTP, submit atomicity, and job-start revalidation proofs."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from test_p4_admission import (
    RUN_DATE,
    _build_fixture,
    _matching_catalog,
    _request,
    _service,
)

from futures_research.api import batch_queue as batch_queue_mod
from futures_research.api import routes_batch as routes_batch_mod
from futures_research.api.backtest_admission import (
    BacktestAdmissionService,
    P4BatchRequest,
)
from futures_research.api.batch_queue import (
    AdmissionRejected,
    BatchQueue,
    BatchRecord,
    RunJob,
)
from futures_research.api.main import app
from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.run_reference_catalog import (
    RunReferenceCatalog,
    RunReferenceMigrationRequired,
)
from futures_research.backtest.runner import (
    BacktestRunConfig,
    BacktestRunner,
    DayProgressSnapshot,
)
from futures_research.data.storage import CanonicalStore


class PausedBatchQueue(BatchQueue):
    """Expose submitted records without racing the test against its worker."""

    def _ensure_worker_unlocked(self) -> None:
        return


def _completed_day_snapshot(
    *,
    trading_date: date = RUN_DATE,
    processed: int = 1,
    total: int = 1,
) -> DayProgressSnapshot:
    return DayProgressSnapshot(
        current_trading_date=trading_date,
        processed_trading_date_count=processed,
        total_trading_date_count=total,
        trade_count=0,
        realized_net_pnl_usd=0.0,
        realized_net_r=0.0,
        reported_at=datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )


def _tree_inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: BacktestAdmissionService,
    queue: BatchQueue,
) -> None:
    monkeypatch.setattr(
        app.state,
        "backtest_admission_service",
        service,
        raising=False,
    )
    monkeypatch.setattr(batch_queue_mod, "_GLOBAL_QUEUE", queue)


def _forbid_admission_failure_writers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    queue: BatchQueue,
) -> dict[str, int]:
    calls: dict[str, int] = {}

    def forbidden(name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            calls[name] = calls.get(name, 0) + 1
            raise AssertionError(f"{name} writer must not run before admission succeeds")

        return fail

    monkeypatch.setattr(queue, "_persist_unlocked", forbidden("batch"))
    monkeypatch.setattr(CanonicalStore, "append", forbidden("canonical"))
    monkeypatch.setattr(SqliteRunStore, "persist", forbidden("run"))
    monkeypatch.setattr(ResultExporter, "export", forbidden("result"))
    monkeypatch.setattr(
        BacktestRunner,
        "_materialize_chart_sidecars",
        forbidden("sidecar"),
    )
    return calls


@pytest.mark.asyncio
async def test_precheck_api_returns_exact_document_and_is_byte_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    before = _tree_inventory(fixture.data_root)

    def forbidden_append(self: CanonicalStore, bars: Any) -> Any:
        del self, bars
        raise AssertionError("precheck must not call a canonical writer")

    monkeypatch.setattr(CanonicalStore, "append", forbidden_append)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/batches/precheck",
            json=_request(fixture).to_storage_dict(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["schema"] == "backtest_precheck.v1"
    assert response.json()["overall_status"] == "pass"
    assert response.json()["can_submit"] is True
    assert _tree_inventory(fixture.data_root) == before
    assert not (fixture.data_root / "jobs" / "batches").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ("/api/v1/batches/precheck", "/api/v1/batches/submit"),
    ids=("precheck", "submit"),
)
@pytest.mark.parametrize(
    "failure_mode",
    ("default_construction", "injected_evaluate"),
)
async def test_admission_dependency_failures_are_sanitized_503_and_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    failure_mode: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    monkeypatch.setattr(batch_queue_mod, "_GLOBAL_QUEUE", queue)
    secret = (
        r"C:\Users\Carlos\Secret\admission-registry.yaml"
        if failure_mode == "default_construction"
        else r"C:\Users\Carlos\Secret\run-reference.sqlite3"
    )

    if failure_mode == "default_construction":
        monkeypatch.setattr(
            app.state,
            "backtest_admission_service",
            object(),
            raising=False,
        )

        def fail_default_service() -> BacktestAdmissionService:
            raise OSError(secret)

        monkeypatch.setattr(
            routes_batch_mod,
            "default_backtest_admission_service",
            fail_default_service,
        )
    else:
        service = _service(fixture)

        def fail_evaluate(
            request: P4BatchRequest,
            *,
            only_cells: Any = None,
        ) -> Any:
            del request, only_cells
            raise OSError(secret)

        monkeypatch.setattr(service, "evaluate", fail_evaluate)
        monkeypatch.setattr(
            app.state,
            "backtest_admission_service",
            service,
            raising=False,
        )

    before = _tree_inventory(fixture.data_root)
    writer_calls = _forbid_admission_failure_writers(monkeypatch, queue=queue)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            endpoint,
            json=_request(fixture).to_storage_dict(),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "backtest admission truth is unavailable"}
    assert secret not in str(response.json())
    assert "OSError" not in response.text
    assert "Traceback" not in response.text
    assert "<html" not in response.text.lower()
    assert writer_calls == {}
    assert queue.list_batches() == []
    assert _tree_inventory(fixture.data_root) == before
    assert list(fixture.data_root.rglob("*.tmp")) == []


@pytest.mark.asyncio
async def test_unmigrated_duplicate_truth_is_200_unknown_and_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)

    def migration_required() -> RunReferenceCatalog:
        raise RunReferenceMigrationRequired("fixture migration required")

    service = _service(fixture, catalog_loader=migration_required)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    before = _tree_inventory(fixture.data_root)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/batches/precheck",
            json=_request(fixture).to_storage_dict(),
        )

    assert response.status_code == 200
    assert response.json()["overall_status"] == "unknown"
    duplicate = response.json()["units"][0]["duplicate"]
    assert duplicate["count_known"] is False
    assert duplicate["exact_match_count"] is None
    assert _tree_inventory(fixture.data_root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validation_run", False),
        ("quantity", 1),
        ("session_name", "eth"),
        ("skip_nautilus_replay", False),
    ],
)
async def test_p4_submit_rejects_engineering_fields_with_422(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    payload = _request(fixture).to_storage_dict()
    payload[field] = value
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/batches/submit", json=payload)

    assert response.status_code == 422
    assert queue.list_batches() == []


@pytest.mark.asyncio
async def test_boolean_slippage_string_is_rejected_not_coerced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    payload = _request(fixture).to_storage_dict()
    assumptions = payload["execution_assumptions"]
    assert isinstance(assumptions, dict)
    slippage = assumptions["slippage_ticks"]
    assert isinstance(slippage, dict)
    slippage["breakout_entry"] = "1"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/batches/precheck", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_block_returns_same_precheck_facts_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    catalog = _matching_catalog(fixture)
    service = _service(fixture, catalog_loader=lambda: catalog)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    before = _tree_inventory(fixture.data_root)
    payload = _request(fixture).to_storage_dict()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        precheck = await client.post("/api/v1/batches/precheck", json=payload)
        submit = await client.post("/api/v1/batches/submit", json=payload)

    assert precheck.status_code == 200
    assert submit.status_code == 409
    assert submit.json() == precheck.json()
    assert queue.list_batches() == []
    assert _tree_inventory(fixture.data_root) == before


@pytest.mark.asyncio
async def test_submit_calls_shared_core_before_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    original_evaluate = service.evaluate
    calls = 0

    def counted_evaluate(
        request: P4BatchRequest,
        *,
        only_cells: Any = None,
    ) -> Any:
        nonlocal calls
        calls += 1
        return original_evaluate(request, only_cells=only_cells)

    monkeypatch.setattr(service, "evaluate", counted_evaluate)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/batches/submit",
            json=_request(fixture).to_storage_dict(),
        )

    assert response.status_code == 200, response.text
    assert calls == 1
    body = response.json()
    assert body["schema"] == "batch_job.v2"
    assert body["summary"]["total"] == 1
    assert "-standard-" in body["jobs"][0]["run_id"]
    snapshot = body["jobs"][0]["execution_assumptions"]
    assert snapshot == {
        "initial_capital_usd": 100_000.0,
        "commission_per_side": 2.5,
        "slippage_ticks": {
            "breakout_entry": 1,
            "stop_exit": 2,
            "target_exit": 0,
            "day_end_exit": 1,
        },
        "target_requires_through": False,
        "fill_model": "conservative",
        "simulation_precision": "one_minute",
        "quantity": 1,
    }


@pytest.mark.asyncio
async def test_submit_persistence_failure_has_zero_visible_batch_or_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    batches_root = fixture.data_root / "jobs" / "batches"
    original_replace = Path.replace

    def fail_batch_replace(self: Path, target: Path) -> Path:
        if self.parent == batches_root and self.suffix == ".tmp":
            raise OSError("fixture atomic replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_batch_replace)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/batches/submit",
            json=_request(fixture).to_storage_dict(),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "standard batch could not be published atomically"
    assert queue._batches == {}
    assert list(batches_root.iterdir()) == []


@pytest.mark.asyncio
async def test_engineering_submit_persistence_failure_is_sanitized_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=_service(fixture), queue=queue)
    batches_root = fixture.data_root / "jobs" / "batches"
    batches_root.mkdir(parents=True)
    existing = batches_root / "existing-operational-state.keep"
    existing.write_bytes(b"existing operational bytes\n")
    before = _tree_inventory(fixture.data_root)
    worker_starts: list[str] = []
    original_replace = Path.replace

    def count_worker_start() -> None:
        worker_starts.append("worker")

    def fail_batch_replace(self: Path, target: Path) -> Path:
        if self.parent == batches_root and self.suffix == ".tmp":
            raise OSError("C:/secret/persist failed")
        return original_replace(self, target)

    monkeypatch.setattr(queue, "_ensure_worker_unlocked", count_worker_start)
    monkeypatch.setattr(Path, "replace", fail_batch_replace)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/batches/submit",
            json={
                "symbols": ["NQ"],
                "strategy_versions": ["trend-v0"],
                "range_start": "2026-05-06T22:00:00Z",
                "range_end": "2026-05-08T21:00:00Z",
                "validation_run": True,
                "skip_nautilus_replay": True,
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "batch operational state is unavailable"
    }
    assert "OSError" not in response.text
    assert "C:/secret" not in response.text
    assert "Traceback" not in response.text
    assert queue._batches == {}
    assert queue.list_batches() == []
    assert worker_starts == []
    assert _tree_inventory(fixture.data_root) == before
    assert existing.read_bytes() == b"existing operational bytes\n"
    assert list(batches_root.glob("*.json")) == []
    assert list(batches_root.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_invalid_engineering_submit_remains_400_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=_service(fixture), queue=queue)
    persist_calls: list[str] = []

    def forbidden_persist(record: BatchRecord) -> None:
        del record
        persist_calls.append("persist")
        raise AssertionError("invalid engineering request reached persistence")

    monkeypatch.setattr(queue, "_persist_unlocked", forbidden_persist)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/batches/submit",
            json={
                "symbols": ["NQ"],
                "strategy_versions": ["trend-v0"],
                "range_start": "2026-05-06T22:00:00Z",
                "range_end": "2026-05-08T21:00:00Z",
                "validation_run": True,
                "pullback_ema_period": 17,
                "skip_nautilus_replay": True,
            },
        )

    assert response.status_code == 400
    assert persist_calls == []
    assert queue._batches == {}


@pytest.mark.asyncio
async def test_blocking_day_has_zero_progress_callbacks_runner_or_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(
        tmp_path,
        omit_minute_by_date={RUN_DATE: 5},
    )
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)
    before = _tree_inventory(fixture.data_root)
    calls = _forbid_admission_failure_writers(monkeypatch, queue=queue)

    def forbidden_runner(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        calls["runner"] = calls.get("runner", 0) + 1
        raise AssertionError("blocking day reached the runner")

    def forbidden_progress(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        calls["progress_callback"] = calls.get("progress_callback", 0) + 1
        raise AssertionError("blocking day emitted progress")

    monkeypatch.setattr(BacktestRunner, "run", forbidden_runner)
    monkeypatch.setattr(queue, "_record_day_progress", forbidden_progress)
    payload = _request(fixture).to_storage_dict()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        precheck = await client.post("/api/v1/batches/precheck", json=payload)
        submit = await client.post("/api/v1/batches/submit", json=payload)

    assert precheck.status_code == 200
    assert submit.status_code == 409
    assert submit.json() == precheck.json()
    coverage = precheck.json()["units"][0]["coverage"]
    assert coverage["blocking_problem_trading_dates"] == [
        RUN_DATE.isoformat()
    ]
    assert coverage["admitted_trading_date_count"] == 0
    assert calls == {}
    assert queue.list_batches() == []
    assert _tree_inventory(fixture.data_root) == before
    assert list((fixture.data_root / "jobs").rglob("*.tmp")) == []


def _write_exclude_decision(data_root: Path, contract_id: str, trading_date: date) -> None:
    path = data_root / "blacklists" / "owner-excluded.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "owner_blacklist.v1",
                "updated_at": "2026-07-27T08:00:00Z",
                "entries": [
                    {
                        "contract_id": contract_id,
                        "trading_date": trading_date.isoformat(),
                        "decision": "exclude",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_job_start_rechecks_truth_drift_and_never_calls_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(
        data_root=fixture.data_root,
        admission_service=service,
        clock=lambda: datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )
    request = _request(fixture)
    record = queue.submit_standard(
        request,
        service.evaluate(request),
        admission_service=service,
    )
    _write_exclude_decision(
        fixture.data_root,
        fixture.contract.contract_id,
        RUN_DATE,
    )

    def forbidden_runner(
        self: BacktestRunner,
        *,
        contract: Any,
        config: BacktestRunConfig,
    ) -> Any:
        del self, contract, config
        raise AssertionError("runner must not execute after a blocking job-start recheck")

    monkeypatch.setattr(BacktestRunner, "run", forbidden_runner)

    with pytest.raises(AdmissionRejected, match="coverage_all_dates_excluded"):
        queue._execute_standard_job(record, record.jobs[0])


def test_job_start_rejects_assumption_snapshot_drift_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(
        data_root=fixture.data_root,
        admission_service=service,
    )
    request = _request(fixture)
    record = queue.submit_standard(
        request,
        service.evaluate(request),
        admission_service=service,
    )
    snapshot = record.jobs[0].execution_assumptions
    assert snapshot is not None
    snapshot["initial_capital_usd"] = 50_000.0

    def forbidden_runner(
        self: BacktestRunner,
        *,
        contract: Any,
        config: BacktestRunConfig,
    ) -> Any:
        del self, contract, config
        raise AssertionError("runner must not consume a drifted assumptions snapshot")

    monkeypatch.setattr(BacktestRunner, "run", forbidden_runner)

    with pytest.raises(AdmissionRejected, match="execution_assumptions_drift"):
        queue._execute_standard_job(record, record.jobs[0])


def test_job_start_runner_consumes_exact_plan_assumptions_and_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dates = (RUN_DATE, date(2026, 7, 21))
    fixture = _build_fixture(
        tmp_path,
        run_dates=run_dates,
        owner_decisions={run_dates[1]: "exclude"},
    )
    service = _service(fixture)
    queue = PausedBatchQueue(
        data_root=fixture.data_root,
        admission_service=service,
    )
    request = _request(fixture)
    evaluation = service.evaluate(request)
    queue.submit_standard(
        request,
        evaluation,
        admission_service=service,
    )
    captured: list[BacktestRunConfig] = []

    def capture_runner(
        self: BacktestRunner,
        *,
        contract: Any,
        config: BacktestRunConfig,
        day_observer: Any = None,
        progress_clock: Any = None,
    ) -> Any:
        del self, contract, day_observer, progress_clock
        captured.append(config)
        return SimpleNamespace(
            exported=SimpleNamespace(result_path=tmp_path / "not-published.json"),
            chart_materialization_error=None,
        )

    monkeypatch.setattr(BacktestRunner, "run", capture_runner)
    monkeypatch.setattr(
        "futures_research.backtest.scorecard.enrich_result_file",
        lambda *args, **kwargs: None,
    )

    with queue._lock:
        claimed = queue._claim_next_job_unlocked()
    assert claimed is not None
    queue._execute_standard_job(*claimed)

    assert len(captured) == 1
    config = captured[0]
    assert config.validation_run is False
    assert config.initial_capital == 100_000
    assert config.quantity == 1
    assert config.costs is not None
    assert config.costs.commission_per_side == 2.5
    assert config.costs.slippage_ticks.model_dump() == {
        "breakout_entry": 1,
        "stop_exit": 2,
        "target_exit": 0,
        "day_end_exit": 1,
    }
    assert config.admitted_trading_dates == (run_dates[0],)
    assert config.excluded_trading_dates == (run_dates[1],)


def test_one_failed_cell_does_not_prevent_later_queued_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = PausedBatchQueue(
        data_root=tmp_path,
        clock=lambda: datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )
    first = RunJob("job-1", "run-1", "NQ", "strategy-0001")
    second = RunJob("job-2", "run-2", "NQ", "strategy-0002")
    record = BatchRecord(
        batch_id="batch-fixture",
        status="queued",
        created_at="2026-07-27T08:00:00Z",
        updated_at="2026-07-27T08:00:00Z",
        request={},
        jobs=[first, second],
    )
    queue._batches[record.batch_id] = record
    calls: list[str] = []

    def execute(self: BatchQueue, batch: BatchRecord, job: RunJob) -> None:
        del batch
        calls.append(job.job_id)
        if job.job_id == first.job_id:
            raise AdmissionRejected("admission_rejected:truth_drift")
        self._record_day_progress(
            record.batch_id,
            job.job_id,
            _completed_day_snapshot(),
        )

    monkeypatch.setattr(BatchQueue, "_execute_job", execute)

    queue._run_loop()

    assert calls == ["job-1", "job-2"]
    final = queue.get(record.batch_id)
    assert final.jobs[0].status == "failed"
    assert final.jobs[0].message == "admission_rejected:truth_drift"
    assert final.jobs[1].status == "completed"
    assert final.status == "partial"


def test_nested_worker_failure_is_sanitized_and_next_cell_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = PausedBatchQueue(
        data_root=tmp_path,
        clock=lambda: datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )
    first = RunJob("job-1", "run-1", "NQ", "strategy-0001")
    second = RunJob("job-2", "run-2", "NQ", "strategy-0002")
    record = BatchRecord(
        batch_id="batch-nested-worker-error",
        status="queued",
        created_at="2026-07-27T08:00:00Z",
        updated_at="2026-07-27T08:00:00Z",
        request={},
        jobs=[first, second],
    )
    queue._batches[record.batch_id] = record
    inner_path = r"C:\Users\Carlos\Secret\market.arrow"
    outer_path = "/srv/private/worker.py:417"
    calls: list[str] = []

    def execute(self: BatchQueue, batch: BatchRecord, job: RunJob) -> None:
        del batch
        calls.append(job.job_id)
        if job.job_id == first.job_id:
            try:
                raise FileNotFoundError(inner_path)
            except FileNotFoundError as exc:
                raise RuntimeError(outer_path) from exc
        self._record_day_progress(
            record.batch_id,
            job.job_id,
            _completed_day_snapshot(),
        )

    monkeypatch.setattr(BatchQueue, "_execute_job", execute)
    with caplog.at_level(logging.ERROR, logger=batch_queue_mod.__name__):
        queue._run_loop()

    assert calls == ["job-1", "job-2"]
    final = queue.get(record.batch_id)
    assert final.jobs[0].status == "failed"
    assert final.jobs[0].message == (
        "worker_failed:internal_error; check server logs before resubmitting"
    )
    assert final.jobs[1].status == "completed"
    assert final.status == "partial"
    public = json.dumps(final.to_dict())
    for forbidden in (
        inner_path,
        outer_path,
        "Traceback",
        "line 417",
        "<html",
    ):
        assert forbidden not in public
    assert "RuntimeError" in public
    assert "FileNotFoundError" in public
    assert "market.arrow" in caplog.text
    assert "worker.py:417" in caplog.text


def test_chart_sidecar_failure_is_sanitized_warning_on_completed_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = _build_fixture(tmp_path)
    service = _service(fixture)
    queue = PausedBatchQueue(
        data_root=fixture.data_root,
        admission_service=service,
        clock=lambda: datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )
    request = _request(fixture)
    record = queue.submit_standard(
        request,
        service.evaluate(request),
        admission_service=service,
    )
    secret = (
        "Traceback (most recent call last): "
        r"C:\Users\Carlos\Secret\charts\run-1.arrow"
    )
    intended_result = tmp_path / "published-results" / "run-1.json"

    def chart_failure_runner(
        self: BacktestRunner,
        *,
        contract: Any,
        config: BacktestRunConfig,
        day_observer: Any = None,
        progress_clock: Any = None,
    ) -> Any:
        del self, contract, config, progress_clock
        assert day_observer is not None
        day_observer(_completed_day_snapshot())
        return SimpleNamespace(
            exported=SimpleNamespace(result_path=intended_result),
            chart_materialization_error=secret,
        )

    monkeypatch.setattr(BacktestRunner, "run", chart_failure_runner)
    monkeypatch.setattr(
        "futures_research.backtest.scorecard.enrich_result_file",
        lambda *args, **kwargs: None,
    )
    with caplog.at_level(logging.WARNING, logger=batch_queue_mod.__name__):
        queue._run_loop()

    final = queue.get(record.batch_id)
    job = final.jobs[0]
    assert job.status == "completed"
    assert job.message == "ok"
    assert job.result_path == str(intended_result)
    assert job.warnings == (
        "chart_sidecar_unavailable: result is complete; "
        "open the chart to retry cache materialization",
    )
    assert final.status == "completed"
    public = json.dumps(final.to_dict())
    assert secret not in public
    assert "Traceback" not in public
    assert "run-1.arrow" not in public
    assert "run-1.arrow" in caplog.text


@pytest.mark.asyncio
async def test_malformed_owner_truth_returns_503_without_partial_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    path = fixture.data_root / "blacklists" / "owner-excluded.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema":"owner_blacklist.v1","entries":"bad"}', encoding="utf-8")
    service = _service(fixture)
    queue = PausedBatchQueue(data_root=fixture.data_root)
    _install(monkeypatch, service=service, queue=queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        precheck = await client.post(
            "/api/v1/batches/precheck",
            json=_request(fixture).to_storage_dict(),
        )
        submit = await client.post(
            "/api/v1/batches/submit",
            json=_request(fixture).to_storage_dict(),
        )

    assert precheck.status_code == 503
    assert submit.status_code == 503
    assert queue.list_batches() == []
