"""P6 Stage A isolated provisioning contract, boundary, and storage tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from typing import Literal, NoReturn, cast
from uuid import UUID, uuid1, uuid3, uuid4, uuid5

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from futures_research.api import paper_ledger
from futures_research.api.paper_provisioning import (
    DisabledPaperProvisioningAuthorizationPolicy,
    OneOffPaperProvisioningAuthorizationPolicy,
    PaperProvisioningAuthorization,
    PaperProvisioningCreateInspection,
    PaperProvisioningRequestConflictError,
)
from futures_research.api.paper_traders import (
    AllowIsolatedPaperActivationPolicy,
    DefaultDenyPaperActivationPolicy,
    DefaultPaperExternalReadinessProvider,
    IsolatedPaperExternalReadinessProvider,
    PaperAccountOrigin,
    PaperExternalReadiness,
    PaperProvisioningOrigin,
    PaperReadinessSignal,
    PaperTraderCreateRequest,
    PaperTraderSelection,
    PaperTraderStore,
    PaperTraderStoreIntegrityError,
    PaperTraderStoreSchemaUpgradeRequiredError,
    create_paper_trader,
    paper_request_payload_sha256,
)
from futures_research.api.promotion_decisions import (
    PromotionDecisionStore,
    source_from_result_snapshot,
)
from futures_research.api.results_catalog import ResultExportArtifacts, ResultsCatalog
from futures_research.api.routes_paper import router
from futures_research.data.contracts import ContractRegistry

_STRATEGY_ID = "strategy-p6-0001"
_STRATEGY_SHA = "a" * 64
_CONTRACT_ID = "NQ-202609-CME"
_RUN_ID = "p6-baseline-001"
_REQUEST_ID = "00000000-0000-4000-8000-000000000001"
_CHECKED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
_PROVISIONING_OPERATION_ID = "paper-provision-" + ("4" * 32)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _manifest(
    run_id: str,
    *,
    strategy_id: str = _STRATEGY_ID,
    strategy_sha: str = _STRATEGY_SHA,
    contract_id: str = _CONTRACT_ID,
    range_end: str = "2026-07-24T00:00:00Z",
    initial_capital: object = 123_456.75,
    validation_run: bool = False,
) -> dict[str, object]:
    return {
        "schema": "run_manifest.v1",
        "run_id": run_id,
        "strategy_version": strategy_id,
        "contract_id": contract_id,
        "session_name": "eth",
        "range_start": "2026-07-01T00:00:00Z",
        "range_end": range_end,
        "initial_capital": initial_capital,
        "quantity": 1,
        "costs": {
            "commission_per_side": 2.8,
            "slippage_ticks": {
                "breakout_entry": 1,
                "stop_exit": 2,
                "target_exit": 0,
                "day_end_exit": 1,
            },
            "target_requires_through": False,
        },
        "fill_model": "conservative",
        "data_fingerprint": {
            "schema": "canonical-bars.v1",
            "algorithm": "sha256",
            "digest": "d" * 64,
            "bar_count": 0,
            "source_partitions": [],
            "quality_report_ids": [],
        },
        "strategy_binding": {
            "schema": "strategy_binding.v1",
            "source": "strategy_file",
            "strategy_id": strategy_id,
            "strategy_name": "P6 locked strategy",
            "content_sha256": strategy_sha,
            "universe_contracts": [contract_id],
            "universe_authorized": True,
            "overrides": [],
        },
        "quality_gate_mode": "enforce",
        "validation_run": validation_run,
        "excluded_trading_dates": [],
        "created_at": "2026-07-25T01:00:00Z",
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_result(
    root: Path,
    *,
    run_id: str = _RUN_ID,
    strategy_id: str = _STRATEGY_ID,
    strategy_sha: str = _STRATEGY_SHA,
    contract_id: str = _CONTRACT_ID,
    range_end: str = "2026-07-24T00:00:00Z",
    initial_capital: object = 123_456.75,
    validation_run: bool = False,
) -> Path:
    main = {
        "schema": "result.v1",
        "run": {
            "run_id": run_id,
            "strategy_version": strategy_id,
            "manifest": _manifest(
                run_id,
                strategy_id=strategy_id,
                strategy_sha=strategy_sha,
                contract_id=contract_id,
                range_end=range_end,
                initial_capital=initial_capital,
                validation_run=validation_run,
            ),
            "engine": {"nautilus": "1.230.0", "app": "0.1.0"},
        },
        "metrics": {
            "trade_count": 0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "net_r": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "expectancy_r": None,
            "max_drawdown_pnl": 0.0,
        },
        "scorecard": [
            {"dim": "1_sample", "status": "pass", "detail": {"trades": 0}},
            {"dim": "7_simplicity", "status": "warn", "detail": {"params": 21}},
        ],
        "trades_ref": f"trades/{run_id}.json",
        "equity_curve_ref": f"equity/{run_id}.json",
        "events_ref": f"events/{run_id}.json",
        "decision_evidence_complete": True,
    }
    main_path = root / f"{run_id}.json"
    _write_json(main_path, main)
    _write_json(
        root / "trades" / f"{run_id}.json",
        {
            "schema": "trades.v1",
            "run_id": run_id,
            "trades": [],
            "decision_evidence_complete": True,
        },
    )
    _write_json(
        root / "equity" / f"{run_id}.json",
        {
            "schema": "equity_curve.v1",
            "run_id": run_id,
            "points": [],
        },
    )
    _write_json(
        root / "events" / f"{run_id}.json",
        {
            "schema": "events.v1",
            "run_id": run_id,
            "events": [],
            "rejection_evidence": [],
            "evidence_summary": {
                "availability": "available",
                "complete": True,
                "evaluation_count": 0,
                "rejection_count": 0,
                "layer_reached_counts": {},
                "blocking_condition_counts": {},
                "deepest_layer": None,
                "trade_count": 0,
            },
            "evidence_complete": True,
        },
    )
    return main_path


@dataclass(frozen=True)
class _Environment:
    root: Path
    catalog: ResultsCatalog
    decisions: PromotionDecisionStore
    traders: PaperTraderStore
    registry: ContractRegistry
    main_path: Path


def _add_decision(
    environment: _Environment,
    *,
    decision: Literal["use", "return", "abandon"] = "use",
    request_id: str = "eligible-source",
) -> None:
    snapshot = environment.catalog.get_verified_snapshot(_RUN_ID)
    environment.decisions.append(
        request_id=request_id,
        request_payload_sha256=sha256(
            f"{request_id}:{decision}".encode()
        ).hexdigest(),
        decision=decision,
        reason="isolated P6 test provenance",
        source=source_from_result_snapshot(snapshot, expected_run_id=_RUN_ID),
    )


def _environment(
    tmp_path: Path,
    *,
    eligible: bool = True,
    decision: Literal["use", "return", "abandon"] = "use",
) -> _Environment:
    root = tmp_path / "results"
    main_path = _write_result(root)
    catalog = ResultsCatalog(root)
    decisions = PromotionDecisionStore(
        tmp_path / "promotion.sqlite3",
        clock=lambda: _CHECKED_AT,
        decision_id_factory=lambda: f"promotion-{uuid4().hex}",
    )
    traders = PaperTraderStore(
        tmp_path / "paper" / "paper-traders.sqlite3",
        clock=lambda: _CHECKED_AT,
        trader_id_factory=lambda: "trader-" + ("1" * 32),
        account_id_factory=lambda: "paper-account-" + ("2" * 32),
        ledger_origin_id_factory=lambda: "paper-ledger-" + ("3" * 32),
    )
    environment = _Environment(
        root=root,
        catalog=catalog,
        decisions=decisions,
        traders=traders,
        registry=ContractRegistry.from_yaml(
            _PROJECT_ROOT / "config" / "contracts.yaml"
        ),
        main_path=main_path,
    )
    if eligible:
        _add_decision(environment, decision=decision)
    return environment


def _provider(
    *,
    market_session: Literal["open", "closed", "unknown"] = "closed",
    ib: Literal["ready", "blocked", "unknown"] = "ready",
    calendar: Literal["ready", "blocked", "unknown"] = "ready",
    telegram: Literal["ready", "blocked", "unknown"] = "ready",
) -> IsolatedPaperExternalReadinessProvider:
    def signal(status: Literal["ready", "blocked", "unknown"]) -> PaperReadinessSignal:
        return PaperReadinessSignal(status=status, reason=f"isolated {status}")

    return IsolatedPaperExternalReadinessProvider(
        checked_at=_CHECKED_AT,
        market_session=market_session,
        ib_realtime=signal(ib),
        exchange_calendar=signal(calendar),
        telegram=signal(telegram),
    )


@contextmanager
def _client(
    environment: _Environment,
    *,
    activation: object | None = None,
    provisioning_policy: object | None = None,
    provider: object | None = None,
    registry: ContractRegistry | None = None,
    trader_store: PaperTraderStore | None = None,
) -> Iterator[TestClient]:
    api = FastAPI()
    api.include_router(router)
    api.state.results_catalog = environment.catalog
    api.state.promotion_decision_store = environment.decisions
    api.state.paper_trader_store = trader_store or environment.traders
    api.state.paper_activation_policy = (
        activation or AllowIsolatedPaperActivationPolicy()
    )
    api.state.paper_provisioning_authorization_policy = (
        provisioning_policy
        if provisioning_policy is not None
        else (
            _one_off_provisioning_policy(environment)
            if environment.main_path.is_file()
            else DisabledPaperProvisioningAuthorizationPolicy()
        )
    )
    api.state.paper_external_readiness_provider = provider or _provider()
    api.state.paper_contract_registry = registry or environment.registry
    # Lazy isolated v4 path — only materialised on POST /traders mirror,
    # so preflight tests keep zero paper side-effects.
    api.state.paper_runtime_store_path = (
        environment.root.parent / "paper" / "runtime.sqlite3"
    )
    with TestClient(api) as client:
        yield client


def _selection(
    environment: _Environment,
    **updates: str,
) -> dict[str, str]:
    selection = {
        "strategy_id": _STRATEGY_ID,
        "content_sha256": _STRATEGY_SHA,
        "contract_id": _CONTRACT_ID,
        "baseline_run_id": _RUN_ID,
        "baseline_result_sha256": sha256(
            environment.main_path.read_bytes()
        ).hexdigest(),
    }
    selection.update(updates)
    return selection


def _readiness_body(environment: _Environment) -> dict[str, object]:
    return {
        "schema": "paper_readiness_request.v1",
        "selection": _selection(environment),
    }


def _create_body(
    environment: _Environment,
    *,
    request_id: str = _REQUEST_ID,
    selection_updates: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "paper_trader_create_request.v1",
        "request_id": request_id,
        "selection": _selection(environment, **(selection_updates or {})),
    }


def _provisioning_body(environment: _Environment) -> dict[str, object]:
    return {
        "schema": "paper_provisioning_readiness_request.v1",
        "selection": _selection(environment),
    }


def _one_off_provisioning_policy(
    environment: _Environment,
    *,
    clock: Callable[[], datetime] | None = None,
    authorized_at: datetime | None = None,
) -> OneOffPaperProvisioningAuthorizationPolicy:
    return OneOffPaperProvisioningAuthorizationPolicy(
        operation_id=_PROVISIONING_OPERATION_ID,
        authorized_selection=PaperTraderSelection.model_validate(
            _selection(environment),
            strict=True,
        ),
        authorized_at=authorized_at or (_CHECKED_AT - timedelta(minutes=1)),
        clock=clock or (lambda: _CHECKED_AT),
    )


def _assert_error(
    response: object,
    *,
    status: int,
    code: str,
) -> None:
    assert hasattr(response, "status_code")
    assert hasattr(response, "json")
    assert response.status_code == status
    payload = response.json()
    assert set(payload) == {"detail"}
    assert set(payload["detail"]) == {
        "schema",
        "code",
        "message",
        "retryable",
    }
    assert payload["detail"]["schema"] == "paper_api_error.v1"
    assert payload["detail"]["code"] == code
    assert payload["detail"]["retryable"] is False
    assert isinstance(payload["detail"]["message"], str)
    assert payload["detail"]["message"]


def _row_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return (
            connection.execute("SELECT count(*) FROM paper_traders").fetchone()[0],
            connection.execute("SELECT count(*) FROM paper_accounts").fetchone()[0],
            connection.execute(
                "SELECT count(*) FROM paper_trader_events"
            ).fetchone()[0],
        )


def _provisioning_origin_payload(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM paper_trader_events
            WHERE ordinal = 3
            """
        ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert isinstance(payload, dict)
    return payload


