"""P3-A exact trading-day and native-daily coverage facts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import yaml

from futures_research.api import data_catalog
from futures_research.api.data_catalog import (
    DataPaths,
    list_coverage,
    upsert_owner_blacklist_entry,
)
from futures_research.api.main import app
from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.coverage import (
    build_contract_coverage_facts,
    compute_trading_day_coverage,
)
from futures_research.data.daily_download import DailyDataIngestionService
from futures_research.data.models import CanonicalBar, QualityIssue, QualityReport
from futures_research.data.sessions import session_bounds_for_trading_date
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT

TRADING_FACT_FIELDS = {
    "first_trading_date",
    "last_trading_date",
    "trading_date_count",
    "complete_trading_date_count",
    "problem_trading_date_count",
    "pending_problem_trading_date_count",
    "owner_trusted_problem_trading_date_count",
    "owner_excluded_trading_date_count",
    "complete_trading_dates",
    "problem_trading_dates",
    "pending_problem_trading_dates",
    "owner_trusted_problem_trading_dates",
    "owner_excluded_trading_dates",
    "longest_complete_segment",
}
TRADING_KNOWN_KEYS = {"schema", "status", "session_name"} | TRADING_FACT_FIELDS
TRADING_UNAVAILABLE_KEYS = TRADING_KNOWN_KEYS | {"error_code"}
NATIVE_FACT_FIELDS = {
    "available_first_trading_date",
    "available_last_trading_date",
    "available_trading_date_count",
    "within_minute_range_trading_date_count",
    "required_minute_range_trading_date_count",
    "missing_within_minute_range_trading_dates",
}
NATIVE_KNOWN_KEYS = {"schema", "status"} | NATIVE_FACT_FIELDS
NATIVE_UNAVAILABLE_KEYS = NATIVE_KNOWN_KEYS | {"error_code"}


def _write_tiny_contract_config(root: Path) -> tuple[Path, ContractSpec]:
    raw = yaml.safe_load(
        (PROJECT_ROOT / "config" / "contracts.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    nq = raw["contracts"]["NQ"]
    nq["sessions"] = {"eth": {"start": "09:00", "end": "09:04"}}
    raw["contracts"] = {"NQ": nq}
    path = root / "config" / "contracts.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path, ContractRegistry.from_yaml(path).by_symbol("NQ")


def _minute_bars(
    contract: ContractSpec,
    trading_date: date,
    *,
    omit: frozenset[int] = frozenset(),
    source: str = "fixture",
) -> list[CanonicalBar]:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    count = int((bounds[1] - bounds[0]) / timedelta(minutes=1))
    return [
        CanonicalBar(
            timestamp=bounds[0] + index * timedelta(minutes=1),
            open=100.0,
            high=100.25,
            low=99.75,
            close=100.0,
            volume=100,
            contract_id=contract.contract_id,
            source=source,
        )
        for index in range(count)
        if index not in omit
    ]


def _native_bar(contract: ContractSpec, trading_date: date) -> CanonicalBar:
    bounds = session_bounds_for_trading_date(
        contract,
        trading_date,
        session_name="eth",
    )
    assert bounds is not None
    return CanonicalBar(
        timestamp=bounds[0],
        open=100.0,
        high=100.25,
        low=99.75,
        close=100.0,
        volume=1_000,
        contract_id=contract.contract_id,
        source="ib_native_daily",
    )


def _row(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["contracts"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _tree_inventory(root: Path) -> dict[str, tuple[int, str]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_corrupt_partition(root: Path, contract_id: str) -> Path:
    path = (
        root
        / f"contract_id={contract_id}"
        / "year=2026"
        / "month=07"
        / "bars.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not parquet")
    return path


def test_four_weekday_facts_overlay_longest_and_native_intersection(
    tmp_path: Path,
) -> None:
    config, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    minute_store = CanonicalStore(paths.market_root)
    minute_store.append(
        [
            *_minute_bars(contract, date(2026, 7, 20)),
            *_minute_bars(
                contract,
                date(2026, 7, 21),
                omit=frozenset({1}),
            ),
            *_minute_bars(contract, date(2026, 7, 23)),
        ]
    )
    CanonicalStore(paths.daily_market_root).append(
        [
            _native_bar(contract, day)
            for day in (
                date(2026, 7, 17),
                date(2026, 7, 20),
                date(2026, 7, 21),
                date(2026, 7, 23),
                date(2026, 7, 24),
            )
        ]
    )
    upsert_owner_blacklist_entry(
        contract_id=contract.contract_id,
        trading_date="2026-07-21",
        decision="trust",
        paths=paths,
    )
    upsert_owner_blacklist_entry(
        contract_id=contract.contract_id,
        trading_date="2026-07-23",
        decision="exclude",
        paths=paths,
    )

    payload = list_coverage(paths, contracts_config=config)
    row = _row(payload)
    trading = row["trading_day_coverage"]
    native = row["native_daily_coverage"]

    assert set(trading) == TRADING_KNOWN_KEYS
    assert trading == {
        "schema": "trading_day_coverage.v1",
        "status": "known",
        "session_name": "eth",
        "first_trading_date": "2026-07-20",
        "last_trading_date": "2026-07-23",
        "trading_date_count": 4,
        "complete_trading_date_count": 2,
        "problem_trading_date_count": 2,
        "pending_problem_trading_date_count": 1,
        "owner_trusted_problem_trading_date_count": 1,
        "owner_excluded_trading_date_count": 1,
        "complete_trading_dates": ["2026-07-20", "2026-07-23"],
        "problem_trading_dates": ["2026-07-21", "2026-07-22"],
        "pending_problem_trading_dates": ["2026-07-22"],
        "owner_trusted_problem_trading_dates": ["2026-07-21"],
        "owner_excluded_trading_dates": ["2026-07-23"],
        "longest_complete_segment": {
            "start_trading_date": "2026-07-20",
            "end_trading_date": "2026-07-20",
            "trading_date_count": 1,
        },
    }
    assert set(native) == NATIVE_KNOWN_KEYS
    assert native == {
        "schema": "native_daily_coverage.v1",
        "status": "known",
        "available_first_trading_date": "2026-07-17",
        "available_last_trading_date": "2026-07-24",
        "available_trading_date_count": 5,
        "within_minute_range_trading_date_count": 3,
        "required_minute_range_trading_date_count": 4,
        "missing_within_minute_range_trading_dates": ["2026-07-22"],
    }

    assert row["symbol"] == "NQ"
    assert row["contract_id"] == contract.contract_id
    assert row["bar_count"] == 11
    assert trading["trading_date_count"] == (
        trading["complete_trading_date_count"]
        + trading["problem_trading_date_count"]
    )
    assert native["required_minute_range_trading_date_count"] == (
        native["within_minute_range_trading_date_count"]
        + len(native["missing_within_minute_range_trading_dates"])
    )


def test_weekend_is_not_a_candidate_and_does_not_break_a_segment(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        *_minute_bars(contract, date(2026, 7, 17)),
        *_minute_bars(contract, date(2026, 7, 20)),
    ]

    projection = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[],
    )

    assert projection.candidate_dates == (
        date(2026, 7, 17),
        date(2026, 7, 20),
    )
    assert projection.document["complete_trading_dates"] == [
        "2026-07-17",
        "2026-07-20",
    ]
    assert projection.document["longest_complete_segment"] == {
        "start_trading_date": "2026-07-17",
        "end_trading_date": "2026-07-20",
        "trading_date_count": 2,
    }


def test_longest_segment_tie_uses_earliest_and_known_empty_is_distinct(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        *_minute_bars(contract, date(2026, 7, 20)),
        *_minute_bars(contract, date(2026, 7, 21)),
        *_minute_bars(contract, date(2026, 7, 23)),
        *_minute_bars(contract, date(2026, 7, 24)),
    ]
    tied = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[],
    ).document
    empty = compute_trading_day_coverage(
        contract,
        [],
        owner_entries=[],
    ).document
    single = compute_trading_day_coverage(
        contract,
        _minute_bars(contract, date(2026, 7, 20)),
        owner_entries=[],
    ).document

    assert tied["longest_complete_segment"] == {
        "start_trading_date": "2026-07-20",
        "end_trading_date": "2026-07-21",
        "trading_date_count": 2,
    }
    assert empty["status"] == "known"
    assert empty["first_trading_date"] is None
    assert empty["last_trading_date"] is None
    assert empty["trading_date_count"] == 0
    assert empty["complete_trading_dates"] == []
    assert empty["problem_trading_dates"] == []
    assert empty["longest_complete_segment"] is None
    assert single["longest_complete_segment"] == {
        "start_trading_date": "2026-07-20",
        "end_trading_date": "2026-07-20",
        "trading_date_count": 1,
    }


def test_excluded_complete_day_cuts_the_longest_segment(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        bar
        for trading_date in (
            date(2026, 7, 20),
            date(2026, 7, 21),
            date(2026, 7, 22),
            date(2026, 7, 23),
            date(2026, 7, 24),
        )
        for bar in _minute_bars(contract, trading_date)
    ]

    document = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[
            {"trading_date": "2026-07-22", "decision": "exclude"}
        ],
    ).document

    assert document["complete_trading_date_count"] == 5
    assert document["owner_excluded_trading_dates"] == ["2026-07-22"]
    assert document["longest_complete_segment"] == {
        "start_trading_date": "2026-07-20",
        "end_trading_date": "2026-07-21",
        "trading_date_count": 2,
    }


class _SeverityChecker:
    def check(
        self,
        contract_id: str,
        bars: Sequence[CanonicalBar],
        *,
        expected_timestamps: Iterable[datetime] | None = None,
        reference_bars: Sequence[CanonicalBar] | None = None,
        reference_interval: timedelta = timedelta(minutes=5),
        tick_size: float | None = None,
    ) -> QualityReport:
        del expected_timestamps, reference_bars, reference_interval, tick_size
        severity: Literal["warning", "info"] = (
            "warning" if bars[0].source == "warning" else "info"
        )
        return QualityReport(
            contract_id=contract_id,
            total_bars=len(bars),
            checks={"fixture": "completed"},
            issues=[
                QualityIssue(
                    category="anomaly",
                    severity=severity,
                    code=f"{severity}_fixture",
                    message="fixture",
                )
            ],
        )


def test_warning_is_problem_info_only_is_complete_and_trust_does_not_rename(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = [
        *_minute_bars(
            contract,
            date(2026, 7, 20),
            source="info",
        ),
        *_minute_bars(
            contract,
            date(2026, 7, 21),
            source="warning",
        ),
    ]

    document = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[
            {"trading_date": "2026-07-21", "decision": "trust"}
        ],
        quality_checker=_SeverityChecker(),
    ).document

    assert document["complete_trading_dates"] == ["2026-07-20"]
    assert document["problem_trading_dates"] == ["2026-07-21"]
    assert document["owner_trusted_problem_trading_dates"] == ["2026-07-21"]
    assert document["pending_problem_trading_dates"] == []


def test_real_warning_from_tick_alignment_is_problem(tmp_path: Path) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    bars = _minute_bars(contract, date(2026, 7, 20))
    bars[0] = bars[0].model_copy(update={"open": 100.1})

    document = compute_trading_day_coverage(
        contract,
        bars,
        owner_entries=[],
    ).document

    assert document["complete_trading_dates"] == []
    assert document["problem_trading_dates"] == ["2026-07-20"]


def test_overnight_minute_uses_exchange_end_label_not_utc_date(
    contracts_registry: ContractRegistry,
) -> None:
    contract = contracts_registry.by_symbol("NQ")
    bounds = session_bounds_for_trading_date(
        contract,
        date(2026, 7, 20),
        session_name="eth",
    )
    assert bounds is not None
    bar = CanonicalBar(
        timestamp=bounds[0],
        open=100.0,
        high=100.25,
        low=99.75,
        close=100.0,
        volume=100,
        contract_id=contract.contract_id,
        source="fixture",
    )

    document = compute_trading_day_coverage(
        contract,
        [bar],
        owner_entries=[],
    ).document

    assert bounds[0].date() == date(2026, 7, 19)
    assert document["first_trading_date"] == "2026-07-20"
    assert document["last_trading_date"] == "2026-07-20"
    assert document["problem_trading_dates"] == ["2026-07-20"]


@pytest.mark.parametrize(
    "timezone_name",
    ["Pacific/Kiritimati", "America/Adak"],
)
def test_coverage_labels_ignore_process_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    monkeypatch.setenv("TZ", timezone_name)

    document = compute_trading_day_coverage(
        contract,
        _minute_bars(contract, date(2026, 7, 20)),
        owner_entries=[],
    ).document

    assert document["first_trading_date"] == "2026-07-20"
    assert document["last_trading_date"] == "2026-07-20"
    assert document["complete_trading_dates"] == ["2026-07-20"]


def test_minute_partition_unreadable_is_unavailable_with_null_facts_and_no_write(
    tmp_path: Path,
) -> None:
    config, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    _write_corrupt_partition(paths.market_root, contract.contract_id)
    before = _tree_inventory(tmp_path)

    row = _row(list_coverage(paths, contracts_config=config))

    assert _tree_inventory(tmp_path) == before
    trading = row["trading_day_coverage"]
    native = row["native_daily_coverage"]
    assert set(trading) == TRADING_UNAVAILABLE_KEYS
    assert trading["status"] == "unavailable"
    assert trading["error_code"] == "minute_data_unreadable"
    assert all(trading[field] is None for field in TRADING_FACT_FIELDS)
    assert set(native) == NATIVE_UNAVAILABLE_KEYS
    assert native["error_code"] == "minute_coverage_dependency_unavailable"
    assert all(native[field] is None for field in NATIVE_FACT_FIELDS)
    assert row["symbol"] == "NQ"
    assert row["contract_id"] == contract.contract_id


def test_unmapped_minute_makes_both_projections_unavailable(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    bounds = session_bounds_for_trading_date(
        contract,
        date(2026, 7, 20),
        session_name="eth",
    )
    assert bounds is not None
    CanonicalStore(paths.market_root).append(
        [
            CanonicalBar(
                timestamp=bounds[0] - timedelta(minutes=1),
                open=100.0,
                high=100.25,
                low=99.75,
                close=100.0,
                volume=100,
                contract_id=contract.contract_id,
                source="fixture",
            )
        ]
    )

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["error_code"] == "minute_timestamp_unmapped"
    assert native["error_code"] == "minute_coverage_dependency_unavailable"


def test_missing_native_root_is_known_zero_not_unavailable(tmp_path: Path) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["status"] == "known"
    assert native == {
        "schema": "native_daily_coverage.v1",
        "status": "known",
        "available_first_trading_date": None,
        "available_last_trading_date": None,
        "available_trading_date_count": 0,
        "within_minute_range_trading_date_count": 0,
        "required_minute_range_trading_date_count": 1,
        "missing_within_minute_range_trading_dates": ["2026-07-20"],
    }


def test_known_empty_minute_and_missing_native_root_are_both_known_zero(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["status"] == "known"
    assert trading["trading_date_count"] == 0
    assert trading["complete_trading_dates"] == []
    assert native["status"] == "known"
    assert native["available_trading_date_count"] == 0
    assert native["within_minute_range_trading_date_count"] == 0
    assert native["required_minute_range_trading_date_count"] == 0
    assert native["missing_within_minute_range_trading_dates"] == []


def test_corrupt_native_partition_only_downgrades_native_projection(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    _write_corrupt_partition(paths.daily_market_root, contract.contract_id)

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["status"] == "known"
    assert native["status"] == "unavailable"
    assert native["error_code"] == "native_daily_unreadable"
    assert all(native[field] is None for field in NATIVE_FACT_FIELDS)


def test_non_session_start_native_timestamp_is_unavailable_not_dropped(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    bad = _native_bar(contract, date(2026, 7, 20)).model_copy(
        update={"timestamp": _native_bar(contract, date(2026, 7, 20)).timestamp
        + timedelta(minutes=1)}
    )
    CanonicalStore(paths.daily_market_root).append([bad])

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["status"] == "known"
    assert native["status"] == "unavailable"
    assert native["error_code"] == "native_daily_timestamp_unmapped"
    assert all(native[field] is None for field in NATIVE_FACT_FIELDS)


def test_missing_session_definition_is_nested_unavailable(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    without_eth = contract.model_copy(update={"sessions": {}})
    paths = DataPaths(data_root=tmp_path / "data")

    trading, native = build_contract_coverage_facts(
        without_eth,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[],
    )

    assert trading["error_code"] == "session_definition_unavailable"
    assert native["error_code"] == "minute_coverage_dependency_unavailable"


def test_invalid_owner_overlay_fails_closed_without_raw_error(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    paths = DataPaths(data_root=tmp_path / "data")

    trading, native = build_contract_coverage_facts(
        contract,
        minute_store=CanonicalStore(paths.market_root),
        native_daily_store=CanonicalStore(paths.daily_market_root),
        owner_entries=[{"trading_date": " bad ", "decision": "exclude"}],
    )

    assert trading["error_code"] == "coverage_computation_failed"
    assert native["error_code"] == "minute_coverage_dependency_unavailable"
    assert "bad" not in str(trading)


@pytest.mark.asyncio
async def test_coverage_get_is_byte_read_only_and_calls_no_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    _, contract = _write_tiny_contract_config(project)
    paths = DataPaths(data_root=project / "data")
    CanonicalStore(paths.market_root).append(
        _minute_bars(contract, date(2026, 7, 20))
    )
    CanonicalStore(paths.daily_market_root).append(
        [_native_bar(contract, date(2026, 7, 20))]
    )
    before = _tree_inventory(project)
    calls = {"append": 0, "ingest": 0, "download": 0}

    def forbidden_append(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["append"] += 1
        raise AssertionError("coverage GET must not append canonical data")

    def forbidden_ingest(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["ingest"] += 1
        raise AssertionError("coverage GET must not ingest data")

    def forbidden_download(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls["download"] += 1
        raise AssertionError("coverage GET must not enqueue downloads")

    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", project)
    monkeypatch.setattr(CanonicalStore, "append", forbidden_append)
    monkeypatch.setattr(DailyDataIngestionService, "ingest", forbidden_ingest)
    monkeypatch.setattr(
        data_catalog,
        "enqueue_download_job",
        forbidden_download,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/data/coverage")

    assert response.status_code == 200
    row = _row(response.json())
    assert row["trading_day_coverage"]["status"] == "known"
    assert row["native_daily_coverage"]["status"] == "known"
    assert calls == {"append": 0, "ingest": 0, "download": 0}
    assert _tree_inventory(project) == before


def test_exact_nested_primitive_types_and_sorted_unique_arrays(
    tmp_path: Path,
) -> None:
    _, contract = _write_tiny_contract_config(tmp_path / "project")
    projection = compute_trading_day_coverage(
        contract,
        [
            *_minute_bars(contract, date(2026, 7, 20)),
            *_minute_bars(contract, date(2026, 7, 21)),
        ],
        owner_entries=[],
    ).document

    assert set(projection) == TRADING_KNOWN_KEYS
    for key in (
        "trading_date_count",
        "complete_trading_date_count",
        "problem_trading_date_count",
        "pending_problem_trading_date_count",
        "owner_trusted_problem_trading_date_count",
        "owner_excluded_trading_date_count",
    ):
        assert type(projection[key]) is int
    for key in (
        "complete_trading_dates",
        "problem_trading_dates",
        "pending_problem_trading_dates",
        "owner_trusted_problem_trading_dates",
        "owner_excluded_trading_dates",
    ):
        values = projection[key]
        assert isinstance(values, list)
        assert values == sorted(set(values))
        assert all(type(value) is str for value in values)
    segment = projection["longest_complete_segment"]
    assert isinstance(segment, dict)
    assert set(segment) == {
        "start_trading_date",
        "end_trading_date",
        "trading_date_count",
    }
    assert type(segment["start_trading_date"]) is str
    assert type(segment["end_trading_date"]) is str
    assert type(segment["trading_date_count"]) is int
