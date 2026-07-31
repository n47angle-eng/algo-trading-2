"""Batch 3 zero-write preview contract and shared-engine regressions."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest

from futures_research.api import chart_series as chart_series_module
from futures_research.api.main import app
from futures_research.api.routes_backtest_preview import BacktestPreviewBody, _fingerprint
from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner
from futures_research.data.contracts import ContractSpec
from futures_research.data.models import CanonicalBar
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.parser import parse_strategy_document

_SOURCE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "strategies" / "trend_p50_ema18.yaml"


@pytest.fixture
def preview_context(tmp_path: Path, contracts_registry, monkeypatch: pytest.MonkeyPatch):
    """Install an isolated runner only for this test's in-process API calls."""
    contract = contracts_registry.by_symbol("NQ")
    runner, bars, database_path, results_root = _preview_runner(tmp_path, contract)
    monkeypatch.setattr(app.state, "backtest_preview_runner", runner, raising=False)
    yield {
        "contract": contract,
        "runner": runner,
        "bars": bars,
        "database_path": database_path,
        "results_root": results_root,
    }


@pytest.mark.asyncio
async def test_preview_returns_full_evidence_and_four_charts_without_any_writer_call(
    preview_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful P2 preview is a pure read-only replay, not a hidden run creation."""
    runner = preview_context["runner"]
    assert isinstance(runner, BacktestRunner)
    database_path = preview_context["database_path"]
    results_root = preview_context["results_root"]
    assert isinstance(database_path, Path)
    assert isinstance(results_root, Path)
    database_path.write_bytes(b"preview database sentinel")
    results_root.mkdir()
    (results_root / "existing-result.json").write_bytes(b"existing artifact sentinel")
    before_database = database_path.read_bytes()
    before_results = _all_file_hashes(results_root)

    def forbidden_write(*_: object, **__: object) -> NoReturn:
        pytest.fail("preview attempted a durable writer")

    monkeypatch.setattr(runner._run_store, "persist", forbidden_write)
    monkeypatch.setattr(runner._result_exporter, "stage_new", forbidden_write)
    monkeypatch.setattr(runner, "_materialize_chart_sidecars", forbidden_write)
    monkeypatch.setattr(chart_series_module, "_write_json_atomic", forbidden_write)

    body = _preview_request(preview_context)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/backtests/preview", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "backtest_preview.v1"
    assert payload["run_scope"] == "dry_run"
    assert payload["persisted"] is False
    assert payload["validation"]["valid"] is True
    assert payload["funnel"]["schema"] == "funnel.v1"
    assert set(payload["charts"]) == {"D", "1H", "30m", "5m"}
    assert all(chart["persisted"] is False for chart in payload["charts"].values())
    assert payload["evidence_summary"]["availability"] == "available"
    assert payload["evidence_summary"]["complete"] is True
    assert not _contains_key(payload, "run_id")
    assert database_path.read_bytes() == before_database
    assert _all_file_hashes(results_root) == before_results


@pytest.mark.asyncio
async def test_preview_validation_and_engine_failures_are_complete_and_zero_write(
    preview_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither four-layer validation nor an engine error is allowed to consume persistence."""
    runner = preview_context["runner"]
    assert isinstance(runner, BacktestRunner)
    database_path = preview_context["database_path"]
    results_root = preview_context["results_root"]
    assert isinstance(database_path, Path)
    assert isinstance(results_root, Path)
    database_path.write_bytes(b"unchanged")
    results_root.mkdir()
    (results_root / "existing.json").write_bytes(b"unchanged")
    before_database = database_path.read_bytes()
    before_results = _all_file_hashes(results_root)

    def forbidden_write(*_: object, **__: object) -> NoReturn:
        pytest.fail("failed preview attempted a durable writer")

    monkeypatch.setattr(runner._run_store, "persist", forbidden_write)
    monkeypatch.setattr(runner._result_exporter, "stage_new", forbidden_write)

    invalid = _preview_request(preview_context)
    invalid["source_text"] = "schema: strategy.v1\n"
    engine_failure = _preview_request(preview_context)
    engine_failure["range_start"] = "2026-08-01T22:00:00Z"
    engine_failure["range_end"] = "2026-08-01T22:10:00Z"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        invalid_response = await client.post("/api/v1/backtests/preview", json=invalid)
        engine_response = await client.post("/api/v1/backtests/preview", json=engine_failure)

    for response in (invalid_response, engine_response):
        assert response.status_code == 422
        payload = response.json()
        assert payload["schema"] == "backtest_preview.v1"
        assert payload["run_scope"] == "dry_run"
        assert payload["persisted"] is False
        assert payload["validation"]["schema"] == "strategy_validation.v1"
        assert payload["errors"]
        assert payload["decision_evidence"] == []
        assert payload["rejection_evidence"] == []
        assert payload["charts"] == {}
        assert not _contains_key(payload, "run_id")
    assert invalid_response.json()["validation"]["valid"] is False
    assert engine_response.json()["validation"]["valid"] is True
    assert engine_response.json()["errors"][0].startswith("ValueError:")
    assert database_path.read_bytes() == before_database
    assert _all_file_hashes(results_root) == before_results


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name, mutate, invalid_json",
    [
        ("missing", lambda body: body.pop("contract_id"), None),
        ("extra", lambda body: body.update({"unexpected": True}), None),
        ("wrong schema", lambda body: body.update({"schema": "wrong.v1"}), None),
        (
            "zero capital",
            lambda body: body["assumptions"].update({"initial_capital_usd": 0}),
            None,
        ),
        (
            "negative capital",
            lambda body: body["assumptions"].update({"initial_capital_usd": -1}),
            None,
        ),
        (
            "negative commission",
            lambda body: body["assumptions"].update({"commission_per_side": -0.1}),
            None,
        ),
        (
            "negative slippage",
            lambda body: body["assumptions"].update({"slippage_ticks": -1}),
            None,
        ),
        (
            "non finite",
            lambda body: body["assumptions"].update({"initial_capital_usd": "NaN"}),
            None,
        ),
        (
            "naive timestamp",
            lambda body: body.update({"range_start": "2026-07-22T22:00:00"}),
            None,
        ),
        (
            "inverted range",
            lambda body: body.update({"range_start": body["range_end"]}),
            None,
        ),
        ("invalid json", lambda _body: None, b'{"schema":'),
    ],
)
async def test_preview_request_model_failures_use_complete_envelope_and_zero_writes(
    preview_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    mutate: Any,
    invalid_json: bytes | None,
) -> None:
    """FastAPI must not intercept request-model errors before the dry-run envelope exists."""
    runner = preview_context["runner"]
    database_path = preview_context["database_path"]
    results_root = preview_context["results_root"]
    assert isinstance(runner, BacktestRunner)
    assert isinstance(database_path, Path)
    assert isinstance(results_root, Path)
    database_path.write_bytes(b"request-validation database sentinel")
    results_root.mkdir()
    (results_root / "sentinel.json").write_bytes(b"request-validation result sentinel")
    before_database = database_path.read_bytes()
    before_results = _all_file_hashes(results_root)

    def forbidden_write(*_: object, **__: object) -> NoReturn:
        pytest.fail(f"{case_name} attempted a preview writer")

    monkeypatch.setattr(runner._run_store, "persist", forbidden_write)
    monkeypatch.setattr(runner._result_exporter, "stage_new", forbidden_write)
    monkeypatch.setattr(runner, "_materialize_chart_sidecars", forbidden_write)

    body = _preview_request(preview_context)
    mutate(body)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        if invalid_json is None:
            response = await client.post("/api/v1/backtests/preview", json=body)
        else:
            response = await client.post(
                "/api/v1/backtests/preview",
                content=invalid_json,
                headers={"content-type": "application/json"},
            )

    assert response.status_code == 422, case_name
    payload = response.json()
    assert payload["schema"] == "backtest_preview.v1"
    assert payload["run_scope"] == "dry_run"
    assert payload["persisted"] is False
    assert payload["funnel"] is None
    assert payload["decision_evidence"] == []
    assert payload["rejection_evidence"] == []
    assert payload["evidence_summary"]["availability"] == "unavailable"
    assert payload["evidence_summary"]["complete"] is False
    assert payload["charts"] == {}
    assert payload["warnings"] == []
    assert payload["errors"]
    assert payload["validation"]["valid"] is False
    assert payload["validation"]["issue_count"] == len(payload["validation"]["issues"])
    assert all(" — " in issue["line"] for issue in payload["validation"]["issues"])
    assert set(payload["request_fingerprint"]) == {"algorithm", "digest"}
    assert not _contains_key(payload, "run_id")
    assert not _contains_key(payload, "batch_id")
    assert database_path.read_bytes() == before_database
    assert _all_file_hashes(results_root) == before_results