def _replace_provisioning_origin(
    path: Path,
    payload: dict[str, object],
) -> None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'paper_trader_events_are_append_only'
            """
        ).fetchone()
        assert row is not None and row[0] is not None
        connection.execute(
            "DROP TRIGGER paper_trader_events_are_append_only"
        )
        connection.execute(
            """
            UPDATE paper_trader_events
            SET payload_json = ?
            WHERE ordinal = 3
            """,
            (
                json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(row[0])
        connection.commit()


_V3_TABLES = {
    "paper_accounts",
    "paper_evidence_blobs",
    "paper_ledger_baseline_members",
    "paper_ledger_equity_points",
    "paper_ledger_origins",
    "paper_review_failures",
    "paper_review_ready_artifacts",
    "paper_review_requests",
    "paper_review_status_events",
    "paper_trader_events",
    "paper_traders",
}
_V3_INDEXES = {
    "paper_trader_events_by_trader",
    "paper_traders_by_created",
}
_V3_TRIGGERS = {
    "paper_accounts_are_immutable",
    "paper_accounts_cannot_be_deleted",
    "paper_evidence_blobs_are_immutable",
    "paper_evidence_blobs_cannot_be_deleted",
    "paper_ledger_baseline_members_are_immutable",
    "paper_ledger_baseline_members_cannot_be_deleted",
    "paper_ledger_equity_points_are_immutable",
    "paper_ledger_equity_points_cannot_be_deleted",
    "paper_ledger_origins_are_immutable",
    "paper_ledger_origins_cannot_be_deleted",
    "paper_review_failures_are_immutable",
    "paper_review_failures_cannot_be_deleted",
    "paper_review_ready_artifacts_are_immutable",
    "paper_review_ready_artifacts_cannot_be_deleted",
    "paper_review_requests_are_immutable",
    "paper_review_requests_cannot_be_deleted",
    "paper_review_status_events_are_append_only",
    "paper_review_status_events_cannot_be_deleted",
    "paper_trader_events_are_append_only",
    "paper_trader_events_cannot_be_deleted",
    "paper_traders_are_immutable",
    "paper_traders_cannot_be_deleted",
}


def _all_row_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT count(*) FROM {table}"  # noqa: S608 - closed test constant
            ).fetchone()[0]
            for table in sorted(_V3_TABLES)
        }


def _contract_params() -> dict[str, str]:
    return {
        "strategy_id": _STRATEGY_ID,
        "content_sha256": _STRATEGY_SHA,
    }


def test_contract_candidates_exact_one_and_baseline_parity(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in environment.root.rglob("*")
        if path.is_file()
    }
    decision_before = (
        environment.decisions.path.read_bytes(),
        environment.decisions.path.stat().st_mtime_ns,
    )
    with _client(environment) as client:
        response = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
        baseline = client.get(
            "/api/v1/paper/baselines",
            params={
                **_contract_params(),
                "contract_id": _CONTRACT_ID,
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"schema", "selection", "count", "contracts"}
    assert payload["schema"] == "paper_contract_list.v1"
    assert payload["selection"] == _contract_params()
    assert payload["count"] == len(payload["contracts"]) == 1
    assert payload["contracts"] == [
        {
            "contract_id": _CONTRACT_ID,
            "symbol": "NQ",
            "display_name": "E-mini Nasdaq-100",
        }
    ]
    assert baseline.status_code == 200
    assert baseline.json()["count"] >= 1
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in environment.root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert (
        environment.decisions.path.read_bytes(),
        environment.decisions.path.stat().st_mtime_ns,
    ) == decision_before
    assert not environment.traders.path.exists()


def test_contract_candidates_multiple_order_unique_and_same_symbol(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    _write_result(
        environment.root,
        run_id="p6-baseline-nq-second",
        range_end="2026-07-25T00:00:00Z",
    )
    _write_result(
        environment.root,
        run_id="p6-baseline-ym",
        contract_id="YM-202609-CBOT",
    )
    next_nq = environment.registry.contracts["NQ"].model_copy(
        update={
            "contract_id": "NQ-202612-CME",
            "display_name": "E-mini Nasdaq-100 December",
        }
    )
    contracts = dict(environment.registry.contracts)
    contracts["NQ_NEXT"] = next_nq
    registry = environment.registry.model_copy(update={"contracts": contracts})
    _write_result(
        environment.root,
        run_id="p6-baseline-nq-next",
        contract_id="NQ-202612-CME",
    )
    with _client(environment, registry=registry) as client:
        response = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
        rows = response.json()["contracts"]
        parity = [
            client.get(
                "/api/v1/paper/baselines",
                params={
                    **_contract_params(),
                    "contract_id": row["contract_id"],
                },
            )
            for row in rows
        ]
    assert response.status_code == 200
    assert response.json()["count"] == len(rows) == 3
    assert [row["contract_id"] for row in rows] == [
        "NQ-202609-CME",
        "NQ-202612-CME",
        "YM-202609-CBOT",
    ]
    assert [row["symbol"] for row in rows] == ["NQ", "NQ", "YM"]
    assert all(item.status_code == 200 for item in parity)
    assert all(item.json()["count"] >= 1 for item in parity)


def test_eligible_strategy_with_zero_candidates_returns_empty(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    for path in sorted(environment.root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
    with _client(environment) as client:
        response = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
    assert response.status_code == 200
    assert response.json() == {
        "schema": "paper_contract_list.v1",
        "selection": _contract_params(),
        "count": 0,
        "contracts": [],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"strategy_id": "wrong", "content_sha256": _STRATEGY_SHA},
        {"strategy_id": _STRATEGY_ID, "content_sha256": "b" * 64},
    ],
)
def test_contract_candidates_unknown_identity_soft_empty(
    tmp_path: Path,
    params: dict[str, str],
) -> None:
    """Page independence: unknown strategy identity is empty options, not a gate fail."""
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.get("/api/v1/paper/contracts", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "paper_contract_list.v1"
    assert body["count"] == 0
    assert body["contracts"] == []
    assert body["selection"] == params


@pytest.mark.parametrize("mutation", ["extra", "missing", "blank"])
def test_contract_candidate_query_is_exact_and_strict(
    tmp_path: Path,
    mutation: str,
) -> None:
    environment = _environment(tmp_path)
    params: dict[str, object] = dict(_contract_params())
    if mutation == "extra":
        params["contract_id"] = _CONTRACT_ID
    elif mutation == "missing":
        del params["strategy_id"]
    else:
        params["strategy_id"] = ""
    with _client(environment) as client:
        response = client.get("/api/v1/paper/contracts", params=params)
    assert response.status_code == 422


def test_contract_candidate_corruption_returns_no_partial_list(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    _write_result(
        environment.root,
        run_id="p6-baseline-ym",
        contract_id="YM-202609-CBOT",
    )
    _write_json(
        environment.root / "equity" / "p6-baseline-ym.json",
        {
            "schema": "equity_curve.v1",
            "run_id": "forged-run",
            "points": [],
        },
    )
    with _client(environment) as client:
        response = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


def test_contract_candidate_registry_text_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    contract = environment.registry.contracts["NQ"].model_copy(
        update={"display_name": " "}
    )
    contracts = dict(environment.registry.contracts)
    contracts["NQ"] = contract
    registry = environment.registry.model_copy(update={"contracts": contracts})
    with _client(environment, registry=registry) as client:
        response = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


def test_baseline_and_readiness_exact_contract_zero_trade(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    params = {
        "strategy_id": _STRATEGY_ID,
        "content_sha256": _STRATEGY_SHA,
        "contract_id": _CONTRACT_ID,
    }
    with _client(environment) as client:
        baselines_response = client.get("/api/v1/paper/baselines", params=params)
        readiness_response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )

    assert baselines_response.status_code == 200
    baseline_payload = baselines_response.json()
    assert set(baseline_payload) == {"schema", "selection", "count", "baselines"}
    assert set(baseline_payload["selection"]) == set(params)
    assert baseline_payload["schema"] == "paper_baseline_list.v1"
    assert baseline_payload["count"] == len(baseline_payload["baselines"]) == 1
    baseline = baseline_payload["baselines"][0]
    assert set(baseline) == {
        "run_id",
        "result_sha256",
        "range_start",
        "range_end",
        "currency",
        "initial_capital",
        "trade_count",
        "net_r",
        "validation_run",
        "integrity",
    }
    assert baseline["currency"] == "USD"
    assert baseline["initial_capital"] == 123_456.75
    assert baseline["trade_count"] == 0
    assert baseline["validation_run"] is False
    assert baseline["integrity"] == "verified"

    assert readiness_response.status_code == 200
    readiness = readiness_response.json()
    assert set(readiness) == {
        "schema",
        "selection",
        "overall",
        "market_session",
        "checked_at",
        "checks",
    }
    assert readiness["schema"] == "paper_readiness.v1"
    assert readiness["overall"] == "ready"
    assert readiness["market_session"] == "closed"
    assert [item["key"] for item in readiness["checks"]] == [
        "ib_realtime",
        "exchange_calendar",
        "telegram",
        "baseline_integrity",
    ]
    assert all(item["status"] == "ready" for item in readiness["checks"])
    assert all(item["checked_at"] == readiness["checked_at"] for item in readiness["checks"])


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        " ",
        str(uuid1()),
        str(uuid3(UUID(int=0), "p6")),
        str(uuid5(UUID(int=0), "p6")),
        str(uuid4()).upper(),
        "{" + str(uuid4()) + "}",
        "00000000-0000-4000-7000-000000000000",
    ],
)
def test_create_rejects_noncanonical_uuid4(
    tmp_path: Path,
    request_id: str,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment, request_id=request_id),
        )
    assert response.status_code == 422
    assert not environment.traders.path.exists()


@pytest.mark.parametrize(
    "sha_value",
    ["A" * 64, "a" * 63, "g" * 64, "", " "],
)
def test_requests_reject_noncanonical_sha(
    tmp_path: Path,
    sha_value: str,
) -> None:
    environment = _environment(tmp_path)
    body = _readiness_body(environment)
    selection = body["selection"]
    assert isinstance(selection, dict)
    selection["baseline_result_sha256"] = sha_value
    with _client(environment) as client:
        response = client.post("/api/v1/paper/readiness", json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("mutation", ["extra", "missing", "blank"])
def test_request_models_forbid_extra_missing_and_null(
    tmp_path: Path,
    mutation: str,
) -> None:
    environment = _environment(tmp_path)
    body = _create_body(environment)
    if mutation == "extra":
        body["fallback"] = True
    elif mutation == "missing":
        del body["selection"]
    else:
        body["selection"] = None
    with _client(environment) as client:
        response = client.post("/api/v1/paper/traders", json=body)
    assert response.status_code == 422
    assert not environment.traders.path.exists()


@pytest.mark.parametrize("mutation", ["extra", "missing", "null"])
def test_baseline_query_is_exact_and_strict(
    tmp_path: Path,
    mutation: str,
) -> None:
    environment = _environment(tmp_path)
    params: dict[str, object] = {
        "strategy_id": _STRATEGY_ID,
        "content_sha256": _STRATEGY_SHA,
        "contract_id": _CONTRACT_ID,
    }
    if mutation == "extra":
        params["fallback"] = "latest"
    elif mutation == "missing":
        del params["contract_id"]
    else:
        params["contract_id"] = ""
    with _client(environment) as client:
        response = client.get("/api/v1/paper/baselines", params=params)
    assert response.status_code == 422


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf")])
def test_money_models_reject_bool_and_nonfinite(value: object) -> None:
    with pytest.raises(ValidationError):
        PaperAccountOrigin(
            account_id="paper-account-" + ("2" * 32),
            currency="USD",
            initial_capital=value,
        )


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
)
def test_baseline_money_corruption_fails_closed(
    tmp_path: Path,
    value: object,
) -> None:
    environment = _environment(tmp_path)
    _write_result(environment.root, initial_capital=value)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


@pytest.mark.parametrize(
    ("eligible", "decision"),
    [(False, "use"), (True, "return"), (True, "abandon")],
)
def test_create_does_not_require_historical_use_decision(
    tmp_path: Path,
    eligible: bool,
    decision: Literal["use", "return", "abandon"],
) -> None:
    """Page independence: PromotionDecision is history, not a create gate.

    A verified baseline + provisioning authorization is enough. Missing or
    non-``use`` decisions must not block paper trader creation.
    """
    environment = _environment(tmp_path, eligible=eligible, decision=decision)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    body = response.json()
    assert body["schema"] == "paper_trader.v1"
    assert body["strategy"]["strategy_id"] == _STRATEGY_ID
    assert body["strategy"]["content_sha256"] == _STRATEGY_SHA
    assert environment.traders.path.exists()


@pytest.mark.parametrize(
    "selection_updates",
    [
        {"strategy_id": "strategy-wrong"},
        {"content_sha256": "b" * 64},
        {"contract_id": "YM-202609-CBOT"},
        {"baseline_run_id": "missing-run"},
        {"baseline_result_sha256": "b" * 64},
    ],
)
def test_create_rejects_each_stale_selection_identity(
    tmp_path: Path,
    selection_updates: dict[str, str],
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment, selection_updates=selection_updates),
        )
    _assert_error(
        response,
        status=409,
        code="provisioning_selection_mismatch",
    )
    assert not environment.traders.path.exists()


def test_validation_run_is_neither_listed_nor_creatable(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    _write_result(environment.root, validation_run=True)
    params = {
        "strategy_id": _STRATEGY_ID,
        "content_sha256": _STRATEGY_SHA,
        "contract_id": _CONTRACT_ID,
    }
    with _client(environment) as client:
        contracts = client.get(
            "/api/v1/paper/contracts",
            params=_contract_params(),
        )
        baselines = client.get("/api/v1/paper/baselines", params=params)
        create = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert contracts.status_code == 200
    assert contracts.json()["contracts"] == []
    assert baselines.status_code == 200
    assert baselines.json()["baselines"] == []
    _assert_error(create, status=409, code="selection_identity_mismatch")


@pytest.mark.parametrize("sidecar", ["trades", "equity", "events"])
def test_missing_required_sidecar_fails_closed(
    tmp_path: Path,
    sidecar: str,
) -> None:
    environment = _environment(tmp_path)
    (environment.root / sidecar / f"{_RUN_ID}.json").unlink()
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


def test_corrupt_sidecar_cannot_be_overridden_by_ready_provider(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    _write_json(
        environment.root / "equity" / f"{_RUN_ID}.json",
        {
            "schema": "equity_curve.v1",
            "run_id": "forged-run",
            "points": [],
        },
    )
    with _client(environment, provider=_provider()) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


def test_baselines_have_exact_deterministic_order(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    _write_result(
        environment.root,
        run_id="p6-baseline-z",
        range_end="2026-07-25T00:00:00Z",
    )
    _write_result(
        environment.root,
        run_id="p6-baseline-a",
        range_end="2026-07-25T00:00:00Z",
    )
    _write_result(
        environment.root,
        run_id="p6-baseline-old",
        range_end="2026-07-23T00:00:00Z",
    )
    _write_result(
        environment.root,
        run_id="p6-baseline-fraction",
        range_end="2026-07-24T00:00:00.500000Z",
    )
    with _client(environment) as client:
        response = client.get(
            "/api/v1/paper/baselines",
            params={
                "strategy_id": _STRATEGY_ID,
                "content_sha256": _STRATEGY_SHA,
                "contract_id": _CONTRACT_ID,
            },
        )
    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()["baselines"]] == [
        "p6-baseline-a",
        "p6-baseline-z",
        "p6-baseline-fraction",
        _RUN_ID,
        "p6-baseline-old",
    ]


@pytest.mark.parametrize("currency", ["", "EUR"])
def test_currency_missing_or_non_usd_fails_closed(
    tmp_path: Path,
    currency: str,
) -> None:
    environment = _environment(tmp_path)
    contract = environment.registry.contracts["NQ"].model_copy(
        update={"currency": currency}
    )
    contracts = dict(environment.registry.contracts)
    contracts["NQ"] = contract
    registry = environment.registry.model_copy(update={"contracts": contracts})
    with _client(environment, registry=registry) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


def test_duplicate_contract_truth_fails_closed(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    contracts = dict(environment.registry.contracts)
    contracts["NQ_DUPLICATE"] = contracts["NQ"]
    registry = environment.registry.model_copy(update={"contracts": contracts})
    with _client(environment, registry=registry) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")


@pytest.mark.parametrize(
    ("key", "status"),
    [
        ("ib", "blocked"),
        ("ib", "unknown"),
        ("calendar", "blocked"),
        ("calendar", "unknown"),
        ("telegram", "blocked"),
        ("telegram", "unknown"),
    ],
)
def test_each_external_readiness_state_is_truthfully_persisted(
    tmp_path: Path,
    key: str,
    status: Literal["blocked", "unknown"],
) -> None:
    environment = _environment(tmp_path)
    values: dict[str, Literal["ready", "blocked", "unknown"]] = {
        "ib": "ready",
        "calendar": "ready",
        "telegram": "ready",
    }
    values[key] = status
    provider = _provider(
        ib=values["ib"],
        calendar=values["calendar"],
        telegram=values["telegram"],
    )
    with _client(environment, provider=provider) as client:
        readiness = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
        create = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert readiness.status_code == 200
    assert readiness.json()["overall"] == "blocked"
    assert create.status_code == 201
    assert create.json()["readiness_snapshot"]["overall"] == "blocked"
    assert create.json()["readiness_snapshot"]["checks"] == readiness.json()["checks"]
    assert _row_counts(environment.traders.path) == (1, 1, 4)


def test_closed_market_does_not_block_and_normal_provider_never_claims_ready(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment, provider=_provider(market_session="closed")) as client:
        closed = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    with _client(
        environment,
        provider=DefaultPaperExternalReadinessProvider(clock=lambda: _CHECKED_AT),
    ) as client:
        normal = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    assert closed.status_code == 200
    assert closed.json()["overall"] == "ready"
    assert closed.json()["market_session"] == "closed"
    assert normal.status_code == 200
    assert normal.json()["overall"] == "blocked"
    assert [check["status"] for check in normal.json()["checks"][:3]] == [
        "unknown",
        "unknown",
        "unknown",
    ]


def test_provisioning_preflight_normal_policy_is_disabled_and_read_only(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    result_before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in environment.root.rglob("*")
        if path.is_file()
    }
    decision_before = (
        environment.decisions.path.read_bytes(),
        environment.decisions.path.stat().st_mtime_ns,
    )
    with _client(
        environment,
        provisioning_policy=DisabledPaperProvisioningAuthorizationPolicy(),
        provider=DefaultPaperExternalReadinessProvider(
            clock=lambda: _CHECKED_AT
        ),
    ) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "schema",
        "selection",
        "can_provision",
        "authorization",
        "runtime_readiness",
    }
    assert payload["schema"] == "paper_provisioning_readiness.v1"
    assert payload["selection"] == _selection(environment)
    assert payload["can_provision"] is False
    assert payload["authorization"] == {
        "schema": "paper_provisioning_authorization.v1",
        "state": "disabled",
        "operation_id": None,
        "authorized_at": None,
        "expires_at": None,
        "reason": "paper trader provisioning is disabled in the normal runtime",
    }
    runtime = payload["runtime_readiness"]
    assert runtime["schema"] == "paper_readiness.v1"
    assert runtime["selection"] == payload["selection"]
    assert runtime["overall"] == "blocked"
    assert runtime["market_session"] == "unknown"
    assert [check["status"] for check in runtime["checks"]] == [
        "unknown",
        "unknown",
        "unknown",
        "ready",
    ]
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in environment.root.rglob("*")
        if path.is_file()
    } == result_before
    assert (
        environment.decisions.path.read_bytes(),
        environment.decisions.path.stat().st_mtime_ns,
    ) == decision_before
    assert not environment.traders.path.exists()


def test_provisioning_preflight_rejects_schema_version_without_side_effects(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    default_paper_root = _PROJECT_ROOT / "data" / "paper"
    assert not environment.traders.path.exists()
    assert not default_paper_root.exists()
    protected_before = {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    body = _provisioning_body(environment)
    body["schema_version"] = body.pop("schema")

    with _client(
        environment,
        provisioning_policy=policy,
        provider=DefaultPaperExternalReadinessProvider(
            clock=lambda: _CHECKED_AT
        ),
    ) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=body,
        )

    assert response.status_code == 422
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(
            _selection(environment),
            strict=True,
        )
    ).state == "armed"
    assert not environment.traders.path.exists()
    assert not default_paper_root.exists()
    assert {
        path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == protected_before


def test_armed_preflight_keeps_blocked_runtime_and_never_claims(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    provider = DefaultPaperExternalReadinessProvider(
        clock=lambda: _CHECKED_AT
    )
    with _client(
        environment,
        provisioning_policy=policy,
        provider=provider,
    ) as client:
        first = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )
        second = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    payload = first.json()
    assert payload["can_provision"] is True
    assert payload["authorization"] == {
        "schema": "paper_provisioning_authorization.v1",
        "state": "armed",
        "operation_id": _PROVISIONING_OPERATION_ID,
        "authorized_at": "2026-07-28T23:59:00Z",
        "expires_at": "2026-07-29T00:29:00Z",
        "reason": (
            "Owner-approved one-off P6 provision-only flow verification"
        ),
    }
    runtime = payload["runtime_readiness"]
    assert runtime["overall"] == "blocked"
    assert [check["status"] for check in runtime["checks"]] == [
        "unknown",
        "unknown",
        "unknown",
        "ready",
    ]
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(
            _selection(environment),
            strict=True,
        )
    ).state == "armed"
    assert not environment.traders.path.exists()


def test_preflight_all_closed_authorization_states_return_200(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    selection = PaperTraderSelection.model_validate(
        _selection(environment),
        strict=True,
    )
    request_id = "00000000-0000-4000-8000-000000000031"
    payload_sha = "e" * 64

    armed = _one_off_provisioning_policy(environment)
    claimed = _one_off_provisioning_policy(environment)
    claimed.claim(
        request_id=request_id,
        request_payload_sha256=payload_sha,
        selection=selection,
    )
    consumed = _one_off_provisioning_policy(environment)
    consumed.claim(
        request_id=request_id,
        request_payload_sha256=payload_sha,
        selection=selection,
    )
    consumed.mark_consumed(
        request_id=request_id,
        request_payload_sha256=payload_sha,
        selection=selection,
    )
    expired = _one_off_provisioning_policy(
        environment,
        authorized_at=_CHECKED_AT - timedelta(minutes=30),
    )
    policies = {
        "disabled": DisabledPaperProvisioningAuthorizationPolicy(),
        "armed": armed,
        "claimed": claimed,
        "consumed": consumed,
        "expired": expired,
    }

    for expected_state, policy in policies.items():
        with _client(
            environment,
            provisioning_policy=policy,
            provider=DefaultPaperExternalReadinessProvider(
                clock=lambda: _CHECKED_AT
            ),
        ) as client:
            response = client.post(
                "/api/v1/paper/provisioning-readiness",
                json=_provisioning_body(environment),
            )
        assert response.status_code == 200
        assert response.json()["authorization"]["state"] == expected_state
        assert response.json()["can_provision"] is (expected_state == "armed")

    assert not environment.traders.path.exists()


def test_wrong_typed_provisioning_policy_fails_closed_to_disabled(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(
        environment,
        provisioning_policy=object(),
    ) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )
    assert response.status_code == 200
    assert response.json()["authorization"]["state"] == "disabled"
    assert response.json()["can_provision"] is False
    assert not environment.traders.path.exists()


def test_provisioning_preflight_exact_business_errors(
    tmp_path: Path,
) -> None:
    # Page independence: no PromotionDecision must not block preflight.
    no_promotion = _environment(tmp_path / "no-promotion", eligible=False)
    with _client(no_promotion) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(no_promotion),
        )
    assert response.status_code == 200
    assert response.json()["schema"] == "paper_provisioning_readiness.v1"

    missing = _environment(tmp_path / "missing")
    missing_body = _provisioning_body(missing)
    missing_selection = missing_body["selection"]
    assert isinstance(missing_selection, dict)
    missing_selection["baseline_run_id"] = "missing-run"
    with _client(missing) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=missing_body,
        )
    _assert_error(response, status=404, code="baseline_not_found")

    stale = _environment(tmp_path / "stale")
    stale_body = _provisioning_body(stale)
    stale_selection = stale_body["selection"]
    assert isinstance(stale_selection, dict)
    stale_selection["contract_id"] = "YM-202609-CBOT"
    with _client(stale) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=stale_body,
        )
    _assert_error(response, status=409, code="selection_identity_mismatch")

    corrupt = _environment(tmp_path / "corrupt")
    (corrupt.root / "events" / f"{_RUN_ID}.json").unlink()
    with _client(corrupt) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(corrupt),
        )
    _assert_error(response, status=503, code="baseline_integrity_failed")

    malformed = _environment(tmp_path / "malformed")
    with _client(
        malformed,
        provider=_MalformedReadinessProvider(),
    ) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(malformed),
        )
    _assert_error(response, status=503, code="external_readiness_invalid")


def test_preflight_never_accesses_store_or_creates_default_p6(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    forbidden_path = tmp_path / "must-not-open" / "paper-traders.sqlite3"
    forbidden_store = _StoreMustNotBeTouched(forbidden_path)
    default_paper_root = _PROJECT_ROOT / "data" / "paper"
    assert not default_paper_root.exists()
    with _client(
        environment,
        trader_store=forbidden_store,
        provisioning_policy=_one_off_provisioning_policy(environment),
        provider=DefaultPaperExternalReadinessProvider(
            clock=lambda: _CHECKED_AT
        ),
    ) as client:
        response = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )
    assert response.status_code == 200
    assert response.json()["can_provision"] is True
    assert not forbidden_path.exists()
    assert not default_paper_root.exists()


def test_preflight_does_not_claim_but_create_consumes_exact_permit(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    with _client(
        environment,
        provisioning_policy=policy,
        provider=_provider(),
    ) as client:
        readiness = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
        preflight = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )
        create = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )

    assert readiness.status_code == 200
    assert readiness.json()["schema"] == "paper_readiness.v1"
    assert readiness.json()["overall"] == "ready"
    assert preflight.status_code == 200
    assert preflight.json()["can_provision"] is True
    assert create.status_code == 201
    assert create.json()["schema"] == "paper_trader.v1"
    assert _row_counts(environment.traders.path) == (1, 1, 4)
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(
            _selection(environment),
            strict=True,
        )
    ).state == "consumed"


@dataclass(frozen=True)
class _MalformedReadinessProvider:
    def check(self, _selection: object) -> PaperExternalReadiness:
        return PaperExternalReadiness(
            checked_at=_CHECKED_AT,
            market_session=cast(
                Literal["open", "closed", "unknown"],
                "halted",
            ),
            ib_realtime=PaperReadinessSignal("ready", "isolated ready"),
            exchange_calendar=PaperReadinessSignal("ready", "isolated ready"),
            telegram=PaperReadinessSignal("ready", "isolated ready"),
        )


def test_malformed_external_readiness_fails_closed(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with _client(environment, provider=_MalformedReadinessProvider()) as client:
        response = client.post(
            "/api/v1/paper/readiness",
            json=_readiness_body(environment),
        )
    _assert_error(response, status=409, code="readiness_blocked")


class _StoreMustNotBeTouched(PaperTraderStore):
    def replay(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
    ) -> None:
        del request_id, request_payload_sha256
        raise AssertionError("create denial must occur before store access")

    def append(self, **_kwargs: object) -> NoReturn:
        raise AssertionError("create denial must occur before store access")

    def list(self) -> NoReturn:
        raise AssertionError("provisioning preflight must not access the store")


def test_create_and_preflight_share_malformed_provider_error_before_claim_or_store(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    forbidden_path = tmp_path / "malformed-denied" / "paper-traders.sqlite3"
    forbidden_store = _StoreMustNotBeTouched(forbidden_path)

    with _client(
        environment,
        provisioning_policy=policy,
        provider=_MalformedReadinessProvider(),
        trader_store=forbidden_store,
    ) as client:
        preflight = client.post(
            "/api/v1/paper/provisioning-readiness",
            json=_provisioning_body(environment),
        )
        create = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )

    _assert_error(
        preflight,
        status=503,
        code="external_readiness_invalid",
    )
    _assert_error(
        create,
        status=503,
        code="external_readiness_invalid",
    )
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(
            _selection(environment),
            strict=True,
        )
    ).state == "armed"
    assert not forbidden_path.exists()


def test_disabled_provisioning_denies_before_any_store_access(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    denied_path = tmp_path / "denied" / "paper-traders.sqlite3"
    denied_store = _StoreMustNotBeTouched(denied_path)
    with _client(
        environment,
        activation=AllowIsolatedPaperActivationPolicy(),
        provisioning_policy=DisabledPaperProvisioningAuthorizationPolicy(),
        trader_store=denied_store,
    ) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    _assert_error(response, status=503, code="provisioning_not_authorized")
    assert not denied_path.exists()


def test_a2_registered_create_uses_policy_and_persists_exact_v3_origin(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    body = _create_body(environment)
    with _client(
        environment,
        activation=DefaultDenyPaperActivationPolicy(),
        provisioning_policy=policy,
        provider=DefaultPaperExternalReadinessProvider(
            clock=lambda: _CHECKED_AT
        ),
    ) as client:
        response = client.post("/api/v1/paper/traders", json=body)

    assert response.status_code == 201, response.text
    trader = response.json()
    assert trader["readiness_snapshot"]["overall"] == "blocked"
    assert [check["status"] for check in trader["readiness_snapshot"]["checks"]] == [
        "unknown",
        "unknown",
        "unknown",
        "ready",
    ]
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(_selection(environment), strict=True)
    ).state == "consumed"
    with sqlite3.connect(environment.traders.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        rows = connection.execute(
            """
            SELECT ordinal, event_type, payload_json, occurred_at
            FROM paper_trader_events
            ORDER BY ordinal
            """
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (1, "trader_created"),
        (2, "account_created"),
        (3, "provisioning_authorized"),
        (4, "provisioned"),
    ]
    origin = json.loads(rows[2][2])
    assert origin == {
        "schema": "paper_provisioning_origin.v1",
        "operation_id": _PROVISIONING_OPERATION_ID,
        "request_id": _REQUEST_ID,
        "request_payload_sha256": sha256(
            json.dumps(
                body,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "selection": _selection(environment),
        "authorized_at": "2026-07-28T23:59:00Z",
        "expires_at": "2026-07-29T00:29:00Z",
        "claimed_at": "2026-07-29T00:00:00Z",
        "reason": (
            "Owner-approved one-off P6 provision-only flow verification"
        ),
        "runtime_readiness": trader["readiness_snapshot"],
    }
    assert rows[2][3] == trader["created_at"]


def test_provisioning_origin_model_requires_exact_keys_and_time_order(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    payload = _provisioning_origin_payload(environment.traders.path)

    parsed = PaperProvisioningOrigin.model_validate_json(
        json.dumps(payload),
        strict=True,
    )
    assert parsed.model_dump(by_alias=True, mode="json") == payload

    for missing in (
        "operation_id",
        "request_id",
        "request_payload_sha256",
        "selection",
        "authorized_at",
        "expires_at",
        "claimed_at",
        "reason",
        "runtime_readiness",
    ):
        drifted = json.loads(json.dumps(payload))
        del drifted[missing]
        with pytest.raises(ValidationError):
            PaperProvisioningOrigin.model_validate_json(
                json.dumps(drifted),
                strict=True,
            )

    alias_only = json.loads(json.dumps(payload))
    alias_only["schema_version"] = alias_only.pop("schema")
    with pytest.raises(ValidationError):
        PaperProvisioningOrigin.model_validate_json(
            json.dumps(alias_only),
            strict=True,
        )

    invalid_order = json.loads(json.dumps(payload))
    invalid_order["claimed_at"] = invalid_order["expires_at"]
    with pytest.raises(ValidationError, match="authorized <= claimed < expires"):
        PaperProvisioningOrigin.model_validate_json(
            json.dumps(invalid_order),
            strict=True,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "request_id",
        "payload_sha",
        "selection",
        "runtime",
        "claimed_after_create",
        "missing_key",
    ],
)
def test_v3_origin_identity_drift_fails_closed_without_read_mutation(
    tmp_path: Path,
    corruption: str,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    payload = _provisioning_origin_payload(environment.traders.path)
    if corruption == "request_id":
        payload["request_id"] = "00000000-0000-4000-8000-000000000099"
    elif corruption == "payload_sha":
        payload["request_payload_sha256"] = "b" * 64
    elif corruption == "selection":
        selection = payload["selection"]
        assert isinstance(selection, dict)
        selection["contract_id"] = "YM-202609-CBOT"
    elif corruption == "runtime":
        runtime = payload["runtime_readiness"]
        assert isinstance(runtime, dict)
        checks = runtime["checks"]
        assert isinstance(checks, list)
        first = checks[0]
        assert isinstance(first, dict)
        first["reason"] = "structurally valid but forged readiness reason"
    elif corruption == "claimed_after_create":
        payload["claimed_at"] = "2026-07-29T00:01:00Z"
    else:
        del payload["reason"]
    _replace_provisioning_origin(environment.traders.path, payload)
    before = (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    )

    with pytest.raises(
        PaperTraderStoreIntegrityError,
        match="invalid immutable record",
    ):
        environment.traders.list()

    assert (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    ) == before


def test_v3_event_constraint_rejects_historical_ordinal_three_name(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201

    with sqlite3.connect(environment.traders.path) as connection:
        row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'paper_trader_events_are_append_only'
            """
        ).fetchone()
        assert row is not None and row[0] is not None
        connection.execute(
            "DROP TRIGGER paper_trader_events_are_append_only"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE paper_trader_events
                SET event_type = 'readiness_passed'
                WHERE ordinal = 3
                """
            )
        connection.execute(row[0])
        connection.commit()


def test_a2_disabled_denial_precedes_store_even_if_old_activation_allows(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    denied_path = tmp_path / "a2-denied" / "paper-traders.sqlite3"
    denied_store = _StoreMustNotBeTouched(denied_path)

    with _client(
        environment,
        activation=AllowIsolatedPaperActivationPolicy(),
        provisioning_policy=DisabledPaperProvisioningAuthorizationPolicy(),
        trader_store=denied_store,
    ) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )

    _assert_error(
        response,
        status=503,
        code="provisioning_not_authorized",
    )
    assert not denied_path.exists()


@pytest.mark.parametrize("denial", ["expired", "mismatch"])
def test_expired_and_mismatch_deny_before_any_store_access(
    tmp_path: Path,
    denial: str,
) -> None:
    environment = _environment(tmp_path)
    denied_path = tmp_path / denial / "paper-traders.sqlite3"
    denied_store = _StoreMustNotBeTouched(denied_path)
    if denial == "expired":
        policy = _one_off_provisioning_policy(
            environment,
            authorized_at=_CHECKED_AT - timedelta(minutes=30),
        )
        body = _create_body(environment)
        expected = (503, "provisioning_not_authorized")
    else:
        policy = _one_off_provisioning_policy(environment)
        body = _create_body(
            environment,
            selection_updates={"contract_id": "YM-202609-CBOT"},
        )
        expected = (409, "provisioning_selection_mismatch")

    with _client(
        environment,
        provisioning_policy=policy,
        trader_store=denied_store,
    ) as client:
        response = client.post("/api/v1/paper/traders", json=body)

    _assert_error(response, status=expected[0], code=expected[1])
    assert not denied_path.exists()


def test_two_request_create_race_claims_one_and_writes_one_transaction(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    trader_ids = iter(
        ["trader-" + ("4" * 32), "trader-" + ("5" * 32)]
    )
    account_ids = iter(
        ["paper-account-" + ("6" * 32), "paper-account-" + ("7" * 32)]
    )
    ledger_ids = iter(
        ["paper-ledger-" + ("8" * 32), "paper-ledger-" + ("9" * 32)]
    )
    store = PaperTraderStore(
        tmp_path / "race" / "paper-traders.sqlite3",
        clock=lambda: _CHECKED_AT,
        trader_id_factory=lambda: next(trader_ids),
        account_id_factory=lambda: next(account_ids),
        ledger_origin_id_factory=lambda: next(ledger_ids),
    )
    request_ids = (
        _REQUEST_ID,
        "00000000-0000-4000-8000-000000000002",
    )

    def attempt(request_id: str) -> tuple[str, bool | None]:
        body = PaperTraderCreateRequest.model_validate(
            _create_body(environment, request_id=request_id)
        )
        try:
            record, created = create_paper_trader(
                body=body,
                catalog=environment.catalog,
                registry=environment.registry,
                eligibility_store=environment.decisions,
                readiness_provider=_provider(),
                authorization_policy=policy,
                trader_store=store,
            )
            return record.request_id, created
        except PaperProvisioningRequestConflictError:
            return request_id, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, request_ids))

    assert sum(created is True for _request_id, created in results) == 1
    assert sum(created is None for _request_id, created in results) == 1
    assert _row_counts(store.path) == (1, 1, 4)
    assert _all_row_counts(store.path) == {
        "paper_accounts": 1,
        "paper_evidence_blobs": 4,
        "paper_ledger_baseline_members": 4,
        "paper_ledger_equity_points": 1,
        "paper_ledger_origins": 1,
        "paper_review_failures": 0,
        "paper_review_ready_artifacts": 0,
        "paper_review_requests": 0,
        "paper_review_status_events": 0,
        "paper_trader_events": 4,
        "paper_traders": 1,
    }
    assert policy.authorization_for(
        PaperTraderSelection.model_validate(_selection(environment), strict=True)
    ).state == "consumed"


class _PauseFirstClaimAfterBindingPolicy:
    def __init__(
        self,
        delegate: OneOffPaperProvisioningAuthorizationPolicy,
    ) -> None:
        self._delegate = delegate
        self._gate_lock = Lock()
        self._pause_next_claim = True
        self.first_claimed = Event()
        self.consumed = Event()

    def authorization_for(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        return self._delegate.authorization_for(selection)

    def inspect_for_create(
        self,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningCreateInspection:
        return self._delegate.inspect_for_create(selection)

    def claim(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        authorization = self._delegate.claim(
            request_id=request_id,
            request_payload_sha256=request_payload_sha256,
            selection=selection,
        )
        with self._gate_lock:
            pause = self._pause_next_claim
            self._pause_next_claim = False
        if pause:
            self.first_claimed.set()
            if not self.consumed.wait(timeout=10):
                raise AssertionError(
                    "same-binding duplicate did not consume the permit"
                )
        return authorization

    def record_append_failure(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        return self._delegate.record_append_failure(
            request_id=request_id,
            request_payload_sha256=request_payload_sha256,
            selection=selection,
        )

    def mark_consumed(
        self,
        *,
        request_id: str,
        request_payload_sha256: str,
        selection: PaperTraderSelection,
    ) -> PaperProvisioningAuthorization:
        authorization = self._delegate.mark_consumed(
            request_id=request_id,
            request_payload_sha256=request_payload_sha256,
            selection=selection,
        )
        self.consumed.set()
        return authorization


def test_same_binding_claimed_to_consumed_interleaving_replays_exact_record(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    delegate = _one_off_provisioning_policy(environment)
    policy = _PauseFirstClaimAfterBindingPolicy(delegate)

    def create() -> tuple[str, bool]:
        record, created = create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=environment.traders,
        )
        return record.trader_id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(create)
        assert policy.first_claimed.wait(timeout=10)
        second = executor.submit(create)
        results = (second.result(timeout=10), first.result(timeout=10))

    assert {trader_id for trader_id, _created in results} == {
        "trader-" + ("1" * 32)
    }
    assert sorted(created for _trader_id, created in results) == [False, True]
    assert _row_counts(environment.traders.path) == (1, 1, 4)
    assert delegate.authorization_for(body.selection).state == "consumed"


def test_consumed_same_binding_without_record_fails_closed(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    fingerprint = paper_request_payload_sha256(body)
    policy = _one_off_provisioning_policy(environment)
    policy.claim(
        request_id=body.request_id,
        request_payload_sha256=fingerprint,
        selection=body.selection,
    )
    policy.mark_consumed(
        request_id=body.request_id,
        request_payload_sha256=fingerprint,
        selection=body.selection,
    )

    with pytest.raises(
        PaperTraderStoreIntegrityError,
        match="consumed provisioning authority has no immutable record",
    ):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=environment.traders,
        )

    assert not environment.traders.path.exists()
    assert policy.authorization_for(body.selection).state == "consumed"


def test_registered_create_maps_other_binding_to_exact_conflict(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    with _client(
        environment,
        provisioning_policy=policy,
    ) as client:
        first = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
        other = client.post(
            "/api/v1/paper/traders",
            json=_create_body(
                environment,
                request_id="00000000-0000-4000-8000-000000000002",
            ),
        )

    assert first.status_code == 201
    _assert_error(
        other,
        status=409,
        code="provisioning_request_conflict",
    )
    assert _row_counts(environment.traders.path) == (1, 1, 4)


def test_claimed_store_failure_retries_same_binding_and_rejects_other(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    failed = False

    def fail_once(step: str) -> None:
        nonlocal failed
        if step == "paper_trader_events" and not failed:
            failed = True
            raise RuntimeError("injected one-time append failure")

    store = PaperTraderStore(
        tmp_path / "retry" / "paper-traders.sqlite3",
        clock=lambda: _CHECKED_AT,
        trader_id_factory=lambda: "trader-" + ("4" * 32),
        account_id_factory=lambda: "paper-account-" + ("5" * 32),
        ledger_origin_id_factory=lambda: "paper-ledger-" + ("6" * 32),
        append_step_hook=fail_once,
    )
    policy = _one_off_provisioning_policy(environment)
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))

    with pytest.raises(RuntimeError, match="one-time append failure"):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=store,
        )

    assert _all_row_counts(store.path) == {
        table: 0 for table in _V3_TABLES
    }
    assert policy.authorization_for(body.selection).state == "claimed"
    other = PaperTraderCreateRequest.model_validate(
        _create_body(
            environment,
            request_id="00000000-0000-4000-8000-000000000002",
        )
    )
    with pytest.raises(PaperProvisioningRequestConflictError):
        create_paper_trader(
            body=other,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=store,
        )
    assert _all_row_counts(store.path) == {
        table: 0 for table in _V3_TABLES
    }

    record, created = create_paper_trader(
        body=body,
        catalog=environment.catalog,
        registry=environment.registry,
        eligibility_store=environment.decisions,
        readiness_provider=_provider(),
        authorization_policy=policy,
        trader_store=store,
    )

    assert created is True
    assert record.request_id == _REQUEST_ID
    assert _row_counts(store.path) == (1, 1, 4)
    assert policy.authorization_for(body.selection).state == "consumed"


def test_post_commit_unknown_outcome_recovers_without_second_record(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    policy = _one_off_provisioning_policy(environment)
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    real_store = environment.traders

    class _RaiseOnceAfterCommit:
        def __init__(self) -> None:
            self.raised = False

        def replay(self, **kwargs: object) -> object:
            return real_store.replay(**kwargs)  # type: ignore[arg-type]

        def append(self, **kwargs: object) -> object:
            result = real_store.append(**kwargs)  # type: ignore[arg-type]
            if not self.raised:
                self.raised = True
                raise RuntimeError("injected post-commit unknown outcome")
            return result

    unknown_store = _RaiseOnceAfterCommit()
    with pytest.raises(RuntimeError, match="post-commit unknown"):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=unknown_store,  # type: ignore[arg-type]
        )

    recovered = real_store.request_status(_REQUEST_ID)
    assert recovered is not None
    assert recovered.status == "completed"
    assert _row_counts(real_store.path) == (1, 1, 4)
    assert policy.authorization_for(body.selection).state == "claimed"

    replay, created = create_paper_trader(
        body=body,
        catalog=environment.catalog,
        registry=environment.registry,
        eligibility_store=environment.decisions,
        readiness_provider=_provider(),
        authorization_policy=policy,
        trader_store=unknown_store,  # type: ignore[arg-type]
    )

    assert created is False
    assert replay == recovered.trader
    assert _row_counts(real_store.path) == (1, 1, 4)
    assert policy.authorization_for(body.selection).state == "consumed"


def test_restart_is_disabled_for_post_but_get_recovers_existing_record(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    body = _create_body(environment)
    with _client(environment) as client:
        created = client.post("/api/v1/paper/traders", json=body)
    assert created.status_code == 201
    before = (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    )

    with _client(
        environment,
        activation=AllowIsolatedPaperActivationPolicy(),
        provisioning_policy=DisabledPaperProvisioningAuthorizationPolicy(),
    ) as restarted:
        denied = restarted.post("/api/v1/paper/traders", json=body)
        recovered = restarted.get(
            f"/api/v1/paper/trader-requests/{_REQUEST_ID}"
        )

    _assert_error(
        denied,
        status=503,
        code="provisioning_not_authorized",
    )
    assert recovered.status_code == 200
    assert recovered.json()["trader"] == created.json()
    assert (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    ) == before
    assert _row_counts(environment.traders.path) == (1, 1, 4)


def test_create_replay_reads_and_exact_persistence(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    body = _create_body(environment)
    with _client(environment) as client:
        first = client.post("/api/v1/paper/traders", json=body)
        replay = client.post("/api/v1/paper/traders", json=body)
        listing = client.get("/api/v1/paper/traders")
        request = client.get(
            f"/api/v1/paper/trader-requests/{_REQUEST_ID}"
        )
        assert first.status_code == 201, first.text
        trader_id = first.json()["trader_id"]
        detail = client.get(f"/api/v1/paper/traders/{trader_id}")

    assert replay.status_code == 200
    assert first.content == replay.content
    trader = first.json()
    assert set(trader) == {
        "schema",
        "trader_id",
        "request_id",
        "strategy",
        "contract_id",
        "baseline",
        "account",
        "safeguards",
        "lifecycle",
        "readiness_snapshot",
        "created_at",
    }
    assert trader["schema"] == "paper_trader.v1"
    assert trader["account"]["currency"] == "USD"
    assert trader["account"]["initial_capital"] == 123_456.75
    assert trader["safeguards"] == {
        "max_drawdown_r": 8,
        "max_losing_streak": 8,
        "blind_minutes": 5,
    }
    assert trader["lifecycle"] == {
        "status": "provisioned",
        "reason": "simulation runtime is not activated in Stage A",
        "as_of": "2026-07-29T00:00:00Z",
    }
    assert listing.status_code == 200
    assert listing.json() == {
        "schema": "paper_trader_list.v1",
        "count": 1,
        "traders": [trader],
    }
    assert request.status_code == 200
    assert request.json() == {
        "schema": "paper_trader_request_status.v1",
        "request_id": _REQUEST_ID,
        "status": "completed",
        "trader": trader,
    }
    assert detail.status_code == 200
    assert detail.json() == trader
    assert _row_counts(environment.traders.path) == (1, 1, 4)

    with sqlite3.connect(environment.traders.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        events = connection.execute(
            """
            SELECT ordinal, event_type
            FROM paper_trader_events
            ORDER BY ordinal
            """
        ).fetchall()
        objects = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    assert events == [
        (1, "trader_created"),
        (2, "account_created"),
        (3, "provisioning_authorized"),
        (4, "provisioned"),
    ]
    assert {item[1] for item in objects if item[0] == "table"} == _V3_TABLES
    assert {item[1] for item in objects if item[0] == "index"} == _V3_INDEXES
    assert {item[1] for item in objects if item[0] == "trigger"} == _V3_TRIGGERS
    with sqlite3.connect(environment.traders.path) as connection:
        fingerprint = connection.execute(
            "SELECT request_payload_sha256 FROM paper_traders"
        ).fetchone()[0]
    assert fingerprint == sha256(
        json.dumps(
            body,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    counts = _all_row_counts(environment.traders.path)
    assert counts == {
        "paper_accounts": 1,
        "paper_evidence_blobs": 4,
        "paper_ledger_baseline_members": 4,
        "paper_ledger_equity_points": 1,
        "paper_ledger_origins": 1,
        "paper_review_failures": 0,
        "paper_review_ready_artifacts": 0,
        "paper_review_requests": 0,
        "paper_review_status_events": 0,
        "paper_trader_events": 4,
        "paper_traders": 1,
    }
    assert not Path(f"{environment.traders.path}-journal").exists()
    assert not Path(f"{environment.traders.path}-wal").exists()
    assert not Path(f"{environment.traders.path}-shm").exists()


def test_replay_rejects_unsafe_stored_run_before_path_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment(tmp_path)
    body = _create_body(environment)
    with _client(environment) as client:
        created = client.post("/api/v1/paper/traders", json=body)
    assert created.status_code == 201

    unsafe_run_id = "../escape"
    unsafe_request = PaperTraderCreateRequest.model_validate(
        _create_body(
            environment,
            selection_updates={"baseline_run_id": unsafe_run_id},
        )
    )
    unsafe_fingerprint = paper_request_payload_sha256(unsafe_request)
    trigger_names = (
        "paper_traders_are_immutable",
        "paper_trader_events_are_append_only",
        "paper_ledger_origins_are_immutable",
    )
    with sqlite3.connect(environment.traders.path) as connection:
        trigger_sql = connection.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN (?, ?, ?)
            ORDER BY name
            """,
            trigger_names,
        ).fetchall()
        assert len(trigger_sql) == len(trigger_names)
        for name, _sql in trigger_sql:
            connection.execute(f"DROP TRIGGER {name}")
        event_payload = json.loads(
            connection.execute(
                """
                SELECT payload_json
                FROM paper_trader_events
                WHERE ordinal = 1
                """
            ).fetchone()[0]
        )
        event_payload["baseline"]["run_id"] = unsafe_run_id
        connection.execute(
            """
            UPDATE paper_traders
            SET baseline_run_id = ?, request_payload_sha256 = ?
            """,
            (unsafe_run_id, unsafe_fingerprint),
        )
        connection.execute(
            """
            UPDATE paper_trader_events
            SET payload_json = ?
            WHERE ordinal = 1
            """,
            (
                json.dumps(
                    event_payload,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(
            "UPDATE paper_ledger_origins SET baseline_run_id = ?",
            (unsafe_run_id,),
        )
        for _name, sql in trigger_sql:
            assert sql is not None
            connection.execute(sql)
        connection.commit()

    def path_generation_must_not_run(_run_id: str) -> tuple[str, ...]:
        raise AssertionError("unsafe stored run reached path generation")

    monkeypatch.setattr(
        paper_ledger,
        "_required_export_paths",
        path_generation_must_not_run,
    )
    monkeypatch.setattr(
        paper_ledger,
        "_ledger_paths",
        path_generation_must_not_run,
    )

    with pytest.raises(
        PaperTraderStoreIntegrityError,
        match="invalid immutable record",
    ):
        environment.traders.replay(
            request_id=_REQUEST_ID,
            request_payload_sha256=unsafe_fingerprint,
        )
    with _client(environment) as client:
        response = client.get(
            f"/api/v1/paper/trader-requests/{_REQUEST_ID}"
        )
    _assert_error(response, status=503, code="store_unavailable")


def test_create_persists_exact_ledger_origin_equity_and_raw_members(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    trader = response.json()

    with sqlite3.connect(environment.traders.path) as connection:
        connection.row_factory = sqlite3.Row
        origin = connection.execute(
            "SELECT * FROM paper_ledger_origins"
        ).fetchone()
        equity = connection.execute(
            "SELECT * FROM paper_ledger_equity_points"
        ).fetchone()
        members = connection.execute(
            """
            SELECT member.ordinal, member.zip_path, member.byte_count, member.sha256,
                   blob.byte_count AS blob_byte_count, blob.payload
            FROM paper_ledger_baseline_members AS member
            JOIN paper_evidence_blobs AS blob ON blob.sha256 = member.sha256
            ORDER BY member.ordinal
            """
        ).fetchall()
    assert origin is not None
    assert equity is not None
    assert origin["ledger_origin_id"] == "paper-ledger-" + ("3" * 32)
    assert origin["trader_id"] == trader["trader_id"]
    assert origin["account_id"] == trader["account"]["account_id"]
    assert origin["origin_at"] == origin["created_at"] == trader["created_at"]
    assert origin["strategy_id"] == _STRATEGY_ID
    assert origin["strategy_name"] == "P6 locked strategy"
    assert origin["strategy_content_sha256"] == _STRATEGY_SHA
    assert origin["contract_id"] == _CONTRACT_ID
    assert origin["contract_exchange"] == "CME"
    assert origin["contract_timezone"] == "America/Chicago"
    assert origin["baseline_run_id"] == _RUN_ID
    assert origin["baseline_result_sha256"] == trader["baseline"]["result_sha256"]
    assert origin["currency"] == "USD"
    assert origin["initial_capital"] == 123_456.75
    assert origin["cash"] == origin["equity"] == 123_456.75
    assert origin["realized_pnl"] == origin["unrealized_pnl"] == 0
    assert origin["independent_account"] == 1
    assert origin["lifecycle_status"] == "provisioned"
    assert origin["engine_status"] == "not_enabled"
    assert origin["safety_state"] == "not_running"
    assert origin["drawdown_r"] == origin["loss_streak"] == 0
    assert (
        origin["trades_hwm"],
        origin["equity_hwm"],
        origin["events_hwm"],
        origin["expected_decisions_hwm"],
        origin["positions_hwm"],
        origin["orders_hwm"],
    ) == (0, 1, 4, 0, 0, 0)
    assert origin["baseline_rejection_count"] == 0
    assert origin["closest_algorithm"] == "p5_structural_closest.v1"
    assert json.loads(origin["closest_rejection_refs_json"]) == []
    assert json.loads(origin["interpretation_json"]) == {
        "categories": ["unknown"],
        "evaluation_status": "not_evaluable",
        "owner_view": (
            "模擬引擎尚未啟用；目前只證明初始帳戶、鎖定baseline及建立證據完整，"
            "未能判斷真實模擬盤偏離。"
        ),
        "reason": "engine_not_enabled",
        "supporting_evidence_refs": [],
    }
    assert tuple(equity) == (
        "paper-ledger-" + ("3" * 32),
        1,
        trader["created_at"],
        "USD",
        123_456.75,
        123_456.75,
        0.0,
        0.0,
    )
    export = environment.catalog.get_export_artifacts(_RUN_ID)
    assert [row["ordinal"] for row in members] == [1, 2, 3, 4]
    assert [row["zip_path"] for row in members] == [
        "baseline/result.json",
        f"baseline/trades/{_RUN_ID}.json",
        f"baseline/equity/{_RUN_ID}.json",
        f"baseline/events/{_RUN_ID}.json",
    ]
    assert [bytes(row["payload"]) for row in members] == [
        payload for _path, payload in export.members
    ]
    assert all(
        row["byte_count"] == row["blob_byte_count"] == len(row["payload"])
        and row["sha256"] == sha256(bytes(row["payload"])).hexdigest()
        for row in members
    )


def test_create_revalidates_baseline_before_authorized_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment(tmp_path)
    original = ResultsCatalog.get_export_artifacts
    calls = 0

    def counted(
        catalog: ResultsCatalog,
        run_id: str,
    ) -> ResultExportArtifacts:
        nonlocal calls
        calls += 1
        return original(catalog, run_id)

    monkeypatch.setattr(ResultsCatalog, "get_export_artifacts", counted)
    body = _create_body(environment)
    with _client(environment) as client:
        first = client.post("/api/v1/paper/traders", json=body)
        replay = client.post("/api/v1/paper/traders", json=body)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert calls == 2


def test_create_captures_selected_baseline_instead_of_latest_candidate(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    _write_result(
        environment.root,
        run_id="zz-latest-candidate",
        range_end="2026-07-25T00:00:00Z",
        initial_capital=200_000.0,
    )
    selected_bytes = {
        relative: (environment.root / relative).read_bytes()
        for relative in (
            f"{_RUN_ID}.json",
            f"trades/{_RUN_ID}.json",
            f"equity/{_RUN_ID}.json",
            f"events/{_RUN_ID}.json",
        )
    }

    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )

    assert response.status_code == 201
    with sqlite3.connect(environment.traders.path) as connection:
        run_id = connection.execute(
            "SELECT baseline_run_id FROM paper_ledger_origins"
        ).fetchone()[0]
        payloads = [
            row[0]
            for row in connection.execute(
                """
                SELECT blob.payload
                FROM paper_ledger_baseline_members AS member
                JOIN paper_evidence_blobs AS blob ON blob.sha256 = member.sha256
                ORDER BY member.ordinal
                """
            ).fetchall()
        ]
    assert run_id == _RUN_ID
    assert [bytes(payload) for payload in payloads] == list(selected_bytes.values())


def test_identical_baseline_blobs_dedupe_across_distinct_traders(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    trader_ids = iter(
        ["trader-" + ("4" * 32), "trader-" + ("5" * 32)]
    )
    account_ids = iter(
        ["paper-account-" + ("6" * 32), "paper-account-" + ("7" * 32)]
    )
    ledger_ids = iter(
        ["paper-ledger-" + ("8" * 32), "paper-ledger-" + ("9" * 32)]
    )
    store = PaperTraderStore(
        tmp_path / "dedupe" / "paper-traders.sqlite3",
        clock=lambda: _CHECKED_AT,
        trader_id_factory=lambda: next(trader_ids),
        account_id_factory=lambda: next(account_ids),
        ledger_origin_id_factory=lambda: next(ledger_ids),
    )
    for request_id in (
        "00000000-0000-4000-8000-000000000020",
        "00000000-0000-4000-8000-000000000021",
    ):
        record, created = create_paper_trader(
            body=PaperTraderCreateRequest.model_validate(
                _create_body(environment, request_id=request_id)
            ),
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=_one_off_provisioning_policy(environment),
            trader_store=store,
        )
        assert created is True
        assert record.request_id == request_id

    counts = _all_row_counts(store.path)
    assert counts["paper_traders"] == 2
    assert counts["paper_accounts"] == 2
    assert counts["paper_trader_events"] == 8
    assert counts["paper_ledger_origins"] == 2
    assert counts["paper_ledger_equity_points"] == 2
    assert counts["paper_ledger_baseline_members"] == 8
    assert counts["paper_evidence_blobs"] == 4


@pytest.mark.parametrize("version", [0, 1, 2, 99])
def test_non_v3_store_requires_upgrade_without_mutation(
    tmp_path: Path,
    version: int,
) -> None:
    path = tmp_path / f"legacy-v{version}.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_stage_a (value TEXT)")
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    with pytest.raises(
        PaperTraderStoreSchemaUpgradeRequiredError,
        match="store_schema_upgrade_required",
    ):
        PaperTraderStore(path).list()

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not Path(f"{path}-journal").exists()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_v2_write_connection_never_lazy_migrates_or_mutates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v2-write.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_stage_b (value TEXT)")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    with pytest.raises(
        PaperTraderStoreSchemaUpgradeRequiredError,
        match="store_schema_upgrade_required",
    ):
        PaperTraderStore(path)._connect_write()

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not any(
        Path(f"{path}{suffix}").exists()
        for suffix in ("-journal", "-wal", "-shm")
    )


def test_v2_version_label_on_v3_objects_is_never_accepted(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    with sqlite3.connect(environment.traders.path) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    before = (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    )

    with pytest.raises(
        PaperTraderStoreSchemaUpgradeRequiredError,
        match="store_schema_upgrade_required",
    ):
        environment.traders.list()

    assert (
        environment.traders.path.read_bytes(),
        environment.traders.path.stat().st_mtime_ns,
    ) == before


@pytest.mark.parametrize("version", [0, 1, 2, 99])
def test_existing_non_v3_create_never_lazy_upgrades_or_backfills(
    tmp_path: Path,
    version: int,
) -> None:
    environment = _environment(tmp_path)
    path = tmp_path / f"non-v3-create-{version}" / "paper-traders.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_stage_a (value TEXT)")
        connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    store = PaperTraderStore(path)

    with _client(environment, trader_store=store) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )

    _assert_error(response, status=503, code="store_unavailable")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not Path(f"{path}-journal").exists()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


@pytest.mark.parametrize(
    "failure_step",
    [
        "paper_trader",
        "paper_account",
        "paper_trader_events",
        "paper_evidence_blobs",
        "paper_ledger_origin",
        "paper_ledger_equity_point",
        "paper_ledger_baseline_members",
    ],
)
def test_every_create_stage_failure_rolls_back_all_rows(
    tmp_path: Path,
    failure_step: str,
) -> None:
    environment = _environment(tmp_path)

    def fail_at(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected failure at {step}")

    store = PaperTraderStore(
        tmp_path / "rollback" / "paper-traders.sqlite3",
        clock=lambda: _CHECKED_AT,
        trader_id_factory=lambda: "trader-" + ("4" * 32),
        account_id_factory=lambda: "paper-account-" + ("5" * 32),
        ledger_origin_id_factory=lambda: "paper-ledger-" + ("6" * 32),
        append_step_hook=fail_at,
    )
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    policy = _one_off_provisioning_policy(environment)

    with pytest.raises(RuntimeError, match=failure_step):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=store,
        )

    assert _all_row_counts(store.path) == {
        table: 0 for table in _V3_TABLES
    }
    assert policy.authorization_for(body.selection).state == "claimed"


def test_same_sha_with_different_existing_blob_bytes_fails_closed(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    connection = environment.traders._connect_write()
    connection.close()
    real_payload = environment.main_path.read_bytes()
    real_sha = sha256(real_payload).hexdigest()
    forged_payload = b"x" * len(real_payload)
    with sqlite3.connect(environment.traders.path) as connection:
        connection.execute(
            """
            INSERT INTO paper_evidence_blobs (sha256, byte_count, payload)
            VALUES (?, ?, ?)
            """,
            (real_sha, len(forged_payload), forged_payload),
        )
        connection.commit()
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))

    with pytest.raises(PaperTraderStoreIntegrityError, match="evidence blob"):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=_one_off_provisioning_policy(environment),
            trader_store=environment.traders,
        )

    counts = _all_row_counts(environment.traders.path)
    assert counts["paper_evidence_blobs"] == 1
    assert all(
        count == 0
        for table, count in counts.items()
        if table != "paper_evidence_blobs"
    )


