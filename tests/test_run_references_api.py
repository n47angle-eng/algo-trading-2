"""HTTP-contract coverage for the additive standard-run reference endpoint."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from futures_research.api.main import app
from futures_research.backtest.persistence import SqliteRunStore
from futures_research.backtest.records import (
    PreparedRun,
    RunResult,
    build_run_result,
    consumed_trading_dates_for_bars,
    prepare_run,
)
from futures_research.backtest.run_reference_catalog import RunReferenceCatalog
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.models import CanonicalBar

_COMPLETED_AT = datetime(2026, 7, 26, 12, tzinfo=UTC)
_RANGE_START = datetime(2026, 7, 20, 22, tzinfo=UTC)
_RANGE_END = _RANGE_START + timedelta(minutes=1)


def _bar(contract: ContractSpec) -> CanonicalBar:
    return CanonicalBar(
        timestamp=_RANGE_START,
        open=20_000.0,
        high=20_001.0,
        low=19_999.0,
        close=20_000.5,
        volume=100,
        contract_id=contract.contract_id,
        source="fixture",
    )


def _prepared(
    contract: ContractSpec,
    *,
    run_id: str,
    strategy_version: str,
    validation_run: bool = False,
) -> PreparedRun:
    return prepare_run(
        run_id=run_id,
        strategy_version=strategy_version,
        contract=contract,
        session_name="eth",
        range_start=_RANGE_START,
        range_end=_RANGE_END,
        initial_capital=100_000.0,
        quantity=1,
        canonical_bars=(_bar(contract),),
        validation_run=validation_run,
        created_at=_COMPLETED_AT,
    )


def _persist(
    store: SqliteRunStore,
    contract: ContractSpec,
    *,
    run_id: str,
    strategy_version: str = "strategy-0003",
    validation_run: bool = False,
) -> PreparedRun:
    prepared = _prepared(
        contract,
        run_id=run_id,
        strategy_version=strategy_version,
        validation_run=validation_run,
    )
    result: RunResult = build_run_result(
        manifest=prepared.manifest,
        trade_records=(),
        event_log=(),
        contract=contract,
        completed_at=_COMPLETED_AT,
    )
    store.persist(
        result,
        contract=contract,
        consumed_trading_dates=consumed_trading_dates_for_bars(
            prepared.bars,
            contract=contract,
            session_name="eth",
        ),
    )
    return prepared


@pytest.fixture
def run_reference_client(tmp_path: Path, contracts_registry: ContractRegistry):
    """ASGI client backed by a complete pair of standard rows plus one validation row."""
    contract = contracts_registry.by_symbol("NQ")
    store = SqliteRunStore(tmp_path / "runs.sqlite3")
    _persist(store, contract, run_id="standard-002")
    _persist(store, contract, run_id="standard-001")
    _persist(store, contract, run_id="validation-001", validation_run=True)
    _persist(store, contract, run_id="legacy-001", strategy_version="strategy-legacy")
    # The immutable legacy run remains untouched; only its derivative is re-created
    # with truthful missing-date evidence.
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM run_trading_dates WHERE run_id = 'legacy-001'")
        connection.execute("DELETE FROM run_lookup WHERE run_id = 'legacy-001'")
        connection.commit()
    store.backfill_run_indexes(registry=contracts_registry)

    app.state.run_reference_catalog = RunReferenceCatalog.from_sources(
        main_database=store.path,
        audit_database=None,
        registry=contracts_registry,
    )
    app.state.run_reference_registry = contracts_registry
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield client
    if hasattr(app.state, "run_reference_catalog"):
        delattr(app.state, "run_reference_catalog")
    if hasattr(app.state, "run_reference_registry"):
        delattr(app.state, "run_reference_registry")


@pytest.mark.asyncio
async def test_strategy_mode_returns_only_sorted_standard_runs(
    run_reference_client: httpx.AsyncClient,
) -> None:
    """Validation evidence never blocks an Owner-facing standard strategy reference check."""
    response = await run_reference_client.get(
        "/api/v1/run-references",
        params={"mode": "strategy", "strategy_version": "strategy-0003"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "schema": "run_reference_list.v1",
        "mode": "strategy",
        "run_scope": "standard",
        "count_known": True,
        "count": 2,
        "known_match_count": 2,
        "runs": [
            {
                "run_id": "standard-001",
                "strategy_version": "strategy-0003",
                "contract_id": "NQ-202609-CME",
                "symbol": "NQ",
                "session_name": "eth",
                "range_start": "2026-07-20T22:00:00Z",
                "range_end": "2026-07-20T22:01:00Z",
            },
            {
                "run_id": "standard-002",
                "strategy_version": "strategy-0003",
                "contract_id": "NQ-202609-CME",
                "symbol": "NQ",
                "session_name": "eth",
                "range_start": "2026-07-20T22:00:00Z",
                "range_end": "2026-07-20T22:01:00Z",
            },
        ],
        "unindexed_candidates": [],
    }


@pytest.mark.asyncio
async def test_duplicate_mode_requires_each_exact_identity_part_after_utc_normalization(
    run_reference_client: httpx.AsyncClient,
) -> None:
    """The duplicate check is exact, except for an equivalent explicit UTC offset."""
    base = {
        "mode": "duplicate",
        "strategy_version": "strategy-0003",
        "symbol": "NQ",
        "range_start": "2026-07-21T06:00:00+08:00",
        "range_end": "2026-07-21T06:01:00+08:00",
    }
    matched = await run_reference_client.get("/api/v1/run-references", params=base)
    assert matched.status_code == 200
    assert matched.json()["count"] == 2
    assert [row["run_id"] for row in matched.json()["runs"]] == [
        "standard-001",
        "standard-002",
    ]

    for field, value in (
        ("strategy_version", "strategy-other"),
        ("symbol", "GC"),
        ("range_start", "2026-07-20T22:00:30Z"),
        ("range_end", "2026-07-20T22:02:00Z"),
    ):
        no_match = await run_reference_client.get(
            "/api/v1/run-references",
            params={**base, field: value},
        )
        assert no_match.status_code == 200
        assert no_match.json()["count_known"] is True
        assert no_match.json()["count"] == 0
        assert no_match.json()["runs"] == []


@pytest.mark.asyncio
async def test_trading_date_distinguishes_unknown_legacy_evidence_from_known_zero(
    run_reference_client: httpx.AsyncClient,
) -> None:
    """A legacy run overlapping a session prevents a false zero; non-overlap does not."""
    unknown = await run_reference_client.get(
        "/api/v1/run-references",
        params={"mode": "trading-date", "symbol": "NQ", "trading_date": "2026-07-21"},
    )
    assert unknown.status_code == 200
    unknown_body = unknown.json()
    assert unknown_body["count_known"] is False
    assert unknown_body["count"] is None
    assert unknown_body["known_match_count"] == 2
    assert [row["run_id"] for row in unknown_body["runs"]] == [
        "standard-001",
        "standard-002",
    ]
    assert unknown_body["unindexed_candidates"] == [
        {"run_id": "legacy-001", "reason": "trading_dates_unavailable"}
    ]

    known_zero = await run_reference_client.get(
        "/api/v1/run-references",
        params={"mode": "trading-date", "symbol": "NQ", "trading_date": "2026-07-22"},
    )
    assert known_zero.status_code == 200
    assert known_zero.json()["count_known"] is True
    assert known_zero.json()["count"] == 0
    assert known_zero.json()["known_match_count"] == 0
    assert known_zero.json()["runs"] == []
    assert known_zero.json()["unindexed_candidates"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"mode": "strategy"},
        {"mode": "strategy", "strategy_version": "strategy-0003", "symbol": "NQ"},
        {"mode": "trading-date", "symbol": "ES", "trading_date": "2026-07-21"},
        {"mode": "trading-date", "symbol": "NQ", "trading_date": "2026-7-21"},
        {
            "mode": "duplicate",
            "strategy_version": "strategy-0003",
            "symbol": "NQ",
            "range_start": "2026-07-20T22:00:00",
            "range_end": "2026-07-20T22:01:00Z",
        },
        {
            "mode": "duplicate",
            "strategy_version": "strategy-0003",
            "symbol": "NQ",
            "range_start": "2026-07-20T22:01:00Z",
            "range_end": "2026-07-20T22:01:00Z",
        },
    ],
)
async def test_query_shape_is_strictly_fail_closed(
    run_reference_client: httpx.AsyncClient,
    params: dict[str, str],
) -> None:
    """Missing, surplus, invalid, and ambiguous query fields always receive 422."""
    response = await run_reference_client.get("/api/v1/run-references", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_repeated_query_field_is_rejected(run_reference_client: httpx.AsyncClient) -> None:
    """A duplicate query name cannot silently choose one identity value."""
    response = await run_reference_client.get(
        "/api/v1/run-references",
        params=[
            ("mode", "strategy"),
            ("strategy_version", "strategy-0003"),
            ("strategy_version", "strategy-other"),
        ],
    )
    assert response.status_code == 422
