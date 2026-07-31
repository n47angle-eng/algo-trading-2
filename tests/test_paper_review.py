"""P6 Stage B ledger-resource and immutable review-state tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event
from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError

from futures_research.api import paper_review as paper_review_module
from futures_research.api.paper_provisioning import (
    OneOffPaperProvisioningAuthorizationPolicy,
)
from futures_research.api.paper_review import (
    PaperLedgerOrigin,
    PaperReviewBuilderRegistry,
    PaperReviewCreateRequest,
    PaperReviewDomainError,
    PaperReviewErrorPayload,
    PaperReviewService,
    review_request_fingerprint,
)
from futures_research.api.paper_traders import (
    IsolatedPaperExternalReadinessProvider,
    PaperTrader,
    PaperTraderCreateRequest,
    PaperTraderStore,
    create_paper_trader,
)
from futures_research.api.promotion_decisions import (
    PromotionDecisionStore,
    source_from_result_snapshot,
)
from futures_research.api.results_catalog import ResultsCatalog
from futures_research.data.contracts import ContractRegistry

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_ROOT = _PROJECT_ROOT / "data" / "backtests" / "results"
_TARGET_RUN_ID = "nq-20260728-standard-365adf"
_TARGET_RESULT_SHA = (
    "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7"
)
_TARGET_STRATEGY_SHA = (
    "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
)
_TRADER_CREATED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
_CAPTURED_AT = datetime(2026, 7, 29, 1, 2, 3, 4, tzinfo=UTC)
_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
_SNAPSHOT_ID = "paper-review-00000000000040008000000000000301"
_BUILDER_ID = "00000000-0000-4000-8000-000000000201"
_REVIEW_TABLES = (
    "paper_review_requests",
    "paper_review_status_events",
    "paper_review_ready_artifacts",
    "paper_review_failures",
)


@dataclass(frozen=True, slots=True)
class _Context:
    store: PaperTraderStore
    service: PaperReviewService
    catalog: ResultsCatalog
    registry: ContractRegistry
    decisions: PromotionDecisionStore
    trader: PaperTrader
    trader_ids: Iterator[str]
    account_ids: Iterator[str]
    ledger_ids: Iterator[str]


class _CommitBeforeActivateRegistry(PaperReviewBuilderRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.activate_entered = Event()
        self.release_activate = Event()

    def activate(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
    ) -> None:
        self.activate_entered.set()
        if not self.release_activate.wait(timeout=5):
            raise AssertionError("activate barrier was not released")
        super().activate(
            store_key=store_key,
            request_id=request_id,
            builder_instance_id=builder_instance_id,
        )


class _FailingActivationRegistry(PaperReviewBuilderRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.activate_calls = 0

    def activate(
        self,
        *,
        store_key: str,
        request_id: str,
        builder_instance_id: str,
    ) -> None:
        self.activate_calls += 1
        raise RuntimeError("injected activate failure")


def _ids(prefix: str, start: int) -> Iterator[str]:
    for value in range(start, start + 100):
        yield f"{prefix}{value:032x}"


def _create_body(request_id: str) -> PaperTraderCreateRequest:
    return PaperTraderCreateRequest(
        schema="paper_trader_create_request.v1",
        request_id=request_id,
        selection={
            "strategy_id": "strategy-0003",
            "content_sha256": _TARGET_STRATEGY_SHA,
            "contract_id": "NQ-202609-CME",
            "baseline_run_id": _TARGET_RUN_ID,
            "baseline_result_sha256": _TARGET_RESULT_SHA,
        },
    )


def _provision_trader(
    *,
    store: PaperTraderStore,
    catalog: ResultsCatalog,
    registry: ContractRegistry,
    decisions: PromotionDecisionStore,
    request_id: str,
) -> PaperTrader:
    body = _create_body(request_id)
    record, created = create_paper_trader(
        body=body,
        catalog=catalog,
        registry=registry,
        eligibility_store=decisions,
        readiness_provider=IsolatedPaperExternalReadinessProvider(
            checked_at=_TRADER_CREATED_AT
        ),
        authorization_policy=OneOffPaperProvisioningAuthorizationPolicy(
            operation_id=f"paper-provision-{request_id.replace('-', '')}",
            authorized_selection=body.selection,
            authorized_at=_TRADER_CREATED_AT,
            clock=lambda: _TRADER_CREATED_AT,
        ),
        trader_store=store,
    )
    assert created is True
    return record


@pytest.fixture
def context(tmp_path: Path) -> _Context:
    catalog = ResultsCatalog(_RESULTS_ROOT)
    registry = ContractRegistry.from_yaml(
        _PROJECT_ROOT / "config" / "contracts.yaml"
    )
    decisions = PromotionDecisionStore(
        tmp_path / "promotion.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        decision_id_factory=lambda: "promotion-" + ("9" * 32),
    )
    snapshot = catalog.get_verified_snapshot(_TARGET_RUN_ID)
    decisions.append(
        request_id="stage-b-phase-two-eligibility",
        request_payload_sha256=sha256(
            b"stage-b-phase-two-eligibility"
        ).hexdigest(),
        decision="use",
        reason="P6 Stage B Phase 2 temp authority",
        source=source_from_result_snapshot(
            snapshot,
            expected_run_id=_TARGET_RUN_ID,
        ),
    )
    trader_ids = _ids("trader-", 1)
    account_ids = _ids("paper-account-", 101)
    ledger_ids = _ids("paper-ledger-", 201)
    store = PaperTraderStore(
        tmp_path / "paper" / "paper-traders.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        trader_id_factory=lambda: next(trader_ids),
        account_id_factory=lambda: next(account_ids),
        ledger_origin_id_factory=lambda: next(ledger_ids),
    )
    trader = _provision_trader(
        store=store,
        catalog=catalog,
        registry=registry,
        decisions=decisions,
        request_id="00000000-0000-4000-8000-000000000001",
    )
    service = PaperReviewService(
        store,
        clock=lambda: _CAPTURED_AT,
        snapshot_id_factory=lambda: _SNAPSHOT_ID,
        builder_instance_id_factory=lambda: _BUILDER_ID,
        builder_registry=PaperReviewBuilderRegistry(),
    )
    return _Context(
        store=store,
        service=service,
        catalog=catalog,
        registry=registry,
        decisions=decisions,
        trader=trader,
        trader_ids=trader_ids,
        account_ids=account_ids,
        ledger_ids=ledger_ids,
    )


def _review_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed test names
            ).fetchone()[0]
            for table in _REVIEW_TABLES
        }


def _db_facts(path: Path) -> tuple[bytes, int, dict[str, int], tuple[bool, ...]]:
    return (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        _review_counts(path),
        tuple(Path(f"{path}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")),
    )


def _accept(
    context: _Context,
    *,
    request_id: str = _REQUEST_ID,
) -> tuple[object, bool]:
    return context.service.accept_review_snapshot(
        trader_id=context.trader.trader_id,
        body=PaperReviewCreateRequest(
            schema="paper_review_create_request.v1",
            request_id=request_id,
        ),
    )


def _drop_mutate_restore(
    path: Path,
    trigger_names: tuple[str, ...],
    mutate: Callable[[sqlite3.Connection], None],
) -> None:
    with sqlite3.connect(path) as connection:
        trigger_sql = connection.execute(
            f"""
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN ({",".join("?" for _name in trigger_names)})
            ORDER BY name
            """,
            trigger_names,
        ).fetchall()
        assert len(trigger_sql) == len(trigger_names)
        for name, _sql in trigger_sql:
            connection.execute(f'DROP TRIGGER "{name}"')
        mutate(connection)
        for _name, sql in trigger_sql:
            assert sql is not None
            connection.execute(sql)
        connection.commit()


def _member_paths() -> list[str]:
    return [
        "paper-review.json",
        "paper/ledger-origin.json",
        "paper/trades.json",
        "paper/equity.json",
        "paper/events.json",
        "divergence/expected-actual.json",
        "baseline/result.json",
        f"baseline/trades/{_TARGET_RUN_ID}.json",
        f"baseline/equity/{_TARGET_RUN_ID}.json",
        f"baseline/events/{_TARGET_RUN_ID}.json",
    ]


def _ready_payload(context: _Context) -> dict[str, object]:
    opener = "terminal opener\n"
    return {
        "schema": "paper_review_ready.v1",
        "display_filename": (
            f"paper-review-{context.trader.trader_id}-"
            "20260729T010203000004Z.zip"
        ),
        "artifact_bytes": 12_345,
        "artifact_sha256": "a" * 64,
        "member_count": 10,
        "members": [
            {
                "path": path,
                "bytes": index + 1,
                "sha256": f"{index + 1:064x}",
            }
            for index, path in enumerate(_member_paths())
        ],
        "terminal_opener": {
            "bytes": len(opener.encode("utf-8")),
            "sha256": sha256(opener.encode("utf-8")).hexdigest(),
        },
        "ready_at": "2026-07-29T01:03:00Z",
    }


def _failure_payload(
    *,
    request_id: str = _REQUEST_ID,
    snapshot_id: str = _SNAPSHOT_ID,
) -> dict[str, object]:
    return {
        "schema": "paper_review_error.v1",
        "code": "artifact_build_failed",
        "message": "Synthetic Phase 2 terminal failure.",
        "retryable": False,
        "request_id": request_id,
        "snapshot_id": snapshot_id,
        "progress": {
            "completed_parts": 3,
            "total_parts": 10,
            "current_part": None,
        },
        "issues": [
            {
                "kind": "artifact_build_failed",
                "path": None,
                "source_ref": None,
                "expected_sha256": None,
                "actual_sha256": None,
                "ref_chain": [],
            }
        ],
    }


def test_ledger_origin_producer_matches_exact_target_golden(
    context: _Context,
) -> None:
    resource = context.service.ledger_origin(context.trader.trader_id)
    payload = resource.model_dump(by_alias=True, mode="json")

    assert set(payload) == {
        "schema",
        "ledger_origin_id",
        "trader_id",
        "origin_at",
        "lifecycle",
        "strategy",
        "contract",
        "baseline",
        "account",
        "balances",
        "high_water_marks",
        "positions",
        "orders",
        "safety",
        "readiness_snapshot",
        "interpretation",
    }
    assert payload["schema"] == "paper_ledger_origin.v1"
    assert payload["trader_id"] == context.trader.trader_id
    assert payload["origin_at"] == "2026-07-29T00:00:00Z"
    assert payload["lifecycle"] == {
        "status": "provisioned",
        "engine_status": "not_enabled",
    }
    assert payload["strategy"] == {
        "strategy_id": "strategy-0003",
        "name": "Trend 回踩 18EMA · engineering activation smoke",
        "content_sha256": _TARGET_STRATEGY_SHA,
    }
    assert payload["contract"] == {
        "contract_id": "NQ-202609-CME",
        "exchange": "CME",
        "timezone": "America/Chicago",
    }
    baseline = payload["baseline"]
    assert isinstance(baseline, dict)
    assert set(baseline) == {
        "run_id",
        "result_sha256",
        "range_start",
        "range_end",
        "rejection_count",
        "closest_algorithm",
        "closest_rejection_refs",
        "members",
    }
    assert baseline["run_id"] == _TARGET_RUN_ID
    assert baseline["result_sha256"] == _TARGET_RESULT_SHA
    assert baseline["rejection_count"] == 14
    assert baseline["closest_algorithm"] == "p5_structural_closest.v1"
    assert baseline["closest_rejection_refs"] == [
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
    ]
    assert baseline["members"] == [
        {
            "path": "baseline/result.json",
            "bytes": 8776,
            "sha256": _TARGET_RESULT_SHA,
        },
        {
            "path": f"baseline/trades/{_TARGET_RUN_ID}.json",
            "bytes": 109,
            "sha256": "9f3c4e1cf03d305abd42deadf4f0e940754d8d99cf4e9a52cf74057ed03c6b69",
        },
        {
            "path": f"baseline/equity/{_TARGET_RUN_ID}.json",
            "bytes": 81,
            "sha256": "4b38517f2b73ed6b9a986096162d5a5cb030117db69c14e84a6309edfe898346",
        },
        {
            "path": f"baseline/events/{_TARGET_RUN_ID}.json",
            "bytes": 35136,
            "sha256": "094ebfbc9e010311292ae4005ce58de187a0aae471c32dc18ea53868629c1d30",
        },
    ]
    assert all("payload" not in member for member in baseline["members"])
    assert payload["account"] == {
        "account_id": context.trader.account.account_id,
        "currency": "USD",
        "initial_capital": 100000.0,
        "independent_account": True,
    }
    assert payload["balances"] == {
        "cash": 100000.0,
        "equity": 100000.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
    }
    assert payload["high_water_marks"] == {
        "trades": 0,
        "equity": 1,
        "events": 4,
        "expected_decisions": 0,
        "positions": 0,
        "orders": 0,
    }
    assert payload["positions"] == payload["orders"] == []
    assert payload["safety"] == {
        "state": "not_running",
        "drawdown_r": 0.0,
        "loss_streak": 0,
        "max_drawdown_r": 8,
        "max_losing_streak": 8,
        "blind_minutes": 5,
    }
    assert [
        check["key"] for check in payload["readiness_snapshot"]["checks"]
    ] == [
        "ib_realtime",
        "exchange_calendar",
        "telegram",
        "baseline_integrity",
    ]
    assert payload["interpretation"]["supporting_evidence_refs"] == [
        {
            "path": f"baseline/events/{_TARGET_RUN_ID}.json",
            "evidence_id": ref,
        }
        for ref in (
            "rejection_000014",
            "rejection_000013",
            "rejection_000012",
        )
    ]
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_ledger_read_is_byte_mtime_row_and_sidecar_readonly(
    context: _Context,
) -> None:
    before = _db_facts(context.store.path)

    first = context.service.ledger_origin(context.trader.trader_id)
    second = context.service.ledger_origin(context.trader.trader_id)

    assert first == second
    assert _db_facts(context.store.path) == before


def test_ledger_projection_reads_each_persisted_equity_value(
    context: _Context,
) -> None:
    accessed: set[str] = set()

    class _ObservedRow:
        def __init__(self, row: sqlite3.Row) -> None:
            self._row = row

        def __getitem__(self, key: str) -> object:
            accessed.add(key)
            return self._row[key]

    class _EquityCursor:
        def __init__(self, row: sqlite3.Row) -> None:
            self._row = row

        def fetchall(self) -> list[_ObservedRow]:
            return [_ObservedRow(self._row)]

    class _ObservedConnection:
        def __init__(
            self,
            connection: sqlite3.Connection,
            equity: sqlite3.Row,
        ) -> None:
            self._connection = connection
            self._equity = equity

        def execute(
            self,
            statement: str,
            parameters: tuple[object, ...] = (),
        ) -> object:
            if "FROM paper_ledger_equity_points" in statement:
                return _EquityCursor(self._equity)
            return self._connection.execute(statement, parameters)

    connection = context.store._connect_readonly()
    try:
        trader_row = connection.execute(
            "SELECT * FROM paper_traders WHERE trader_id = ?",
            (context.trader.trader_id,),
        ).fetchone()
        origin = connection.execute(
            "SELECT * FROM paper_ledger_origins WHERE trader_id = ?",
            (context.trader.trader_id,),
        ).fetchone()
        equity = connection.execute(
            "SELECT * FROM paper_ledger_equity_points"
        ).fetchone()
        assert trader_row is not None
        assert origin is not None
        assert equity is not None
        record = context.store._record_from_row(connection, trader_row)

        context.service._build_ledger_origin(
            _ObservedConnection(connection, equity),  # type: ignore[arg-type]
            record=record,
            origin=origin,
        )
    finally:
        connection.close()

    assert {
        "currency",
        "cash",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
    } <= accessed


def test_missing_store_and_missing_trader_have_exact_ledger_outcome(
    tmp_path: Path,
    context: _Context,
) -> None:
    missing_path = tmp_path / "never-created" / "paper-traders.sqlite3"
    missing = PaperReviewService(PaperTraderStore(missing_path))
    for service, trader_id in (
        (missing, "trader-" + ("f" * 32)),
        (context.service, "trader-" + ("e" * 32)),
    ):
        with pytest.raises(PaperReviewDomainError) as raised:
            service.ledger_origin(trader_id)
        assert raised.value.code == "trader_not_found"
    assert not missing_path.exists()
    assert not missing_path.parent.exists()


def test_missing_store_review_accept_is_preaccept_error_without_creation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-accept" / "paper.sqlite3"
    service = PaperReviewService(PaperTraderStore(path))

    with pytest.raises(PaperReviewDomainError) as raised:
        service.accept_review_snapshot(
            trader_id="trader-" + ("f" * 32),
            body=PaperReviewCreateRequest(
                schema="paper_review_create_request.v1",
                request_id=_REQUEST_ID,
            ),
        )

    assert raised.value.code == "trader_not_found"
    assert raised.value.payload.request_id == _REQUEST_ID
    assert raised.value.payload.snapshot_id is None
    assert raised.value.payload.progress is None
    assert not path.exists()
    assert not path.parent.exists()


def test_trader_without_ledger_is_ledger_not_ready(
    context: _Context,
) -> None:
    _drop_mutate_restore(
        context.store.path,
        (
            "paper_ledger_baseline_members_cannot_be_deleted",
            "paper_ledger_equity_points_cannot_be_deleted",
            "paper_ledger_origins_cannot_be_deleted",
        ),
        lambda connection: (
            connection.execute("DELETE FROM paper_ledger_baseline_members"),
            connection.execute("DELETE FROM paper_ledger_equity_points"),
            connection.execute("DELETE FROM paper_ledger_origins"),
        ),
    )

    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.ledger_origin(context.trader.trader_id)

    assert raised.value.code == "ledger_not_ready"


def test_missing_ledger_read_never_opens_write_connection(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drop_mutate_restore(
        context.store.path,
        (
            "paper_ledger_baseline_members_cannot_be_deleted",
            "paper_ledger_equity_points_cannot_be_deleted",
            "paper_ledger_origins_cannot_be_deleted",
        ),
        lambda connection: (
            connection.execute("DELETE FROM paper_ledger_baseline_members"),
            connection.execute("DELETE FROM paper_ledger_equity_points"),
            connection.execute("DELETE FROM paper_ledger_origins"),
        ),
    )

    def write_must_not_open() -> sqlite3.Connection:
        raise AssertionError("ledger read attempted a lazy write or backfill")

    monkeypatch.setattr(context.store, "_connect_write", write_must_not_open)

    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.ledger_origin(context.trader.trader_id)

    assert raised.value.code == "ledger_not_ready"


@pytest.mark.parametrize(
    ("trigger", "statement"),
    [
        (
            "paper_ledger_origins_are_immutable",
            "UPDATE paper_ledger_origins SET interpretation_json = '{}'",
        ),
        (
            "paper_ledger_baseline_members_cannot_be_deleted",
            "DELETE FROM paper_ledger_baseline_members WHERE ordinal = 4",
        ),
    ],
)
def test_ledger_persisted_drift_fails_closed(
    context: _Context,
    trigger: str,
    statement: str,
) -> None:
    _drop_mutate_restore(
        context.store.path,
        (trigger,),
        lambda connection: connection.execute(statement),
    )

    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.ledger_origin(context.trader.trader_id)

    assert raised.value.code == "ledger_integrity_failed"


@pytest.mark.parametrize("version", [0, 1, 2, 99])
def test_non_v3_ledger_read_requires_schema_upgrade_without_write(
    tmp_path: Path,
    version: int,
) -> None:
    path = tmp_path / f"legacy-v{version}.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    service = PaperReviewService(PaperTraderStore(path))
    for read in (
        lambda: service.ledger_origin("trader-" + ("1" * 32)),
        lambda: service.review_request_status(_REQUEST_ID),
    ):
        with pytest.raises(PaperReviewDomainError) as raised:
            read()
        assert raised.value.code == "store_schema_upgrade_required"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not any(
        Path(f"{path}{suffix}").exists()
        for suffix in ("-journal", "-wal", "-shm")
    )


def test_ledger_model_accepts_safe_run_identity_matrix(
    context: _Context,
) -> None:
    canonical = context.service.ledger_origin(
        context.trader.trader_id
    ).model_dump(by_alias=True, mode="json")

    for run_id in ("nq-20260728-standard-365adf", "a", "A_1.x-y"):
        payload = json.loads(json.dumps(canonical))
        payload["baseline"]["run_id"] = run_id
        payload["baseline"]["members"][1]["path"] = (
            f"baseline/trades/{run_id}.json"
        )
        payload["baseline"]["members"][2]["path"] = (
            f"baseline/equity/{run_id}.json"
        )
        payload["baseline"]["members"][3]["path"] = (
            f"baseline/events/{run_id}.json"
        )
        for ref in payload["interpretation"]["supporting_evidence_refs"]:
            ref["path"] = f"baseline/events/{run_id}.json"
        payload["readiness_snapshot"] = context.trader.readiness_snapshot

        parsed = PaperLedgerOrigin.model_validate(payload, strict=True)

        assert parsed.baseline.run_id == run_id


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        " run",
        "run ",
        ".hidden",
        "../escape",
        "a/b",
        "a\\b",
        "C:drive",
        "a#b",
        "a?b",
        "a\x00b",
    ],
)
def test_ledger_model_rejects_unsafe_run_before_path_projection(
    context: _Context,
    run_id: str,
) -> None:
    payload = context.service.ledger_origin(
        context.trader.trader_id
    ).model_dump(by_alias=True, mode="json")
    payload["baseline"]["run_id"] = run_id

    with pytest.raises(ValidationError, match="baseline run ID"):
        PaperLedgerOrigin.model_validate(payload, strict=True)


def test_ledger_model_rejects_consistent_traversal_identity(
    context: _Context,
) -> None:
    run_id = "../escape"
    payload = context.service.ledger_origin(
        context.trader.trader_id
    ).model_dump(by_alias=True, mode="json")
    payload["baseline"]["run_id"] = run_id
    payload["baseline"]["members"][1]["path"] = (
        f"baseline/trades/{run_id}.json"
    )
    payload["baseline"]["members"][2]["path"] = (
        f"baseline/equity/{run_id}.json"
    )
    payload["baseline"]["members"][3]["path"] = (
        f"baseline/events/{run_id}.json"
    )
    for ref in payload["interpretation"]["supporting_evidence_refs"]:
        ref["path"] = f"baseline/events/{run_id}.json"
    payload["readiness_snapshot"] = context.trader.readiness_snapshot

    with pytest.raises(ValidationError, match="baseline run ID"):
        PaperLedgerOrigin.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00Z",
        "0099-12-31T23:59:59Z",
        "0100-01-01T00:00:00.000001Z",
        "9999-12-31T23:59:59.100000Z",
    ],
)
def test_review_utc_accepts_exact_consumer_boundary(timestamp: str) -> None:
    assert paper_review_module._canonical_utc(timestamp) == timestamp


@pytest.mark.parametrize(
    "timestamp",
    [
        "0000-01-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-07-29 00:00:00Z",
        "2026-07-29T00:00:00z",
        "2026-07-29T00:00:00+00:00",
        "2026-07-29T00:00:00.0Z",
        "2026-07-29T00:00:00.00000Z",
        "2026-07-29T00:00:00.000000Z",
        "2026-07-29T00:00:00.1234567Z",
    ],
)
def test_review_utc_rejects_noncanonical_consumer_boundary(
    timestamp: str,
) -> None:
    with pytest.raises(ValueError, match="canonical UTC"):
        paper_review_module._canonical_utc(timestamp)


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        "00000000-0000-1000-8000-000000000101",
        "00000000-0000-4000-7000-000000000101",
        str(uuid1()),
        str(uuid4()).upper(),
        "{" + str(uuid4()) + "}",
    ],
)
def test_review_request_rejects_noncanonical_uuid4(
    request_id: str,
) -> None:
    with pytest.raises(ValidationError):
        PaperReviewCreateRequest(
            schema="paper_review_create_request.v1",
            request_id=request_id,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"schema": "paper_review_create_request.v2", "request_id": _REQUEST_ID},
        {"schema": "paper_review_create_request.v1"},
        {
            "schema": "paper_review_create_request.v1",
            "request_id": _REQUEST_ID,
            "snapshot_id": _SNAPSHOT_ID,
        },
    ],
)
def test_review_request_shape_is_exact_before_any_write(
    context: _Context,
    body: dict[str, object],
) -> None:
    before = _db_facts(context.store.path)

    with pytest.raises(ValidationError):
        PaperReviewCreateRequest.model_validate(body, strict=True)

    assert _db_facts(context.store.path) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", "unknown_code"),
        ("issues", [{"kind": "unknown_kind"}]),
        ("issues", [{"kind": "build_interrupted", "ref_chain": None}]),
    ],
)
def test_review_error_unknown_or_nullable_drift_fails_closed(
    field: str,
    value: object,
) -> None:
    payload = {
        "schema": "paper_review_error.v1",
        "code": "build_interrupted",
        "message": "Build stopped.",
        "retryable": False,
        "request_id": _REQUEST_ID,
        "snapshot_id": _SNAPSHOT_ID,
        "progress": {
            "completed_parts": 0,
            "total_parts": 10,
            "current_part": None,
        },
        "issues": [
            {
                "kind": "build_interrupted",
                "path": None,
                "source_ref": None,
                "expected_sha256": None,
                "actual_sha256": None,
                "ref_chain": [],
            }
        ],
    }
    if field == "issues" and isinstance(value, list):
        base_issue = payload["issues"][0]
        assert isinstance(base_issue, dict)
        base_issue.update(value[0])
    else:
        payload[field] = value

    with pytest.raises(ValidationError):
        PaperReviewErrorPayload.model_validate(payload, strict=True)


def test_accept_persists_exact_request_preparing_and_fingerprint(
    context: _Context,
) -> None:
    status, created = _accept(context)

    assert created is True
    assert status.model_dump(by_alias=True, mode="json") == {
        "schema": "paper_review_status.v1",
        "request_id": _REQUEST_ID,
        "snapshot_id": _SNAPSHOT_ID,
        "trader_id": context.trader.trader_id,
        "status": "preparing",
        "captured_at": "2026-07-29T01:02:03.000004Z",
        "progress": {
            "completed_parts": 0,
            "total_parts": 10,
            "current_part": None,
        },
        "ready": None,
        "error": None,
    }
    expected_intent = (
        '{"request_id":"00000000-0000-4000-8000-000000000101",'
        '"schema":"paper_review_create_intent.v1",'
        f'"trader_id":"{context.trader.trader_id}"}}'
    ).encode()
    with sqlite3.connect(context.store.path) as connection:
        connection.row_factory = sqlite3.Row
        request = connection.execute(
            "SELECT * FROM paper_review_requests"
        ).fetchone()
        event = connection.execute(
            "SELECT * FROM paper_review_status_events"
        ).fetchone()
    assert request is not None
    assert request["snapshot_id"] == _SNAPSHOT_ID
    assert request["captured_at"] == request["created_at"] == (
        "2026-07-29T01:02:03.000004Z"
    )
    assert request["builder_instance_id"] == _BUILDER_ID
    assert request["request_fingerprint"] == sha256(expected_intent).hexdigest()
    assert request["request_fingerprint"] == review_request_fingerprint(
        request_id=_REQUEST_ID,
        trader_id=context.trader.trader_id,
    )
    assert tuple(event) == (
        _REQUEST_ID,
        1,
        "preparing",
        "2026-07-29T01:02:03.000004Z",
        '{"completed_parts":0,"current_part":null,"total_parts":10}',
    )
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 1,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 0,
    }


def test_same_request_replays_frozen_identity_without_factories_or_write(
    context: _Context,
) -> None:
    first, created = _accept(context)
    before = _db_facts(context.store.path)
    replay_service = PaperReviewService(
        context.store,
        clock=lambda: (_ for _ in ()).throw(AssertionError("clock replayed")),
        snapshot_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("snapshot factory replayed")
        ),
        builder_instance_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("builder factory replayed")
        ),
        builder_registry=context.service.builder_registry,
    )

    replay, replay_created = replay_service.accept_review_snapshot(
        trader_id=context.trader.trader_id,
        body=PaperReviewCreateRequest(
            schema="paper_review_create_request.v1",
            request_id=_REQUEST_ID,
        ),
    )

    assert created is True
    assert replay_created is False
    assert replay == first
    assert _db_facts(context.store.path) == before


def test_request_id_conflict_for_other_trader_is_zero_write(
    context: _Context,
) -> None:
    _accept(context)
    other = _provision_trader(
        store=context.store,
        catalog=context.catalog,
        registry=context.registry,
        decisions=context.decisions,
        request_id="00000000-0000-4000-8000-000000000002",
    )
    before = _db_facts(context.store.path)

    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.accept_review_snapshot(
            trader_id=other.trader_id,
            body=PaperReviewCreateRequest(
                schema="paper_review_create_request.v1",
                request_id=_REQUEST_ID,
            ),
        )

    assert raised.value.code == "request_id_conflict"
    assert _db_facts(context.store.path) == before


def test_concurrent_same_request_creates_exactly_one_snapshot(
    context: _Context,
) -> None:
    body = PaperReviewCreateRequest(
        schema="paper_review_create_request.v1",
        request_id=_REQUEST_ID,
    )

    def accept() -> tuple[str, bool]:
        status, created = context.service.accept_review_snapshot(
            trader_id=context.trader.trader_id,
            body=body,
        )
        return status.snapshot_id, created

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _value: accept(), range(16)))

    assert {snapshot for snapshot, _created in results} == {_SNAPSHOT_ID}
    assert sum(created for _snapshot, created in results) == 1
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 1,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 0,
    }


def test_commit_before_activate_blocks_status_and_replay_across_services(
    context: _Context,
) -> None:
    registry = _CommitBeforeActivateRegistry()
    factory_calls = {"clock": 0, "snapshot": 0, "builder": 0}

    def clock() -> datetime:
        factory_calls["clock"] += 1
        return _CAPTURED_AT

    def snapshot_id() -> str:
        factory_calls["snapshot"] += 1
        return _SNAPSHOT_ID

    def builder_id() -> str:
        factory_calls["builder"] += 1
        return _BUILDER_ID

    accepting = PaperReviewService(
        context.store,
        clock=clock,
        snapshot_id_factory=snapshot_id,
        builder_instance_id_factory=builder_id,
        builder_registry=registry,
    )
    status_observer = PaperReviewService(
        context.store,
        builder_registry=registry,
    )
    replay_observer = PaperReviewService(
        context.store,
        clock=lambda: (_ for _ in ()).throw(
            AssertionError("clock replayed")
        ),
        snapshot_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("snapshot factory replayed")
        ),
        builder_instance_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("builder factory replayed")
        ),
        builder_registry=registry,
    )
    body = PaperReviewCreateRequest(
        schema="paper_review_create_request.v1",
        request_id=_REQUEST_ID,
    )
    status_started = Event()
    status_done = Event()
    replay_started = Event()
    replay_done = Event()

    def observe_status() -> object:
        status_started.set()
        try:
            return status_observer.review_request_status(_REQUEST_ID)
        finally:
            status_done.set()

    def replay() -> tuple[object, bool]:
        replay_started.set()
        try:
            return replay_observer.accept_review_snapshot(
                trader_id=context.trader.trader_id,
                body=body,
            )
        finally:
            replay_done.set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        accept_future = executor.submit(
            accepting.accept_review_snapshot,
            trader_id=context.trader.trader_id,
            body=body,
        )
        assert registry.activate_entered.wait(timeout=5)
        committed = _db_facts(context.store.path)
        assert committed[2] == {
            "paper_review_requests": 1,
            "paper_review_status_events": 1,
            "paper_review_ready_artifacts": 0,
            "paper_review_failures": 0,
        }

        status_future = executor.submit(observe_status)
        replay_future = executor.submit(replay)
        assert status_started.wait(timeout=5)
        assert replay_started.wait(timeout=5)
        try:
            status_returned_before_release = status_done.wait(timeout=0.2)
            replay_returned_before_release = replay_done.wait(timeout=0.2)
            during_transition = _db_facts(context.store.path)
        finally:
            registry.release_activate.set()

        accepted, created = accept_future.result(timeout=5)
        observed = status_future.result(timeout=5)
        replayed, replay_created = replay_future.result(timeout=5)

    assert status_returned_before_release is False
    assert replay_returned_before_release is False
    assert created is True
    assert replay_created is False
    assert accepted == observed == replayed
    assert accepted.status == "preparing"
    assert accepted.ready is None
    assert accepted.error is None
    assert accepted.request_id == _REQUEST_ID
    assert accepted.snapshot_id == _SNAPSHOT_ID
    assert accepted.trader_id == context.trader.trader_id
    assert accepted.captured_at == "2026-07-29T01:02:03.000004Z"
    assert factory_calls == {"clock": 1, "snapshot": 1, "builder": 1}
    assert during_transition == committed
    assert _db_facts(context.store.path) == committed


def test_activation_failure_releases_lock_and_preserves_orphan(
    context: _Context,
    tmp_path: Path,
) -> None:
    registry = _FailingActivationRegistry()
    failing = PaperReviewService(
        context.store,
        clock=lambda: _CAPTURED_AT,
        snapshot_id_factory=lambda: _SNAPSHOT_ID,
        builder_instance_id_factory=lambda: _BUILDER_ID,
        builder_registry=registry,
    )
    body = PaperReviewCreateRequest(
        schema="paper_review_create_request.v1",
        request_id=_REQUEST_ID,
    )

    with pytest.raises(RuntimeError, match="injected activate failure"):
        failing.accept_review_snapshot(
            trader_id=context.trader.trader_id,
            body=body,
        )

    assert registry.activate_calls == 1
    committed = _db_facts(context.store.path)
    assert committed[2] == {
        "paper_review_requests": 1,
        "paper_review_status_events": 1,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 0,
    }
    recovering = PaperReviewService(
        context.store,
        clock=lambda: (_ for _ in ()).throw(
            AssertionError("clock replayed after activation failure")
        ),
        snapshot_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("snapshot factory replayed after activation failure")
        ),
        builder_instance_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("builder factory replayed after activation failure")
        ),
        builder_registry=registry,
    )

    status = recovering.review_request_status(_REQUEST_ID)
    replayed, created = recovering.accept_review_snapshot(
        trader_id=context.trader.trader_id,
        body=body,
    )

    assert created is False
    assert replayed == status
    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "build_interrupted"
    assert _db_facts(context.store.path) == committed

    independent_ids = _ids("trader-", 501)
    independent_accounts = _ids("paper-account-", 601)
    independent_ledgers = _ids("paper-ledger-", 701)
    independent_store = PaperTraderStore(
        tmp_path / "independent" / "paper-traders.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        trader_id_factory=lambda: next(independent_ids),
        account_id_factory=lambda: next(independent_accounts),
        ledger_origin_id_factory=lambda: next(independent_ledgers),
    )
    independent_trader = _provision_trader(
        store=independent_store,
        catalog=context.catalog,
        registry=context.registry,
        decisions=context.decisions,
        request_id="00000000-0000-4000-8000-000000000501",
    )
    independent = PaperReviewService(
        independent_store,
        clock=lambda: _CAPTURED_AT,
        snapshot_id_factory=lambda: (
            "paper-review-00000000000040008000000000000302"
        ),
        builder_instance_id_factory=lambda: (
            "00000000-0000-4000-8000-000000000202"
        ),
        builder_registry=PaperReviewBuilderRegistry(),
    )

    independent_status, independent_created = (
        independent.accept_review_snapshot(
            trader_id=independent_trader.trader_id,
            body=PaperReviewCreateRequest(
                schema="paper_review_create_request.v1",
                request_id="00000000-0000-4000-8000-000000000102",
            ),
        )
    )

    assert independent_created is True
    assert independent_status.status == "preparing"


@pytest.mark.parametrize(
    "service_kwargs",
    [
        {"snapshot_id_factory": lambda: "paper-review-" + ("A" * 32)},
        {
            "snapshot_id_factory": lambda: (
                "paper-review-00000000000010008000000000000301"
            )
        },
        {
            "snapshot_id_factory": lambda: (
                "paper-review-00000000000040007000000000000301"
            )
        },
        {"builder_instance_id_factory": lambda: str(uuid1())},
        {"clock": lambda: datetime(2026, 7, 29, 1, 2, 3)},
    ],
)
def test_bad_snapshot_clock_or_builder_factory_rolls_back(
    context: _Context,
    service_kwargs: dict[str, Callable[[], object]],
) -> None:
    service = PaperReviewService(
        context.store,
        builder_registry=PaperReviewBuilderRegistry(),
        **service_kwargs,
    )

    with pytest.raises(ValueError):
        service.accept_review_snapshot(
            trader_id=context.trader.trader_id,
            body=PaperReviewCreateRequest(
                schema="paper_review_create_request.v1",
                request_id=_REQUEST_ID,
            ),
        )

    assert _review_counts(context.store.path) == {
        table: 0 for table in _REVIEW_TABLES
    }


@pytest.mark.parametrize("failure_step", ["review_request", "preparing_event"])
def test_each_accept_transaction_failure_rolls_back(
    context: _Context,
    failure_step: str,
) -> None:
    def fail(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected {step}")

    service = PaperReviewService(
        context.store,
        clock=lambda: _CAPTURED_AT,
        snapshot_id_factory=lambda: _SNAPSHOT_ID,
        builder_instance_id_factory=lambda: _BUILDER_ID,
        builder_registry=PaperReviewBuilderRegistry(),
        step_hook=fail,
    )

    with pytest.raises(RuntimeError, match=failure_step):
        service.accept_review_snapshot(
            trader_id=context.trader.trader_id,
            body=PaperReviewCreateRequest(
                schema="paper_review_create_request.v1",
                request_id=_REQUEST_ID,
            ),
        )

    assert _review_counts(context.store.path) == {
        table: 0 for table in _REVIEW_TABLES
    }


def test_active_builder_progress_is_in_memory_only(
    context: _Context,
) -> None:
    _accept(context)
    before = _db_facts(context.store.path)
    context.service._set_progress_for_test(
        _REQUEST_ID,
        completed_parts=4,
        current_part="paper/events.json",
    )

    status = context.service.review_request_status(_REQUEST_ID)

    assert status.status == "preparing"
    assert status.progress.model_dump() == {
        "completed_parts": 4,
        "total_parts": 10,
        "current_part": "paper/events.json",
    }
    assert _db_facts(context.store.path) == before


def test_inactive_preparing_projects_build_interrupted_without_write(
    context: _Context,
) -> None:
    _accept(context)
    context.service._deactivate_builder_for_test(_REQUEST_ID)
    before = _db_facts(context.store.path)
    observer = PaperReviewService(
        context.store,
        clock=lambda: (_ for _ in ()).throw(
            AssertionError("clock replayed for genuine interruption")
        ),
        snapshot_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("snapshot factory replayed for genuine interruption")
        ),
        builder_instance_id_factory=lambda: (_ for _ in ()).throw(
            AssertionError("builder factory replayed for genuine interruption")
        ),
        builder_registry=context.service.builder_registry,
    )

    status = observer.review_request_status(_REQUEST_ID)
    replay, created = observer.accept_review_snapshot(
        trader_id=context.trader.trader_id,
        body=PaperReviewCreateRequest(
            schema="paper_review_create_request.v1",
            request_id=_REQUEST_ID,
        ),
    )

    assert created is False
    assert replay == status
    assert status.status == "failed"
    assert status.ready is None
    assert status.error is not None
    assert status.error.model_dump(by_alias=True, mode="json") == {
        "schema": "paper_review_error.v1",
        "code": "build_interrupted",
        "message": "Snapshot build is no longer active in this process.",
        "retryable": False,
        "request_id": _REQUEST_ID,
        "snapshot_id": _SNAPSHOT_ID,
        "progress": {
            "completed_parts": 0,
            "total_parts": 10,
            "current_part": None,
        },
        "issues": [
            {
                "kind": "build_interrupted",
                "path": None,
                "source_ref": None,
                "expected_sha256": None,
                "actual_sha256": None,
                "ref_chain": [],
            }
        ],
    }
    assert _db_facts(context.store.path) == before


def test_interrupted_status_get_never_opens_write_connection(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept(context)
    context.service._deactivate_builder_for_test(_REQUEST_ID)

    def write_must_not_open() -> sqlite3.Connection:
        raise AssertionError("interrupted status GET attempted a write")

    monkeypatch.setattr(context.store, "_connect_write", write_must_not_open)

    status = context.service.review_request_status(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "build_interrupted"


def test_ready_terminal_pair_round_trips_and_is_immutable(
    context: _Context,
) -> None:
    _accept(context)
    opener = "terminal opener\n"
    context.service._save_ready_for_test(
        request_id=_REQUEST_ID,
        artifact_relpath=f"sha256/aa/{'a' * 64}.zip",
        ready_payload=_ready_payload(context),
        terminal_opener_text=opener,
    )

    status = context.service.review_request_status(_REQUEST_ID)

    assert status.status == "ready"
    assert status.progress.completed_parts == 10
    assert status.progress.current_part is None
    assert status.error is None
    assert status.ready is not None
    assert status.ready.model_dump(by_alias=True, mode="json") == (
        _ready_payload(context)
    )
    with sqlite3.connect(context.store.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM paper_review_ready_artifacts"
        ).fetchone()
        event = connection.execute(
            """
            SELECT *
            FROM paper_review_status_events
            WHERE ordinal = 2
            """
        ).fetchone()
    assert row is not None
    assert row["terminal_opener_text"] == opener
    assert row["terminal_opener_bytes"] == len(opener.encode())
    assert row["terminal_opener_sha256"] == sha256(opener.encode()).hexdigest()
    assert event is not None
    assert event["status"] == "ready"
    assert event["payload_json"] == (
        '{"artifact_sha256":"' + ("a" * 64)
        + '","completed_parts":10,"total_parts":10}'
    )
    with pytest.raises(PaperReviewDomainError) as raised:
        context.service._save_failure_for_test(
            request_id=_REQUEST_ID,
            http_status=503,
            error_payload=_failure_payload(),
        )
    assert raised.value.code == "snapshot_integrity_failed"


def test_failed_terminal_pair_round_trips_and_is_immutable(
    context: _Context,
) -> None:
    _accept(context)
    context.service._save_failure_for_test(
        request_id=_REQUEST_ID,
        http_status=503,
        error_payload=_failure_payload(),
    )

    status = context.service.review_request_status(_REQUEST_ID)

    assert status.status == "failed"
    assert status.ready is None
    assert status.error is not None
    assert status.error.model_dump(by_alias=True, mode="json") == (
        _failure_payload()
    )
    with sqlite3.connect(context.store.path) as connection:
        connection.row_factory = sqlite3.Row
        failure = connection.execute(
            "SELECT * FROM paper_review_failures"
        ).fetchone()
        event = connection.execute(
            """
            SELECT *
            FROM paper_review_status_events
            WHERE ordinal = 2
            """
        ).fetchone()
    assert failure is not None
    error_bytes = json.dumps(
        _failure_payload(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert failure["error_json"].encode() == error_bytes
    assert failure["error_sha256"] == sha256(error_bytes).hexdigest()
    assert event is not None
    assert event["payload_json"] == (
        '{"completed_parts":3,"error_sha256":"'
        + sha256(error_bytes).hexdigest()
        + '","total_parts":10}'
    )
    with pytest.raises(PaperReviewDomainError) as raised:
        context.service._save_ready_for_test(
            request_id=_REQUEST_ID,
            artifact_relpath=f"sha256/aa/{'a' * 64}.zip",
            ready_payload=_ready_payload(context),
            terminal_opener_text="terminal opener\n",
        )
    assert raised.value.code == "snapshot_integrity_failed"


@pytest.mark.parametrize(
    "failure_step",
    ["ready_artifact", "ready_event", "failure_row", "failed_event"],
)
def test_terminal_pair_failure_rolls_back_atomically(
    context: _Context,
    failure_step: str,
) -> None:
    _accept(context)
    before = _review_counts(context.store.path)

    def fail(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected {step}")

    service = PaperReviewService(
        context.store,
        clock=lambda: _CAPTURED_AT,
        builder_registry=context.service.builder_registry,
        step_hook=fail,
    )
    with pytest.raises(RuntimeError, match=failure_step):
        if failure_step.startswith("ready"):
            service._save_ready_for_test(
                request_id=_REQUEST_ID,
                artifact_relpath=f"sha256/aa/{'a' * 64}.zip",
                ready_payload=_ready_payload(context),
                terminal_opener_text="terminal opener\n",
            )
        else:
            service._save_failure_for_test(
                request_id=_REQUEST_ID,
                http_status=503,
                error_payload=_failure_payload(),
            )

    assert _review_counts(context.store.path) == before
    status = context.service.review_request_status(_REQUEST_ID)
    assert status.status == "preparing"


@pytest.mark.parametrize("corruption", ["half_ready", "dual_terminal", "payload"])
def test_half_dual_or_malformed_terminal_state_fails_closed(
    context: _Context,
    corruption: str,
) -> None:
    _accept(context)
    if corruption == "payload":
        _drop_mutate_restore(
            context.store.path,
            ("paper_review_status_events_are_append_only",),
            lambda connection: connection.execute(
                """
                UPDATE paper_review_status_events
                SET payload_json = '{"completed_parts":1}'
                WHERE ordinal = 1
                """
            ),
        )
    else:
        context.service._save_ready_for_test(
            request_id=_REQUEST_ID,
            artifact_relpath=f"sha256/aa/{'a' * 64}.zip",
            ready_payload=_ready_payload(context),
            terminal_opener_text="terminal opener\n",
        )
        with sqlite3.connect(context.store.path) as connection:
            if corruption == "half_ready":
                trigger = connection.execute(
                    """
                    SELECT sql
                    FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name = 'paper_review_status_events_cannot_be_deleted'
                    """
                ).fetchone()
                assert trigger is not None and trigger[0] is not None
                connection.execute(
                    "DROP TRIGGER paper_review_status_events_cannot_be_deleted"
                )
                connection.execute(
                    "DELETE FROM paper_review_status_events WHERE ordinal = 2"
                )
                connection.execute(trigger[0])
            else:
                error_json = json.dumps(
                    _failure_payload(),
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                connection.execute(
                    """
                    INSERT INTO paper_review_failures (
                        request_id, http_status, error_json, error_sha256, failed_at
                    ) VALUES (?, 503, ?, ?, ?)
                    """,
                    (
                        _REQUEST_ID,
                        error_json,
                        sha256(error_json.encode()).hexdigest(),
                        "2026-07-29T01:03:00Z",
                    ),
                )
            connection.commit()

    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.review_request_status(_REQUEST_ID)

    assert raised.value.code == "snapshot_integrity_failed"


def test_terminal_rejects_second_append_and_schema_rejects_third_event(
    context: _Context,
) -> None:
    _accept(context)
    context.service._save_failure_for_test(
        request_id=_REQUEST_ID,
        http_status=503,
        error_payload=_failure_payload(),
    )
    before = _review_counts(context.store.path)

    with pytest.raises(PaperReviewDomainError):
        context.service._save_failure_for_test(
            request_id=_REQUEST_ID,
            http_status=503,
            error_payload=_failure_payload(),
        )
    with (
        sqlite3.connect(context.store.path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            """
            INSERT INTO paper_review_status_events (
                request_id, ordinal, status, occurred_at, payload_json
            ) VALUES (?, 3, 'failed', ?, '{}')
            """,
            (_REQUEST_ID, "2026-07-29T01:04:00Z"),
        )
    assert _review_counts(context.store.path) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE paper_review_requests SET trader_id = 'trader-"
        + ("f" * 32)
        + "'",
        "DELETE FROM paper_review_requests",
        "UPDATE paper_review_status_events SET payload_json = '{}'",
        "DELETE FROM paper_review_status_events",
    ],
)
def test_review_request_and_status_guards_are_immutable(
    context: _Context,
    statement: str,
) -> None:
    _accept(context)
    with (
        sqlite3.connect(context.store.path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(statement)


def test_missing_request_status_is_readonly_and_never_creates_store(
    tmp_path: Path,
    context: _Context,
) -> None:
    before = _db_facts(context.store.path)
    with pytest.raises(PaperReviewDomainError) as raised:
        context.service.review_request_status(_REQUEST_ID)
    assert raised.value.code == "request_not_found"
    assert raised.value.payload.model_dump(by_alias=True, mode="json") == {
        "schema": "paper_review_error.v1",
        "code": "request_not_found",
        "message": "paper review request does not exist",
        "retryable": False,
        "request_id": _REQUEST_ID,
        "snapshot_id": None,
        "progress": None,
        "issues": [],
    }
    assert _db_facts(context.store.path) == before

    missing_path = tmp_path / "missing-status" / "paper.sqlite3"
    missing = PaperReviewService(PaperTraderStore(missing_path))
    with pytest.raises(PaperReviewDomainError) as raised:
        missing.review_request_status(_REQUEST_ID)
    assert raised.value.code == "request_not_found"
    assert not missing_path.exists()


def test_fingerprint_changes_with_trader_identity() -> None:
    first = review_request_fingerprint(
        request_id=_REQUEST_ID,
        trader_id="trader-" + ("1" * 32),
    )
    second = review_request_fingerprint(
        request_id=_REQUEST_ID,
        trader_id="trader-" + ("2" * 32),
    )

    assert first != second
    assert first == sha256(
        b'{"request_id":"00000000-0000-4000-8000-000000000101",'
        b'"schema":"paper_review_create_intent.v1",'
        b'"trader_id":"trader-11111111111111111111111111111111"}'
    ).hexdigest()