def test_gets_do_not_create_or_mutate_store(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    missing_path = tmp_path / "missing" / "paper-traders.sqlite3"
    store = PaperTraderStore(missing_path)
    with _client(environment, trader_store=store) as client:
        listing = client.get("/api/v1/paper/traders")
        request = client.get(
            f"/api/v1/paper/trader-requests/{_REQUEST_ID}"
        )
        detail = client.get("/api/v1/paper/traders/trader-" + ("1" * 32))
    assert listing.status_code == 200
    assert listing.json() == {
        "schema": "paper_trader_list.v1",
        "count": 0,
        "traders": [],
    }
    _assert_error(request, status=404, code="request_not_found")
    _assert_error(detail, status=404, code="request_not_found")
    assert not missing_path.exists()

    with _client(environment) as client:
        created = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
        assert created.status_code == 201
        before_bytes = environment.traders.path.read_bytes()
        before_mtime = environment.traders.path.stat().st_mtime_ns
        client.get("/api/v1/paper/traders")
        client.get(
            f"/api/v1/paper/trader-requests/{_REQUEST_ID}"
        )
        client.get(f"/api/v1/paper/traders/{created.json()['trader_id']}")
    assert environment.traders.path.read_bytes() == before_bytes
    assert environment.traders.path.stat().st_mtime_ns == before_mtime


def test_same_request_concurrency_produces_one_atomic_record(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    policy = _one_off_provisioning_policy(environment)

    def create() -> tuple[str, bool]:
        record, created = create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=policy,
            trader_store=environment.traders,
        )
        return record.trader_id, created

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _item: create(), range(16)))
    assert {trader_id for trader_id, _created in results} == {
        "trader-" + ("1" * 32)
    }
    assert sum(created for _trader_id, created in results) == 1
    assert _row_counts(environment.traders.path) == (1, 1, 4)
    counts = _all_row_counts(environment.traders.path)
    assert counts["paper_ledger_origins"] == 1
    assert counts["paper_ledger_equity_points"] == 1
    assert counts["paper_ledger_baseline_members"] == 4
    assert counts["paper_evidence_blobs"] == 4


