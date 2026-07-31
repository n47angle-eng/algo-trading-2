"""P6 Stage B immutable ledger evidence and target-authority tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from futures_research.api.paper_ledger import (
    PaperLedgerEvidenceError,
    PaperLedgerOriginSeed,
    capture_paper_ledger_baseline,
    insert_paper_ledger_origin,
    select_structural_closest_rejection_refs,
)
from futures_research.api.paper_provisioning import (
    OneOffPaperProvisioningAuthorizationPolicy,
)
from futures_research.api.paper_traders import (
    IsolatedPaperExternalReadinessProvider,
    PaperTraderCreateRequest,
    PaperTraderStore,
    create_paper_trader,
)
from futures_research.api.promotion_decisions import (
    PromotionDecisionStore,
    source_from_result_snapshot,
)
from futures_research.api.results_catalog import ResultExportArtifacts, ResultsCatalog
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
_TARGET_WHOLE_FILE_SHA = (
    "86a89a6f5cde85f5e54e95a29951f5a4947e51784b6d37f67b916325967bc36c"
)
_TARGET_CREATED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def _rejection(
    evidence_id: str,
    *,
    reached_layers: list[str],
    blocker_count: int,
    evaluation_sequence: int,
    timestamp: str,
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "reached_layers": reached_layers,
        "blocking_condition_ids": [
            f"blocker_{index}" for index in range(blocker_count)
        ],
        "evaluation_sequence": evaluation_sequence,
        "timestamp": timestamp,
    }


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            _rejection(
                "execution",
                reached_layers=["execution"],
                blocker_count=9,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            _rejection(
                "entry",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=99,
                timestamp="2026-07-29T00:00:01Z",
            ),
            "execution",
        ),
        (
            _rejection(
                "more-layers",
                reached_layers=["entry", "daily"],
                blocker_count=2,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            _rejection(
                "fewer-layers",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=99,
                timestamp="2026-07-29T00:00:01Z",
            ),
            "more-layers",
        ),
        (
            _rejection(
                "fewer-blockers",
                reached_layers=["entry", "daily"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            _rejection(
                "more-blockers",
                reached_layers=["entry", "daily"],
                blocker_count=2,
                evaluation_sequence=99,
                timestamp="2026-07-29T00:00:01Z",
            ),
            "fewer-blockers",
        ),
        (
            _rejection(
                "higher-sequence",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=2,
                timestamp="2026-07-29T00:00:00Z",
            ),
            _rejection(
                "lower-sequence",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:01Z",
            ),
            "higher-sequence",
        ),
        (
            _rejection(
                "later-time",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:01Z",
            ),
            _rejection(
                "earlier-time",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            "later-time",
        ),
        (
            _rejection(
                "rejection_b",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            _rejection(
                "rejection_a",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp="2026-07-29T00:00:00Z",
            ),
            "rejection_b",
        ),
    ],
)
def test_structural_closest_uses_each_frozen_tie_breaker(
    left: dict[str, object],
    right: dict[str, object],
    expected: str,
) -> None:
    selected = select_structural_closest_rejection_refs([right, left])

    assert selected[0] == expected
    assert set(selected) == {
        str(left["evidence_id"]),
        str(right["evidence_id"]),
    }


def test_structural_closest_rejects_unknown_layer() -> None:
    with pytest.raises(PaperLedgerEvidenceError, match="layer"):
        select_structural_closest_rejection_refs(
            [
                _rejection(
                    "unknown-layer",
                    reached_layers=["weekly"],
                    blocker_count=1,
                    evaluation_sequence=1,
                    timestamp="2026-07-29T00:00:00Z",
                )
            ]
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00Z",
        "0099-12-31T23:59:59Z",
        "0100-01-01T00:00:00.000001Z",
        "9999-12-31T23:59:59.100000Z",
    ],
)
def test_structural_closest_accepts_only_canonical_producer_utc_set(
    timestamp: str,
) -> None:
    selected = select_structural_closest_rejection_refs(
        [
            _rejection(
                "canonical-time",
                reached_layers=["entry"],
                blocker_count=1,
                evaluation_sequence=1,
                timestamp=timestamp,
            )
        ]
    )

    assert selected == ("canonical-time",)


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
def test_structural_closest_rejects_noncanonical_utc_text(
    timestamp: str,
) -> None:
    with pytest.raises(PaperLedgerEvidenceError, match="timestamp"):
        select_structural_closest_rejection_refs(
            [
                _rejection(
                    "noncanonical-time",
                    reached_layers=["entry"],
                    blocker_count=1,
                    evaluation_sequence=1,
                    timestamp=timestamp,
                )
            ]
        )


def _synthetic_artifacts(
    run_id: str = "ledger-capture-001",
) -> ResultExportArtifacts:
    events = json.dumps(
        {
            "schema": "events.v1",
            "run_id": run_id,
            "events": [],
            "rejection_evidence": [],
            "evidence_summary": {"rejection_count": 0},
            "evidence_complete": True,
        },
        separators=(",", ":"),
    ).encode()
    return ResultExportArtifacts(
        run_id=run_id,
        members=(
            ("result.json", b'{"schema":"result.v1"}'),
            (f"trades/{run_id}.json", b'{"trades":[]}'),
            (f"equity/{run_id}.json", b'{"points":[]}'),
            (f"events/{run_id}.json", events),
        ),
    )


def test_capture_rejects_consistent_traversal_run_identity() -> None:
    run_id = "../escape"
    artifacts = _synthetic_artifacts(run_id)

    with pytest.raises(PaperLedgerEvidenceError, match="run"):
        capture_paper_ledger_baseline(
            artifacts=artifacts,
            expected_run_id=run_id,
            expected_result_sha256=sha256(artifacts.members[0][1]).hexdigest(),
        )


@pytest.mark.parametrize(
    "run_id",
    [
        "nq-20260728-standard-365adf",
        "a",
        "A_1.x-y",
    ],
)
def test_capture_accepts_safe_run_id_matrix(run_id: str) -> None:
    artifacts = _synthetic_artifacts(run_id)

    capture = capture_paper_ledger_baseline(
        artifacts=artifacts,
        expected_run_id=run_id,
        expected_result_sha256=sha256(artifacts.members[0][1]).hexdigest(),
    )

    assert capture.run_id == run_id


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
def test_capture_rejects_unsafe_run_id_matrix(run_id: str) -> None:
    artifacts = _synthetic_artifacts(run_id)

    with pytest.raises(PaperLedgerEvidenceError, match="run"):
        capture_paper_ledger_baseline(
            artifacts=artifacts,
            expected_run_id=run_id,
            expected_result_sha256=sha256(artifacts.members[0][1]).hexdigest(),
        )


def test_unsafe_origin_seed_is_rejected_before_any_insert() -> None:
    artifacts = _synthetic_artifacts()
    capture = capture_paper_ledger_baseline(
        artifacts=artifacts,
        expected_run_id=artifacts.run_id,
        expected_result_sha256=sha256(artifacts.members[0][1]).hexdigest(),
    )
    unsafe_run_id = "../escape"
    seed = PaperLedgerOriginSeed(
        ledger_origin_id="paper-ledger-" + ("1" * 32),
        trader_id="trader-" + ("2" * 32),
        account_id="paper-account-" + ("3" * 32),
        origin_at="2026-07-29T00:00:00Z",
        strategy_id="strategy-safe-run-test",
        strategy_name="Safe run test",
        strategy_content_sha256="4" * 64,
        contract_id="NQ-202609-CME",
        contract_exchange="CME",
        contract_timezone="America/Chicago",
        baseline_run_id=unsafe_run_id,
        baseline_result_sha256=capture.result_sha256,
        baseline_range_start="2026-07-01T00:00:00Z",
        baseline_range_end="2026-07-29T00:00:00Z",
        currency="USD",
        initial_capital=100_000.0,
        baseline_capture=replace(capture, run_id=unsafe_run_id),
    )
    statements: list[str] = []
    connection = sqlite3.connect(":memory:")
    connection.set_trace_callback(statements.append)
    try:
        with pytest.raises(PaperLedgerEvidenceError, match="run"):
            insert_paper_ledger_origin(connection, seed=seed)
    finally:
        connection.close()

    assert not any(
        statement.lstrip().upper().startswith("INSERT")
        for statement in statements
    )


@pytest.mark.parametrize(
    "corruption",
    ["member_order", "unsafe_path", "result_sha", "events_json"],
)
def test_capture_rejects_order_path_sha_and_json_drift(corruption: str) -> None:
    artifacts = _synthetic_artifacts()
    members = list(artifacts.members)
    expected_sha = sha256(members[0][1]).hexdigest()
    if corruption == "member_order":
        members[1], members[2] = members[2], members[1]
    elif corruption == "unsafe_path":
        members[1] = ("../outside.json", members[1][1])
    elif corruption == "result_sha":
        expected_sha = "0" * 64
    else:
        members[3] = (members[3][0], b"{")
    corrupted = ResultExportArtifacts(run_id=artifacts.run_id, members=tuple(members))

    with pytest.raises(PaperLedgerEvidenceError):
        capture_paper_ledger_baseline(
            artifacts=corrupted,
            expected_run_id=artifacts.run_id,
            expected_result_sha256=expected_sha,
        )


def test_target_capture_has_exact_raw_members_closest_and_interpretation() -> None:
    catalog = ResultsCatalog(_RESULTS_ROOT)
    artifacts = catalog.get_export_artifacts(_TARGET_RUN_ID)

    capture = capture_paper_ledger_baseline(
        artifacts=artifacts,
        expected_run_id=_TARGET_RUN_ID,
        expected_result_sha256=_TARGET_RESULT_SHA,
    )

    assert capture.rejection_count == 14
    assert capture.closest_algorithm == "p5_structural_closest.v1"
    assert capture.closest_rejection_refs == (
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
    )
    assert [
        (member.path, member.byte_count, member.sha256)
        for member in capture.members
    ] == [
        (
            "baseline/result.json",
            8776,
            "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7",
        ),
        (
            f"baseline/trades/{_TARGET_RUN_ID}.json",
            109,
            "9f3c4e1cf03d305abd42deadf4f0e940754d8d99cf4e9a52cf74057ed03c6b69",
        ),
        (
            f"baseline/equity/{_TARGET_RUN_ID}.json",
            81,
            "4b38517f2b73ed6b9a986096162d5a5cb030117db69c14e84a6309edfe898346",
        ),
        (
            f"baseline/events/{_TARGET_RUN_ID}.json",
            35136,
            "094ebfbc9e010311292ae4005ce58de187a0aae471c32dc18ea53868629c1d30",
        ),
    ]
    for captured_member, source_member in zip(
        capture.members,
        artifacts.members,
        strict=True,
    ):
        assert captured_member.payload == source_member[1]
        assert captured_member.sha256 == sha256(source_member[1]).hexdigest()

    interpretation = json.loads(capture.interpretation_json)
    assert interpretation == {
        "evaluation_status": "not_evaluable",
        "reason": "engine_not_enabled",
        "owner_view": (
            "模擬引擎尚未啟用；目前只證明初始帳戶、鎖定baseline及建立證據完整，"
            "未能判斷真實模擬盤偏離。"
        ),
        "categories": ["unknown"],
        "supporting_evidence_refs": [
            {
                "path": f"baseline/events/{_TARGET_RUN_ID}.json",
                "evidence_id": "rejection_000014",
            },
            {
                "path": f"baseline/events/{_TARGET_RUN_ID}.json",
                "evidence_id": "rejection_000013",
            },
            {
                "path": f"baseline/events/{_TARGET_RUN_ID}.json",
                "evidence_id": "rejection_000012",
            },
        ],
    }
    assert capture.interpretation_json == json.dumps(
        interpretation,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_real_target_create_persists_authoritative_identity_and_raw_evidence(
    tmp_path: Path,
) -> None:
    catalog = ResultsCatalog(_RESULTS_ROOT)
    snapshot = catalog.get_verified_snapshot(_TARGET_RUN_ID)
    decisions = PromotionDecisionStore(
        tmp_path / "promotion.sqlite3",
        clock=lambda: _TARGET_CREATED_AT,
        decision_id_factory=lambda: "promotion-" + ("9" * 32),
    )
    decisions.append(
        request_id="stage-b-target-eligibility",
        request_payload_sha256=sha256(b"stage-b-target-eligibility").hexdigest(),
        decision="use",
        reason="Stage B target authority test",
        source=source_from_result_snapshot(
            snapshot,
            expected_run_id=_TARGET_RUN_ID,
        ),
    )
    store = PaperTraderStore(
        tmp_path / "paper" / "paper-traders.sqlite3",
        clock=lambda: _TARGET_CREATED_AT,
        trader_id_factory=lambda: "trader-" + ("1" * 32),
        account_id_factory=lambda: "paper-account-" + ("2" * 32),
        ledger_origin_id_factory=lambda: "paper-ledger-" + ("3" * 32),
    )
    body = PaperTraderCreateRequest(
        schema="paper_trader_create_request.v1",
        request_id="00000000-0000-4000-8000-000000000010",
        selection={
            "strategy_id": "strategy-0003",
            "content_sha256": _TARGET_STRATEGY_SHA,
            "contract_id": "NQ-202609-CME",
            "baseline_run_id": _TARGET_RUN_ID,
            "baseline_result_sha256": _TARGET_RESULT_SHA,
        },
    )

    record, created = create_paper_trader(
        body=body,
        catalog=catalog,
        registry=ContractRegistry.from_yaml(
            _PROJECT_ROOT / "config" / "contracts.yaml"
        ),
        eligibility_store=decisions,
        readiness_provider=IsolatedPaperExternalReadinessProvider(
            checked_at=_TARGET_CREATED_AT
        ),
        authorization_policy=OneOffPaperProvisioningAuthorizationPolicy(
            operation_id="paper-provision-" + ("8" * 32),
            authorized_selection=body.selection,
            authorized_at=_TARGET_CREATED_AT,
            clock=lambda: _TARGET_CREATED_AT,
        ),
        trader_store=store,
    )

    assert created is True
    assert set(record.model_dump(by_alias=True)) == {
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
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        origin = connection.execute(
            "SELECT * FROM paper_ledger_origins"
        ).fetchone()
        assert origin is not None
        members = connection.execute(
            """
            SELECT member.ordinal, member.zip_path, member.byte_count, member.sha256,
                   blob.payload
            FROM paper_ledger_baseline_members AS member
            JOIN paper_evidence_blobs AS blob ON blob.sha256 = member.sha256
            ORDER BY member.ordinal
            """
        ).fetchall()
    assert origin["ledger_origin_id"] == "paper-ledger-" + ("3" * 32)
    assert origin["trader_id"] == record.trader_id
    assert origin["account_id"] == record.account.account_id
    assert origin["strategy_id"] == "strategy-0003"
    assert origin["strategy_name"] == (
        "Trend 回踩 18EMA · engineering activation smoke"
    )
    assert origin["strategy_content_sha256"] == _TARGET_STRATEGY_SHA
    assert origin["strategy_content_sha256"] != _TARGET_WHOLE_FILE_SHA
    assert origin["contract_id"] == "NQ-202609-CME"
    assert origin["contract_exchange"] == "CME"
    assert origin["contract_timezone"] == "America/Chicago"
    assert origin["currency"] == "USD"
    assert origin["initial_capital"] == 100000.0
    assert origin["cash"] == origin["equity"] == 100000.0
    assert origin["realized_pnl"] == origin["unrealized_pnl"] == 0
    assert (
        origin["trades_hwm"],
        origin["equity_hwm"],
        origin["events_hwm"],
        origin["expected_decisions_hwm"],
        origin["positions_hwm"],
        origin["orders_hwm"],
    ) == (0, 1, 4, 0, 0, 0)
    assert origin["baseline_rejection_count"] == 14
    assert json.loads(origin["closest_rejection_refs_json"]) == [
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
    ]
    artifacts = catalog.get_export_artifacts(_TARGET_RUN_ID)
    assert [bytes(row["payload"]) for row in members] == [
        payload for _path, payload in artifacts.members
    ]