@pytest.mark.asyncio
async def test_preview_request_fingerprint_is_deterministic_from_the_raw_request(
    preview_context: dict[str, object],
) -> None:
    """Rejected raw bodies are still attributable without allocating a durable identity."""
    body = _preview_request(preview_context)
    body["schema"] = "wrong.v1"
    changed = deepcopy(body)
    changed["filename"] = "a-different-raw-field.yaml"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post("/api/v1/backtests/preview", json=body)
        second = await client.post("/api/v1/backtests/preview", json=body)
        third = await client.post("/api/v1/backtests/preview", json=changed)

    assert first.status_code == second.status_code == third.status_code == 422
    assert first.json()["request_fingerprint"] == second.json()["request_fingerprint"]
    assert (
        first.json()["request_fingerprint"]["digest"]
        != third.json()["request_fingerprint"]["digest"]
    )


@pytest.mark.asyncio
async def test_preview_reuses_the_canonical_p2_universe_validator_without_writing(
    preview_context: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = preview_context["runner"]
    assert isinstance(runner, BacktestRunner)

    def forbidden_write(*_: object, **__: object) -> NoReturn:
        pytest.fail("invalid P2 universe attempted a preview writer")

    monkeypatch.setattr(runner._run_store, "persist", forbidden_write)
    monkeypatch.setattr(runner._result_exporter, "stage_new", forbidden_write)
    body = _preview_request(preview_context)
    body["source_text"] = str(body["source_text"]).replace(
        "asset_class: equity_index_futures", "asset_class: commodity_futures"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/backtests/preview", json=body)

    assert response.status_code == 422
    payload = response.json()
    assert any(issue["path"] == "universe.asset_class" for issue in payload["validation"]["issues"])
    assert payload["persisted"] is False


def test_preview_replay_and_durable_run_share_the_same_evidence_facts(
    tmp_path: Path,
    contracts_registry,
) -> None:
    """The only approved preview/standard difference is persistence, never engine semantics."""
    contract = contracts_registry.by_symbol("NQ")
    runner, bars, _, _ = _preview_runner(tmp_path, contract)
    parsed = parse_strategy_document(_SOURCE_PATH.read_text(encoding="utf-8"))
    common = {
        "strategy_version": "unsaved-preview-source",
        "session_name": "eth",
        "range_start": bars[0].timestamp,
        "range_end": bars[-1].timestamp + timedelta(minutes=1),
        "initial_capital": 100_000.0,
        "quantity": 1,
        "verify_nautilus_replay": False,
        "strategy_spec": parsed.spec,
    }
    preview = runner.preview(
        contract=contract,
        config=BacktestRunConfig(run_id="preview-internal", **common),
    )
    durable = runner.run(
        contract=contract,
        config=BacktestRunConfig(run_id="preview-equivalence-001", **common),
    )

    assert [item.to_dict() for item in preview.result.rejection_evidence] == [
        item.to_dict() for item in durable.result.rejection_evidence
    ]
    assert preview.result.evidence_summary is not None
    assert durable.result.evidence_summary is not None
    assert preview.result.evidence_summary.to_dict() == durable.result.evidence_summary.to_dict()
    assert [
        record.decision_evidence.to_dict()
        for record in preview.result.trade_records
        if record.decision_evidence is not None
    ] == [
        record.decision_evidence.to_dict()
        for record in durable.result.trade_records
        if record.decision_evidence is not None
    ]


def test_preview_fingerprint_includes_every_operator_controlled_input(
    tmp_path: Path,
    contracts_registry,
) -> None:
    """Changing source, contract, range, or an assumption never reuses a preview identity."""
    contract = contracts_registry.by_symbol("NQ")
    _, bars, _, _ = _preview_runner(tmp_path, contract)
    body = _preview_request(
        {
            "contract": contract,
            "bars": bars,
        }
    )
    variants: list[dict[str, Any]] = []
    source_change = deepcopy(body)
    source_change["source_text"] = str(source_change["source_text"]) + "\n"
    variants.append(source_change)
    contract_change = deepcopy(body)
    contract_change["contract_id"] = "GC-202608-COMEX"
    variants.append(contract_change)
    range_change = deepcopy(body)
    range_change["range_end"] = (
        bars[-1].timestamp + timedelta(minutes=2)
    ).isoformat().replace("+00:00", "Z")
    variants.append(range_change)
    assumption_change = deepcopy(body)
    assumptions = dict(assumption_change["assumptions"])
    assumptions["commission_per_side"] = 3.0
    assumption_change["assumptions"] = assumptions
    variants.append(assumption_change)

    digests = {
        _fingerprint(BacktestPreviewBody.model_validate(candidate))["digest"]
        for candidate in [body, *variants]
    }
    assert len(digests) == 5


def _preview_runner(
    tmp_path: Path,
    contract: ContractSpec,
) -> tuple[BacktestRunner, tuple[CanonicalBar, ...], Path, Path]:
    start = datetime(2026, 7, 22, 22, tzinfo=UTC)
    bars = tuple(
        CanonicalBar(
            timestamp=start + timedelta(minutes=index),
            open=20_000.0 + index * 0.25,
            high=20_001.0 + index * 0.25,
            low=19_999.5 + index * 0.25,
            close=20_000.5 + index * 0.25,
            volume=100,
            contract_id=contract.contract_id,
            source="fixture",
        )
        for index in range(10)
    )
    market_root = tmp_path / "market"
    CanonicalStore(market_root).append(bars)
    database_path = tmp_path / "runs.sqlite3"
    results_root = tmp_path / "results"
    return (
        BacktestRunner(
            canonical_store=CanonicalStore(market_root),
            daily_canonical_store=CanonicalStore(tmp_path / "market-daily"),
            run_store=SqliteRunStore(database_path),
            result_exporter=ResultExporter(results_root),
            quality_reports_root=None,
        ),
        bars,
        database_path,
        results_root,
    )


def _preview_request(context: dict[str, object]) -> dict[str, Any]:
    contract = context["contract"]
    bars = context["bars"]
    assert isinstance(contract, ContractSpec)
    assert isinstance(bars, tuple)
    first = bars[0]
    last = bars[-1]
    assert isinstance(first, CanonicalBar)
    assert isinstance(last, CanonicalBar)
    return {
        "schema": "backtest_preview_request.v1",
        "source_text": _SOURCE_PATH.read_text(encoding="utf-8"),
        "filename": "unsaved-variant.yaml",
        "contract_id": contract.contract_id,
        "range_start": first.timestamp.isoformat().replace("+00:00", "Z"),
        "range_end": (last.timestamp + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "assumptions": {
            "initial_capital_usd": 100_000.0,
            "commission_per_side": 2.5,
            "slippage_ticks": 1,
        },
    }


def _all_file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _contains_key(value: object, wanted: str) -> bool:
    if isinstance(value, dict):
        return wanted in value or any(_contains_key(item, wanted) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, wanted) for item in value)
    return False