def test_same_request_different_intent_conflicts_without_rows(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    body = _create_body(environment)
    with _client(environment) as client:
        first = client.post("/api/v1/paper/traders", json=body)
        before_counts = _all_row_counts(environment.traders.path)
        changed = _create_body(
            environment,
            selection_updates={"contract_id": "YM-202609-CBOT"},
        )
        conflict = client.post("/api/v1/paper/traders", json=changed)
    assert first.status_code == 201
    _assert_error(
        conflict,
        status=409,
        code="provisioning_selection_mismatch",
    )
    assert _row_counts(environment.traders.path) == (1, 1, 4)
    assert _all_row_counts(environment.traders.path) == before_counts


def test_transaction_rolls_back_every_row_on_event_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment(tmp_path)

    def fail_events(
        _connection: sqlite3.Connection,
        _record: object,
        _origin: object,
    ) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(
        PaperTraderStore,
        "_insert_initial_events",
        staticmethod(fail_events),
    )
    body = PaperTraderCreateRequest.model_validate(_create_body(environment))
    with pytest.raises(RuntimeError, match="injected event failure"):
        create_paper_trader(
            body=body,
            catalog=environment.catalog,
            registry=environment.registry,
            eligibility_store=environment.decisions,
            readiness_provider=_provider(),
            authorization_policy=_one_off_provisioning_policy(environment),
            trader_store=environment.traders,
        )
    assert _row_counts(environment.traders.path) == (0, 0, 0)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE paper_traders SET contract_id = 'forged'",
        "DELETE FROM paper_traders",
        "UPDATE paper_accounts SET currency = 'EUR'",
        "DELETE FROM paper_accounts",
        "UPDATE paper_trader_events SET event_type = 'provisioned'",
        "DELETE FROM paper_trader_events",
        "UPDATE paper_ledger_origins SET events_hwm = 3",
        "DELETE FROM paper_ledger_origins",
        "UPDATE paper_ledger_equity_points SET cash = 1",
        "DELETE FROM paper_ledger_equity_points",
        "UPDATE paper_evidence_blobs SET byte_count = 1",
        "DELETE FROM paper_evidence_blobs",
        "UPDATE paper_ledger_baseline_members SET ordinal = 4",
        "DELETE FROM paper_ledger_baseline_members",
    ],
)
def test_database_guards_reject_updates_and_deletes(
    tmp_path: Path,
    statement: str,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    with (
        sqlite3.connect(environment.traders.path) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(statement)
    assert _row_counts(environment.traders.path) == (1, 1, 4)


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP TRIGGER paper_traders_are_immutable",
        "DROP TRIGGER paper_ledger_origins_are_immutable",
        "DROP INDEX paper_traders_by_created",
        "CREATE TABLE unexpected_stage_b_object (value TEXT)",
        "PRAGMA writable_schema = ON",
    ],
)
def test_store_integrity_rejects_missing_guard_index_or_schema(
    tmp_path: Path,
    mutation: str,
) -> None:
    environment = _environment(tmp_path)
    with _client(environment) as client:
        response = client.post(
            "/api/v1/paper/traders",
            json=_create_body(environment),
        )
    assert response.status_code == 201
    with sqlite3.connect(environment.traders.path) as connection:
        if mutation == "PRAGMA writable_schema = ON":
            connection.execute(mutation)
            connection.execute(
                """
                UPDATE sqlite_master
                SET sql = replace(sql, 'blind_minutes = 5', 'blind_minutes = 6')
                WHERE type = 'table' AND name = 'paper_traders'
                """
            )
            connection.execute("PRAGMA writable_schema = OFF")
        else:
            connection.execute(mutation)
        connection.commit()
    with pytest.raises(PaperTraderStoreIntegrityError):
        environment.traders.list()
    with _client(environment) as client:
        response = client.get("/api/v1/paper/traders")
    _assert_error(response, status=503, code="store_unavailable")
