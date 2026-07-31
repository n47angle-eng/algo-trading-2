"""P4-A composite coverage, warm-up, duplicate, and immutable-plan truth."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from futures_research.api.backtest_admission import (
    BacktestAdmissionService,
    P4BatchRequest,
)
from futures_research.backtest import runner as runner_mod
from futures_research.backtest.persistence import (
    ResultExporter,
    RunIndexIntegrityError,
    SqliteRunStore,
)
from futures_research.backtest.records import (
    fingerprint_canonical_bars,
    prepare_run,
)
from futures_research.backtest.run_reference_catalog import (
    CatalogRun,
    RunReferenceCatalog,
    RunReferenceMigrationRequired,
)
from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner
from futures_research.data import coverage as coverage_mod
from futures_research.data.contracts import (
    ContractRegistry,
    ContractSpec,
    SessionHours,
)
from futures_research.data.models import CanonicalBar
from futures_research.data.sessions import (
    session_bounds_for_trading_date,
    trading_date_for_session_start,
)
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.store import StrategyStore

STRATEGY_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "strategies"
P65_EMA90 = STRATEGY_FIXTURES / "trend_p65_ema90.yaml"
P50_EMA18 = STRATEGY_FIXTURES / "trend_p50_ema18.yaml"
RUN_DATE = date(2026, 7, 20)
FIXED_NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)

TOP_LEVEL_KEYS = {
    "schema",
    "checked_at",
    "overall_status",
    "can_submit",
    "unit_count",
    "units",
}
UNIT_KEYS = {
    "strategy_version",
    "symbol",
    "contract_id",
    "session_name",
    "range_start",
    "range_end",
    "status",
    "reason_codes",
    "coverage",
    "warmup",
    "duplicate",
}
COVERAGE_KEYS = {
    "status",
    "requested_trading_date_count",
    "admitted_trading_date_count",
    "complete_trading_dates",
    "owner_trusted_problem_trading_dates",
    "owner_excluded_trading_dates",
    "roll_blackout_trading_dates",
    "excluded_trading_dates",
    "blocking_problem_trading_dates",
    "missing_native_daily_trading_dates",
    "reason_codes",
}
WARMUP_KEYS = {
    "status",
    "required_prior_trading_date_count",
    "available_prior_trading_date_count",
    "evaluable_trading_date_count",
    "first_evaluable_trading_date",
    "suggested_range_start",
    "reason_codes",
}
DUPLICATE_KEYS = {
    "status",
    "count_known",
    "exact_match_count",
    "prior_run_ids",
    "unindexed_candidate_count",
    "acknowledged",
    "reason_codes",
}


@dataclass(frozen=True)
class AdmissionFixture:
    data_root: Path
    registry: ContractRegistry
    store: StrategyStore
    strategy_id: str
    contract: ContractSpec
    run_dates: tuple[date, ...]
    range_start: datetime
    range_end: datetime


def _tiny_registry(*, include_ym: bool = False) -> ContractRegistry:
    real = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    sessions = {
        "eth": SessionHours(start=time(9, 0), end=time(9, 10)),
        "rth": SessionHours(start=time(9, 2), end=time(9, 8)),
    }
    symbols = ("NQ", "YM") if include_ym else ("NQ",)
    contracts = {
        symbol: real.by_symbol(symbol).model_copy(
            update={
                "sessions": sessions,
                "roll_blackout_half_window_days": 0,
                "roll_blackout_overrides": [],
            }
        )
        for symbol in symbols
    }
    return ContractRegistry(
        version=real.version,
        canonical_storage=real.canonical_storage,
        contracts=contracts,
    )


def _weekdays_before(trading_date: date, count: int) -> tuple[date, ...]:
    result: list[date] = []
    cursor = trading_date - timedelta(days=1)
    while len(result) < count:
        if cursor.weekday() <= 4:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(reversed(result))


def _minute_bars(
    contract: ContractSpec,
    trading_date: date,
    *,
    omit_index: int | None = None,
) -> tuple[CanonicalBar, ...]:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    count = int((bounds[1] - bounds[0]) / timedelta(minutes=1))
    return tuple(
        CanonicalBar(
            timestamp=bounds[0] + index * timedelta(minutes=1),
            open=100.0,
            high=100.0 + contract.tick_size,
            low=100.0 - contract.tick_size,
            close=100.0,
            volume=100,
            contract_id=contract.contract_id,
            source="fixture",
        )
        for index in range(count)
        if index != omit_index
    )


def _daily_bar(
    contract: ContractSpec,
    trading_date: date,
    *,
    ordinal: int,
) -> CanonicalBar:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    close = 100.0 + ordinal * contract.tick_size
    return CanonicalBar(
        timestamp=bounds[0],
        open=close - contract.tick_size,
        high=close + 2 * contract.tick_size,
        low=close - 2 * contract.tick_size,
        close=close,
        volume=1_000 + ordinal,
        contract_id=contract.contract_id,
        source="ib_native_daily",
    )


def _import_confirmed(
    store: StrategyStore,
    *,
    ema18: bool = False,
    session_name: str = "eth",
    include_ym: bool = False,
) -> str:
    path = P50_EMA18 if ema18 else P65_EMA90
    text = path.read_text(encoding="utf-8")
    if session_name != "eth":
        text = text.replace("  session: eth", f"  session: {session_name}")
    if include_ym:
        text = text.replace(
            "  contracts: [NQ]\n  expansion_rationale: {}",
            (
                "  contracts: [NQ, YM]\n"
                "  expansion_rationale:\n"
                "    YM: 同資產類別跨合約穩健性驗證"
            ),
        )
    outcome = store.import_document(text)
    strategy_id = str(outcome.record["strategy_id"])
    store.confirm(strategy_id)
    return strategy_id


def _build_fixture(
    tmp_path: Path,
    *,
    prior_count: int = 95,
    run_dates: tuple[date, ...] = (RUN_DATE,),
    omit_minute_by_date: dict[date, int] | None = None,
    omit_native_dates: frozenset[date] = frozenset(),
    owner_decisions: dict[date, str] | None = None,
    ema18: bool = False,
    session_name: str = "eth",
) -> AdmissionFixture:
    data_root = tmp_path / "data"
    registry = _tiny_registry()
    contract = registry.by_symbol("NQ")
    store = StrategyStore(root=data_root / "strategies")
    strategy_id = _import_confirmed(
        store,
        ema18=ema18,
        session_name=session_name,
    )

    omitted = omit_minute_by_date or {}
    minutes = tuple(
        bar
        for trading_date in run_dates
        for bar in _minute_bars(
            contract,
            trading_date,
            omit_index=omitted.get(trading_date),
        )
    )
    CanonicalStore(data_root / "market").append(minutes)

    prior_dates = _weekdays_before(run_dates[0], prior_count)
    native_dates = tuple(
        day
        for day in (*prior_dates, *run_dates)
        if day not in omit_native_dates
    )
    CanonicalStore(data_root / "market-daily").append(
        _daily_bar(contract, day, ordinal=index)
        for index, day in enumerate(native_dates)
    )

    if owner_decisions:
        path = data_root / "blacklists" / "owner-excluded.v1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": "owner_blacklist.v1",
                    "updated_at": "2026-07-27T08:00:00Z",
                    "entries": [
                        {
                            "contract_id": contract.contract_id,
                            "trading_date": day.isoformat(),
                            "decision": decision,
                            "note": "fixture",
                            "decided_at": "2026-07-27T08:00:00Z",
                        }
                        for day, decision in owner_decisions.items()
                    ],
                }
            ),
            encoding="utf-8",
        )

    first_bounds = session_bounds_for_trading_date(
        contract,
        run_dates[0],
        session_name=session_name,
    )
    last_bounds = session_bounds_for_trading_date(
        contract,
        run_dates[-1],
        session_name=session_name,
    )
    assert first_bounds is not None and last_bounds is not None
    return AdmissionFixture(
        data_root=data_root,
        registry=registry,
        store=store,
        strategy_id=strategy_id,
        contract=contract,
        run_dates=run_dates,
        range_start=first_bounds[0],
        range_end=last_bounds[1],
    )


def _empty_catalog() -> RunReferenceCatalog:
    return RunReferenceCatalog((), Path("fixture-main.sqlite3"), None)


def _service(
    fixture: AdmissionFixture,
    *,
    catalog_loader: Any = _empty_catalog,
) -> BacktestAdmissionService:
    return BacktestAdmissionService(
        data_root=fixture.data_root,
        registry=fixture.registry,
        strategy_store=fixture.store,
        run_reference_loader=catalog_loader,
        clock=lambda: FIXED_NOW,
    )


def _request(
    fixture: AdmissionFixture,
    *,
    strategy_versions: list[str] | None = None,
    symbols: list[str] | None = None,
    acknowledgements: list[dict[str, str]] | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    commission_by_symbol: dict[str, float] | None = None,
) -> P4BatchRequest:
    selected_symbols = symbols or ["NQ"]
    start = range_start or fixture.range_start
    end = range_end or fixture.range_end
    return P4BatchRequest.model_validate(
        {
            "strategy_versions": strategy_versions or [fixture.strategy_id],
            "symbols": selected_symbols,
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
            "execution_assumptions": {
                "initial_capital_usd": 100_000,
                "commission_per_side_by_symbol": (
                    commission_by_symbol
                    or {symbol: 2.5 for symbol in selected_symbols}
                ),
                "slippage_ticks": {
                    "breakout_entry": 1,
                    "stop_exit": 2,
                    "target_exit": 0,
                    "day_end_exit": 1,
                },
            },
            "duplicate_acknowledgements": acknowledgements or [],
        }
    )


def _unit(document: dict[str, Any]) -> dict[str, Any]:
    units = document["units"]
    assert isinstance(units, list) and len(units) == 1
    unit = units[0]
    assert isinstance(unit, dict)
    return unit


@pytest.mark.parametrize(
    ("prior_count", "expected_status", "expected_available"),
    [(94, "warn", 94), (95, "pass", 95)],
)
def test_warmup_exact_94_short_95_sufficient(
    tmp_path: Path,
    prior_count: int,
    expected_status: str,
    expected_available: int,
) -> None:
    fixture = _build_fixture(tmp_path, prior_count=prior_count)

    warmup = _unit(_service(fixture).evaluate(_request(fixture)).document)["warmup"]

    assert warmup["required_prior_trading_date_count"] == 95
    assert warmup["available_prior_trading_date_count"] == expected_available
    assert warmup["status"] == expected_status
    assert warmup["evaluable_trading_date_count"] == (0 if prior_count == 94 else 1)


def test_ema18_strategy_still_uses_shared_daily_95_requirement(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path, prior_count=94, ema18=True)

    warmup = _unit(_service(fixture).evaluate(_request(fixture)).document)["warmup"]

    assert warmup["required_prior_trading_date_count"] == 95
    assert warmup["status"] == "warn"


def test_future_daily_bars_do_not_change_prior_warmup_count(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path, prior_count=94)
    future_dates = (
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
    )
    CanonicalStore(fixture.data_root / "market-daily").append(
        _daily_bar(fixture.contract, day, ordinal=200 + index)
        for index, day in enumerate(future_dates)
    )

    warmup = _unit(_service(fixture).evaluate(_request(fixture)).document)["warmup"]

    assert warmup["available_prior_trading_date_count"] == 94
    assert warmup["status"] == "warn"


def test_moving_start_earlier_does_not_increase_prior_warmup(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path, prior_count=95)
    prior_date = _weekdays_before(RUN_DATE, 95)[-1]
    prior_bounds = session_bounds_for_trading_date(
        fixture.contract,
        prior_date,
        session_name="eth",
    )
    assert prior_bounds is not None

    original = _unit(_service(fixture).evaluate(_request(fixture)).document)["warmup"]
    earlier = _unit(
        _service(fixture)
        .evaluate(_request(fixture, range_start=prior_bounds[0]))
        .document
    )["warmup"]

    assert original["available_prior_trading_date_count"] == 95
    assert earlier["available_prior_trading_date_count"] == 94
    assert earlier["status"] == "warn"


def test_warmup_suggestion_only_moves_start_forward_to_exact_session_boundary(
    tmp_path: Path,
) -> None:
    run_dates = (RUN_DATE, date(2026, 7, 21))
    fixture = _build_fixture(tmp_path, prior_count=94, run_dates=run_dates)
    second_bounds = session_bounds_for_trading_date(
        fixture.contract,
        run_dates[1],
        session_name="eth",
    )
    assert second_bounds is not None

    warmup = _unit(_service(fixture).evaluate(_request(fixture)).document)["warmup"]

    assert warmup["status"] == "warn"
    assert warmup["suggested_range_start"] == second_bounds[0].isoformat().replace(
        "+00:00", "Z"
    )
    assert datetime.fromisoformat(
        warmup["suggested_range_start"].replace("Z", "+00:00")
    ) > fixture.range_start
    shifted_start = datetime.fromisoformat(
        warmup["suggested_range_start"].replace("Z", "+00:00")
    )
    shifted = _unit(
        _service(fixture)
        .evaluate(_request(fixture, range_start=shifted_start))
        .document
    )["warmup"]
    assert shifted["status"] == "pass"
    assert shifted["required_prior_trading_date_count"] == 95
    assert shifted["available_prior_trading_date_count"] == 95


@pytest.mark.parametrize(
    ("scenario", "coverage_status", "reason_code", "admitted_count"),
    [
        ("complete", "pass", "coverage_complete", 1),
        ("trusted", "warn", "coverage_owner_trusted", 1),
        ("pending", "block", "coverage_pending_problem", 0),
        ("excluded", "block", "coverage_all_dates_excluded", 0),
        ("native_missing", "block", "coverage_native_daily_missing", 0),
    ],
)
def test_coverage_owner_decision_partition_is_fail_closed(
    tmp_path: Path,
    scenario: str,
    coverage_status: str,
    reason_code: str,
    admitted_count: int,
) -> None:
    incomplete = scenario in {"trusted", "pending", "excluded"}
    decisions = (
        {RUN_DATE: "trust"}
        if scenario == "trusted"
        else {RUN_DATE: "exclude"}
        if scenario == "excluded"
        else None
    )
    fixture = _build_fixture(
        tmp_path,
        omit_minute_by_date={RUN_DATE: 3} if incomplete else None,
        omit_native_dates=(
            frozenset({RUN_DATE}) if scenario == "native_missing" else frozenset()
        ),
        owner_decisions=decisions,
    )

    coverage = _unit(_service(fixture).evaluate(_request(fixture)).document)[
        "coverage"
    ]

    assert coverage["status"] == coverage_status
    assert reason_code in coverage["reason_codes"]
    assert coverage["admitted_trading_date_count"] == admitted_count
    requested = set(fixture.run_dates)
    admitted = {
        date.fromisoformat(value)
        for key in (
            "complete_trading_dates",
            "owner_trusted_problem_trading_dates",
        )
        for value in coverage[key]
    }
    excluded = {
        date.fromisoformat(value) for value in coverage["excluded_trading_dates"]
    }
    blocking = {
        date.fromisoformat(value)
        for value in coverage["blocking_problem_trading_dates"]
    }
    assert admitted | excluded | blocking == requested
    assert not (admitted & excluded or admitted & blocking or excluded & blocking)


def test_trusted_date_is_admitted_and_excluded_date_is_removed_from_plan(
    tmp_path: Path,
) -> None:
    run_dates = (RUN_DATE, date(2026, 7, 21), date(2026, 7, 22))
    fixture = _build_fixture(
        tmp_path,
        run_dates=run_dates,
        omit_minute_by_date={run_dates[0]: 3, run_dates[1]: 3},
        owner_decisions={run_dates[0]: "trust", run_dates[1]: "exclude"},
    )

    evaluation = _service(fixture).evaluate(_request(fixture))
    plan = evaluation.plan_for(fixture.strategy_id, "NQ")

    assert run_dates[0] in plan.admitted_trading_dates
    assert run_dates[1] in plan.excluded_trading_dates
    assert run_dates[1] not in plan.admitted_trading_dates
    assert run_dates[2] in plan.admitted_trading_dates


def test_missing_minute_date_is_not_relabelled_as_owner_trusted(
    tmp_path: Path,
) -> None:
    run_dates = (RUN_DATE, date(2026, 7, 21))
    fixture = _build_fixture(tmp_path, run_dates=(run_dates[0],))
    second_bounds = session_bounds_for_trading_date(
        fixture.contract,
        run_dates[1],
        session_name="eth",
    )
    assert second_bounds is not None
    request = _request(fixture, range_end=second_bounds[1])

    coverage = _unit(_service(fixture).evaluate(request).document)["coverage"]

    assert coverage["status"] == "block"
    assert "coverage_minute_missing" in coverage["reason_codes"]
    assert run_dates[1].isoformat() in coverage["blocking_problem_trading_dates"]


def test_eth_and_rth_share_one_physical_read_but_keep_distinct_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path, prior_count=95)
    rth_id = _import_confirmed(fixture.store, session_name="rth")
    minute_reads = 0
    daily_reads = 0
    original_arrow_read = coverage_mod._read_full_contract_table
    original_store_read = CanonicalStore.read

    def counted_arrow_read(partitions: Any) -> Any:
        nonlocal minute_reads
        minute_reads += 1
        return original_arrow_read(partitions)

    def counted_store_read(
        self: CanonicalStore,
        contract_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CanonicalBar]:
        nonlocal daily_reads
        if self.root.name == "market-daily":
            daily_reads += 1
        return original_store_read(self, contract_id, start=start, end=end)

    monkeypatch.setattr(coverage_mod, "_read_full_contract_table", counted_arrow_read)
    monkeypatch.setattr(CanonicalStore, "read", counted_store_read)

    document = _service(fixture).evaluate(
        _request(
            fixture,
            strategy_versions=[fixture.strategy_id, rth_id],
        )
    ).document

    assert minute_reads == 1
    assert daily_reads == 1
    assert [unit["session_name"] for unit in document["units"]] == ["eth", "rth"]
    assert all(unit["coverage"]["status"] == "pass" for unit in document["units"])


def test_rth_projection_does_not_hide_timestamp_outside_physical_eth_source(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path, session_name="rth")
    eth_bounds = session_bounds_for_trading_date(
        fixture.contract,
        RUN_DATE,
        session_name="eth",
    )
    assert eth_bounds is not None
    CanonicalStore(fixture.data_root / "market").append(
        [
            CanonicalBar(
                timestamp=eth_bounds[1] + timedelta(minutes=1),
                open=100.0,
                high=100.0 + fixture.contract.tick_size,
                low=100.0 - fixture.contract.tick_size,
                close=100.0,
                volume=100,
                contract_id=fixture.contract.contract_id,
                source="fixture",
            )
        ]
    )

    coverage = _unit(_service(fixture).evaluate(_request(fixture)).document)[
        "coverage"
    ]

    assert coverage["status"] == "unknown"
    assert coverage["reason_codes"] == ["coverage_unknown"]


def _matching_catalog(
    fixture: AdmissionFixture,
    *,
    validation_run: bool = False,
    lookup_available: bool = True,
    symbol: str | None = "NQ",
) -> RunReferenceCatalog:
    run = CatalogRun(
        source="main",
        lookup_available=lookup_available,
        run_id="prior-standard-001",
        validation_run=validation_run,
        strategy_version=fixture.strategy_id,
        strategy_content_sha256=None,
        contract_id=fixture.contract.contract_id,
        symbol=symbol,
        session_name="eth",
        range_start=fixture.range_start.isoformat().replace("+00:00", "Z"),
        range_end=fixture.range_end.isoformat().replace("+00:00", "Z"),
        trading_dates_status="complete",
        trading_dates=fixture.run_dates,
    )
    return RunReferenceCatalog((run,), Path("fixture-main.sqlite3"), None)


def test_exact_standard_duplicate_blocks_until_exact_acknowledgement(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    catalog = _matching_catalog(fixture)
    service = _service(fixture, catalog_loader=lambda: catalog)

    blocked = _unit(service.evaluate(_request(fixture)).document)["duplicate"]
    acknowledgement = {
        "strategy_version": fixture.strategy_id,
        "symbol": "NQ",
        "range_start": fixture.range_start.isoformat(),
        "range_end": fixture.range_end.isoformat(),
    }
    allowed = _unit(
        service.evaluate(
            _request(fixture, acknowledgements=[acknowledgement])
        ).document
    )["duplicate"]

    assert blocked["status"] == "block"
    assert blocked["prior_run_ids"] == ["prior-standard-001"]
    assert allowed["status"] == "warn"
    assert allowed["reason_codes"] == ["duplicate_acknowledged"]


def test_validation_run_is_excluded_from_duplicate_truth(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    catalog = _matching_catalog(fixture, validation_run=True)

    duplicate = _unit(
        _service(fixture, catalog_loader=lambda: catalog)
        .evaluate(_request(fixture))
        .document
    )["duplicate"]

    assert duplicate["status"] == "pass"
    assert duplicate["exact_match_count"] == 0


def test_strategy_symbol_or_range_drift_clears_duplicate_identity(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    catalog = _matching_catalog(fixture)
    shifted_end = fixture.range_end - timedelta(minutes=1)

    duplicate = _unit(
        _service(fixture, catalog_loader=lambda: catalog)
        .evaluate(_request(fixture, range_end=shifted_end))
        .document
    )["duplicate"]

    assert duplicate["status"] == "pass"
    assert duplicate["exact_match_count"] == 0


def test_unproven_or_unmigrated_duplicate_truth_is_unknown_not_zero(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    unproven = _matching_catalog(fixture, lookup_available=False)
    unproven_doc = _unit(
        _service(fixture, catalog_loader=lambda: unproven)
        .evaluate(_request(fixture))
        .document
    )["duplicate"]

    def migration_required() -> RunReferenceCatalog:
        raise RunReferenceMigrationRequired("fixture migration required")

    unmigrated_doc = _unit(
        _service(fixture, catalog_loader=migration_required)
        .evaluate(_request(fixture))
        .document
    )["duplicate"]

    assert unproven_doc["status"] == "unknown"
    assert unproven_doc["count_known"] is False
    assert unproven_doc["exact_match_count"] is None
    assert unmigrated_doc["status"] == "unknown"
    assert unmigrated_doc["reason_codes"] == ["duplicate_index_unavailable"]


def test_catalog_integrity_failure_is_structured_unknown(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    def corrupt_catalog() -> RunReferenceCatalog:
        raise RunIndexIntegrityError("fixture proof mismatch")

    duplicate = _unit(
        _service(fixture, catalog_loader=corrupt_catalog)
        .evaluate(_request(fixture))
        .document
    )["duplicate"]

    assert duplicate["status"] == "unknown"
    assert duplicate["reason_codes"] == ["duplicate_catalog_integrity_error"]


def test_stale_exact_acknowledgement_blocks_when_count_is_known_zero(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)
    acknowledgement = {
        "strategy_version": fixture.strategy_id,
        "symbol": "NQ",
        "range_start": fixture.range_start.isoformat(),
        "range_end": fixture.range_end.isoformat(),
    }

    duplicate = _unit(
        _service(fixture)
        .evaluate(_request(fixture, acknowledgements=[acknowledgement]))
        .document
    )["duplicate"]

    assert duplicate["status"] == "block"
    assert duplicate["reason_codes"] == ["duplicate_acknowledgement_stale"]


def test_precheck_contract_has_exact_keys_types_and_allowed_enums(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    document = _service(fixture).evaluate(_request(fixture)).document
    unit = _unit(document)

    assert set(document) == TOP_LEVEL_KEYS
    assert set(unit) == UNIT_KEYS
    assert set(unit["coverage"]) == COVERAGE_KEYS
    assert set(unit["warmup"]) == WARMUP_KEYS
    assert set(unit["duplicate"]) == DUPLICATE_KEYS
    assert document["schema"] == "backtest_precheck.v1"
    assert document["overall_status"] in {"pass", "warn", "block", "unknown"}
    assert unit["status"] in {"pass", "warn", "block", "unknown"}
    assert type(document["can_submit"]) is bool
    assert type(document["unit_count"]) is int
    assert type(document["checked_at"]) is str
    assert type(document["units"]) is list
    assert document["unit_count"] == len(document["units"])
    assert all(
        type(unit[key]) is str
        for key in (
            "strategy_version",
            "symbol",
            "contract_id",
            "session_name",
            "range_start",
            "range_end",
        )
    )
    assert all(type(code) is str for code in unit["reason_codes"])
    coverage = unit["coverage"]
    assert coverage["status"] in {"pass", "warn", "block", "unknown"}
    assert type(coverage["requested_trading_date_count"]) is int
    assert type(coverage["admitted_trading_date_count"]) is int
    for key in COVERAGE_KEYS - {
        "status",
        "requested_trading_date_count",
        "admitted_trading_date_count",
    }:
        assert type(coverage[key]) is list
        assert all(type(value) is str for value in coverage[key])
    warmup = unit["warmup"]
    assert warmup["status"] in {"pass", "warn", "block", "unknown"}
    for key in (
        "required_prior_trading_date_count",
        "available_prior_trading_date_count",
        "evaluable_trading_date_count",
    ):
        assert warmup[key] is None or type(warmup[key]) is int
    for key in ("first_evaluable_trading_date", "suggested_range_start"):
        assert warmup[key] is None or type(warmup[key]) is str
    assert type(warmup["reason_codes"]) is list
    assert all(type(code) is str for code in warmup["reason_codes"])
    duplicate = unit["duplicate"]
    assert duplicate["status"] in {"pass", "warn", "block", "unknown"}
    assert type(duplicate["count_known"]) is bool
    assert (
        duplicate["exact_match_count"] is None
        or type(duplicate["exact_match_count"]) is int
    )
    assert type(duplicate["prior_run_ids"]) is list
    assert all(type(run_id) is str for run_id in duplicate["prior_run_ids"])
    assert (
        duplicate["unindexed_candidate_count"] is None
        or type(duplicate["unindexed_candidate_count"]) is int
    )
    assert type(duplicate["acknowledged"]) is bool
    assert type(duplicate["reason_codes"]) is list
    assert all(type(code) is str for code in duplicate["reason_codes"])


def test_unauthorized_symbol_remains_an_exact_blocked_matrix_cell(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    registry = _tiny_registry(include_ym=True)
    store = StrategyStore(root=data_root / "strategies")
    strategy_id = _import_confirmed(store)
    contract = registry.by_symbol("YM")
    CanonicalStore(data_root / "market").append(_minute_bars(contract, RUN_DATE))
    prior_dates = _weekdays_before(RUN_DATE, 95)
    CanonicalStore(data_root / "market-daily").append(
        _daily_bar(contract, day, ordinal=index)
        for index, day in enumerate((*prior_dates, RUN_DATE))
    )
    bounds = session_bounds_for_trading_date(
        contract,
        RUN_DATE,
        session_name="eth",
    )
    assert bounds is not None
    fixture = AdmissionFixture(
        data_root=data_root,
        registry=registry,
        store=store,
        strategy_id=strategy_id,
        contract=contract,
        run_dates=(RUN_DATE,),
        range_start=bounds[0],
        range_end=bounds[1],
    )

    document = _service(fixture).evaluate(
        _request(fixture, symbols=["YM"])
    ).document
    unit = _unit(document)

    assert document["unit_count"] == 1
    assert document["overall_status"] == "block"
    assert document["can_submit"] is False
    assert unit["symbol"] == "YM"
    assert unit["status"] == "block"
    assert unit["reason_codes"] == ["strategy_symbol_not_authorized"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"validation_run": False},
        {"quantity": 1},
        {"session_name": "eth"},
        {"skip_nautilus_replay": False},
        {"strategy_overrides": {}},
    ],
)
def test_standard_request_forbids_engineering_and_override_fields(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    fixture = _build_fixture(tmp_path)
    payload = _request(fixture).to_storage_dict()
    payload.update(mutation)

    with pytest.raises(ValueError):
        P4BatchRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("execution_assumptions", "initial_capital_usd"), "100000"),
        (
            (
                "execution_assumptions",
                "slippage_ticks",
                "breakout_entry",
            ),
            True,
        ),
        (("symbols",), ["nq"]),
        (("strategy_versions",), [" strategy-0001"]),
    ],
)
def test_standard_request_rejects_primitive_and_canonical_identity_drift(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
) -> None:
    fixture = _build_fixture(tmp_path)
    payload = _request(fixture).to_storage_dict()
    cursor: dict[str, Any] = payload
    for key in path[:-1]:
        nested = cursor[key]
        assert isinstance(nested, dict)
        cursor = nested
    cursor[path[-1]] = value

    with pytest.raises(ValueError):
        P4BatchRequest.model_validate(payload)


def test_each_matrix_cell_keeps_full_capital_and_its_symbol_cost(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    registry = _tiny_registry(include_ym=True)
    store = StrategyStore(root=data_root / "strategies")
    strategy_id = _import_confirmed(store, include_ym=True)
    run_dates = (RUN_DATE,)
    for symbol in ("NQ", "YM"):
        contract = registry.by_symbol(symbol)
        CanonicalStore(data_root / "market").append(
            _minute_bars(contract, RUN_DATE)
        )
        prior = _weekdays_before(RUN_DATE, 95)
        CanonicalStore(data_root / "market-daily").append(
            _daily_bar(contract, day, ordinal=index)
            for index, day in enumerate((*prior, RUN_DATE))
        )
    nq_bounds = session_bounds_for_trading_date(
        registry.by_symbol("NQ"),
        RUN_DATE,
        session_name="eth",
    )
    assert nq_bounds is not None
    fixture = AdmissionFixture(
        data_root=data_root,
        registry=registry,
        store=store,
        strategy_id=strategy_id,
        contract=registry.by_symbol("NQ"),
        run_dates=run_dates,
        range_start=nq_bounds[0],
        range_end=nq_bounds[1],
    )
    request = _request(
        fixture,
        symbols=["NQ", "YM"],
        commission_by_symbol={"NQ": 2.5, "YM": 3.75},
    )

    evaluation = _service(fixture).evaluate(request)
    nq = evaluation.plan_for(strategy_id, "NQ")
    ym = evaluation.plan_for(strategy_id, "YM")

    assert nq.initial_capital_usd == ym.initial_capital_usd == 100_000
    assert nq.costs.commission_per_side == 2.5
    assert ym.costs.commission_per_side == 3.75
    assert nq.costs.slippage_ticks.model_dump() == {
        "breakout_entry": 1,
        "stop_exit": 2,
        "target_exit": 0,
        "day_end_exit": 1,
    }


def test_prepare_run_excludes_owner_date_from_fingerprint_and_manifest(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(
        tmp_path,
        run_dates=(RUN_DATE, date(2026, 7, 21)),
    )
    first = _minute_bars(fixture.contract, RUN_DATE)
    second = _minute_bars(fixture.contract, date(2026, 7, 21))
    plan_costs = fixture.contract.execution_costs.model_copy(
        update={"commission_per_side": 7.25}
    )

    prepared = prepare_run(
        run_id="p4-plan-fixture",
        strategy_version=fixture.strategy_id,
        contract=fixture.contract,
        session_name="eth",
        range_start=fixture.range_start,
        range_end=fixture.range_end,
        initial_capital=100_000,
        quantity=1,
        canonical_bars=(*first, *second),
        costs=plan_costs,
        additional_excluded_trading_dates=(date(2026, 7, 21),),
    )

    expected = fingerprint_canonical_bars(first)
    assert prepared.bars == first
    assert prepared.manifest.excluded_trading_dates == (date(2026, 7, 21),)
    assert prepared.manifest.data_fingerprint.digest == expected.digest
    assert prepared.manifest.data_fingerprint.bar_count == len(first)
    assert prepared.manifest.costs.commission_per_side == 7.25
    assert prepared.manifest.simulation_precision == "one_minute"


def test_runner_consumes_only_admitted_minutes_and_excludes_native_daily_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excluded_date = date(2026, 7, 21)
    fixture = _build_fixture(
        tmp_path,
        run_dates=(RUN_DATE, excluded_date),
        owner_decisions={excluded_date: "exclude"},
    )
    evaluation = _service(fixture).evaluate(_request(fixture))
    plan = evaluation.plan_for(fixture.strategy_id, "NQ")
    captured_native_dates: list[tuple[date, ...]] = []
    original_builder = runner_mod.build_native_daily_mtf_series

    def capture_native_input(
        contract: ContractSpec,
        native_daily_bars: Any,
        *,
        session_name: str,
    ) -> Any:
        bars = tuple(native_daily_bars)
        captured_native_dates.append(
            tuple(
                trading_date_for_session_start(
                    contract,
                    bar.timestamp,
                    session_name="eth",
                )
                for bar in bars
            )
        )
        return original_builder(
            contract,
            bars,
            session_name=session_name,
        )

    monkeypatch.setattr(
        runner_mod,
        "build_native_daily_mtf_series",
        capture_native_input,
    )
    runner = BacktestRunner(
        canonical_store=CanonicalStore(fixture.data_root / "market"),
        daily_canonical_store=CanonicalStore(fixture.data_root / "market-daily"),
        run_store=SqliteRunStore(tmp_path / "unused-runs.sqlite3"),
        result_exporter=ResultExporter(tmp_path / "unused-results"),
        quality_reports_root=None,
    )

    replay = runner._replay(
        contract=plan.contract,
        config=BacktestRunConfig(
            run_id="p4-owner-exclude-replay",
            strategy_version=plan.strategy_version,
            session_name=plan.resolved_strategy.session_name,
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
            verify_nautilus_replay=False,
            validation_run=False,
            strategy_spec=plan.resolved_strategy.spec,
            strategy_binding=plan.resolved_strategy.binding,
        ),
    )

    assert replay.raw_bar_count == len(_minute_bars(fixture.contract, RUN_DATE)) * 2
    assert replay.admitted_bar_count == len(_minute_bars(fixture.contract, RUN_DATE))
    assert replay.result.manifest.excluded_trading_dates == (excluded_date,)
    assert replay.result.manifest.data_fingerprint.bar_count == replay.admitted_bar_count
    assert captured_native_dates
    assert all(excluded_date not in dates for dates in captured_native_dates)
