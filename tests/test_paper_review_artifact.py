"""P6 Stage B deterministic review artifact component tests."""

# ruff: noqa: E501

from __future__ import annotations

import inspect
import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event
from zipfile import ZIP_STORED, ZipFile

import pytest

import futures_research.api.paper_review_artifact as artifact_module
from futures_research.api.paper_provisioning import (
    OneOffPaperProvisioningAuthorizationPolicy,
)
from futures_research.api.paper_review import (
    PaperReviewBuilderRegistry,
    PaperReviewCreateRequest,
    PaperReviewDomainError,
    PaperReviewService,
)
from futures_research.api.paper_review_artifact import (
    PaperReviewArtifactBuilder,
    _validate_member_path,
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
_RUN_ID = "nq-20260728-standard-365adf"
_RESULT_SHA = "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7"
_STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
_TRADER_CREATED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
_CAPTURED_AT = datetime(2026, 7, 29, 1, 2, 3, 4, tzinfo=UTC)
_READY_AT = datetime(2026, 7, 29, 2, 3, 4, 5, tzinfo=UTC)
_REQUEST_ID = "00000000-0000-4000-8000-000000000101"
_SNAPSHOT_ID = "paper-review-00000000000040008000000000000301"
_BUILDER_ID = "00000000-0000-4000-8000-000000000201"
_TRADER_ID = "trader-" + ("0" * 31) + "1"
_ACCOUNT_ID = "paper-account-" + ("0" * 30) + "65"
_LEDGER_ID = "paper-ledger-" + ("0" * 30) + "c9"
_MEMBER_PATHS = (
    "paper-review.json",
    "paper/ledger-origin.json",
    "paper/trades.json",
    "paper/equity.json",
    "paper/events.json",
    "divergence/expected-actual.json",
    "baseline/result.json",
    f"baseline/trades/{_RUN_ID}.json",
    f"baseline/equity/{_RUN_ID}.json",
    f"baseline/events/{_RUN_ID}.json",
)
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
    trader: PaperTrader
    artifact_root: Path


def _ids(prefix: str, start: int) -> Iterator[str]:
    for value in range(start, start + 100):
        yield f"{prefix}{value:032x}"


def _make_context(root: Path, *, artifact_root: Path | None = None) -> _Context:
    catalog = ResultsCatalog(_RESULTS_ROOT)
    decisions = PromotionDecisionStore(
        root / "promotion.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        decision_id_factory=lambda: "promotion-" + ("9" * 32),
    )
    snapshot = catalog.get_verified_snapshot(_RUN_ID)
    decisions.append(
        request_id="phase-three-eligibility",
        request_payload_sha256=sha256(b"phase-three-eligibility").hexdigest(),
        decision="use",
        reason="P6 Stage B Phase 3 temp authority",
        source=source_from_result_snapshot(
            snapshot,
            expected_run_id=_RUN_ID,
        ),
    )
    trader_ids = _ids("trader-", 1)
    account_ids = _ids("paper-account-", 101)
    ledger_ids = _ids("paper-ledger-", 201)
    store = PaperTraderStore(
        root / "paper" / "paper-traders.sqlite3",
        clock=lambda: _TRADER_CREATED_AT,
        trader_id_factory=lambda: next(trader_ids),
        account_id_factory=lambda: next(account_ids),
        ledger_origin_id_factory=lambda: next(ledger_ids),
    )
    body = PaperTraderCreateRequest(
        schema="paper_trader_create_request.v1",
        request_id="00000000-0000-4000-8000-000000000001",
        selection={
            "strategy_id": "strategy-0003",
            "content_sha256": _STRATEGY_SHA,
            "contract_id": "NQ-202609-CME",
            "baseline_run_id": _RUN_ID,
            "baseline_result_sha256": _RESULT_SHA,
        },
    )
    trader, created = create_paper_trader(
        body=body,
        catalog=catalog,
        registry=ContractRegistry.from_yaml(
            _PROJECT_ROOT / "config" / "contracts.yaml"
        ),
        eligibility_store=decisions,
        readiness_provider=IsolatedPaperExternalReadinessProvider(
            checked_at=_TRADER_CREATED_AT
        ),
        authorization_policy=OneOffPaperProvisioningAuthorizationPolicy(
            operation_id="paper-provision-" + ("8" * 32),
            authorized_selection=body.selection,
            authorized_at=_TRADER_CREATED_AT,
            clock=lambda: _TRADER_CREATED_AT,
        ),
        trader_store=store,
    )
    assert created is True
    registry = PaperReviewBuilderRegistry()
    service = PaperReviewService(
        store,
        clock=lambda: _CAPTURED_AT,
        snapshot_id_factory=lambda: _SNAPSHOT_ID,
        builder_instance_id_factory=lambda: _BUILDER_ID,
        builder_registry=registry,
    )
    accepted, accepted_created = service.accept_review_snapshot(
        trader_id=trader.trader_id,
        body=PaperReviewCreateRequest(
            schema="paper_review_create_request.v1",
            request_id=_REQUEST_ID,
        ),
    )
    assert accepted_created is True
    assert accepted.status == "preparing"
    return _Context(
        store=store,
        service=service,
        trader=trader,
        artifact_root=artifact_root or (root / "artifacts"),
    )


@pytest.fixture
def context(tmp_path: Path) -> _Context:
    return _make_context(tmp_path / "one")


def _review_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(
                f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed names
            ).fetchone()[0]
            for table in _REVIEW_TABLES
        }


def _db_facts(path: Path) -> tuple[bytes, int, dict[str, int], tuple[bool, ...]]:
    return (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        _review_counts(path),
        tuple(
            Path(f"{path}{suffix}").exists()
            for suffix in ("-journal", "-wal", "-shm")
        ),
    )


def _mutate_creation_event(
    context: _Context,
    corruption: str,
) -> None:
    with sqlite3.connect(context.store.path) as connection:
        trigger = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'paper_trader_events_are_append_only'
            """
        ).fetchone()
        assert trigger is not None and trigger[0] is not None
        connection.execute(
            "DROP TRIGGER paper_trader_events_are_append_only"
        )
        if corruption == "event_type":
            connection.execute(
                """
                UPDATE paper_trader_events
                SET event_type = 'provisioned'
                WHERE ordinal = 3
                """
            )
        elif corruption == "occurred_at":
            connection.execute(
                """
                UPDATE paper_trader_events
                SET occurred_at = '2026-07-29T00:00:01Z'
                WHERE ordinal = 3
                """
            )
        else:
            row = connection.execute(
                """
                SELECT payload_json
                FROM paper_trader_events
                WHERE ordinal = 3
                """
            ).fetchone()
            assert row is not None
            payload = json.loads(row[0])
            if corruption == "request_id":
                payload["request_id"] = (
                    "00000000-0000-4000-8000-000000000099"
                )
            elif corruption == "payload_sha":
                payload["request_payload_sha256"] = "b" * 64
            elif corruption == "selection":
                payload["selection"]["contract_id"] = "YM-202609-CBOT"
            elif corruption == "runtime":
                payload["runtime_readiness"]["checks"][0]["reason"] = (
                    "structurally valid forged runtime truth"
                )
            else:
                del payload["reason"]
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
        connection.execute(trigger[0])
        connection.commit()


def _artifact_relpath(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT artifact_relpath FROM paper_review_ready_artifacts"
        ).fetchone()
    assert row is not None
    return str(row[0])


def _artifact_path(context: _Context) -> Path:
    return context.artifact_root / _artifact_relpath(context.store.path)


def _zip_members(path: Path) -> dict[str, bytes]:
    with ZipFile(path, "r") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _canonical_generated(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _expected_opener(*, artifact_bytes: int, artifact_sha256: str) -> str:
    return f"""你會收到一個不可變 P6 review ZIP：paper-review-{_TRADER_ID}-20260729T010203000004Z.zip

身份（先同 paper-review.json 逐項核對）：
- schema：paper-review.v1
- snapshot_id：{_SNAPSHOT_ID}
- request_id：{_REQUEST_ID}
- trader_id：{_TRADER_ID}
- captured_at UTC：2026-07-29T01:02:03.000004Z
- strategy_version_id：strategy-0003
- strategy content_sha256：{_STRATEGY_SHA}
- baseline run_id：{_RUN_ID}
- baseline result_sha256：{_RESULT_SHA}
- ZIP bytes：{artifact_bytes}
- ZIP SHA-256：{artifact_sha256}

目前狀態：
- trader status = provisioned
- evaluation_status = not_evaluable
- evaluation_reason = engine_not_enabled
- 模擬引擎未啟用，實際 trades / positions / expected decisions 都是 0。
- baseline 有 14 筆 rejection evidence；它們不是 14 筆 expected trades，也不是 14 筆 missed trades。

ZIP 必須只有以下十個 ordered members：
1. paper-review.json
2. paper/ledger-origin.json
3. paper/trades.json
4. paper/equity.json
5. paper/events.json
6. divergence/expected-actual.json
7. baseline/result.json
8. baseline/trades/{_RUN_ID}.json
9. baseline/equity/{_RUN_ID}.json
10. baseline/events/{_RUN_ID}.json

請按以下次序處理：
1. 先驗 paper-review.json 以上身份，再逐項驗 members path、bytes、SHA-256及引用。
2. 讀 divergence/expected-actual.json；零交易 profile 必須保持 not_evaluable，不能寫成「沒有偏離」。
3. 如需追查，再讀 paper sidecars、baseline/result.json及它引用的三個 baseline sidecars。
4. 分開判斷 market、data、execution、strategy-understanding、unknown；每個結論附 ZIP 內 path 及 evidence id。
5. 明確列出「已證明」、「未能判斷」及「下一步需要甚麼證據」。

禁止修改或覆蓋舊 strategy、trader、baseline、ledger、snapshot或 ZIP。
如證據支持修改策略，只可輸出一個直接由 strategy-0003 衍生的新 strategy.v1，
保留 parent strategy ID及content SHA lineage；不要啟動交易、IB、Telegram或寫入舊記錄。
新策略必須回到策略工作台匯入、驗證、Owner確認，再經數據、回測、結果及建立新 trader。
"""


def test_golden_package_ready_pair_and_opener_are_exact(
    context: _Context,
) -> None:
    clock_calls = 0

    def completion_clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return _READY_AT

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=completion_clock,
    ).build(_REQUEST_ID)

    assert status.status == "ready"
    assert status.ready is not None
    assert status.error is None
    assert status.progress.model_dump() == {
        "completed_parts": 10,
        "total_parts": 10,
        "current_part": None,
    }
    assert clock_calls == 1
    ready = status.ready
    assert ready.display_filename == (
        f"paper-review-{_TRADER_ID}-20260729T010203000004Z.zip"
    )
    assert ready.ready_at == "2026-07-29T02:03:04.000005Z"
    assert tuple(member.path for member in ready.members) == _MEMBER_PATHS

    artifact = _artifact_path(context)
    assert artifact.is_file()
    assert artifact.stat().st_size == ready.artifact_bytes
    assert sha256(artifact.read_bytes()).hexdigest() == ready.artifact_sha256
    assert _artifact_relpath(context.store.path) == (
        f"sha256/{ready.artifact_sha256[:2]}/{ready.artifact_sha256}.zip"
    )

    with ZipFile(artifact, "r") as archive:
        assert archive.namelist() == list(_MEMBER_PATHS)
        assert archive.comment == b""
        for info in archive.infolist():
            assert info.compress_type == ZIP_STORED
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.extra == b""
            assert info.comment == b""
            assert (info.external_attr >> 16) & 0o777 == 0o600

    members = _zip_members(artifact)
    for path in _MEMBER_PATHS[:6]:
        parsed = json.loads(members[path])
        assert members[path] == _canonical_generated(parsed)
    main = json.loads(members["paper-review.json"])
    assert set(main) == {
        "schema",
        "snapshot",
        "trader",
        "strategy",
        "contract",
        "baseline",
        "account_origin",
        "as_of_state",
        "summary",
        "interpretation",
        "refs",
        "members",
    }
    assert set(main["snapshot"]) == {
        "snapshot_id",
        "request_id",
        "captured_at",
        "high_water_marks",
    }
    assert set(main["snapshot"]["high_water_marks"]) == {
        "trades",
        "equity",
        "events",
        "expected_decisions",
    }
    assert set(main["trader"]) == {
        "trader_id",
        "status",
        "created_at",
        "last_status_change_at",
    }
    assert set(main["strategy"]) == {
        "strategy_version_id",
        "name",
        "content_sha256",
    }
    assert set(main["contract"]) == {
        "contract_id",
        "exchange",
        "timezone",
    }
    assert set(main["baseline"]) == {
        "run_id",
        "result_sha256",
        "result_ref",
    }
    assert set(main["account_origin"]) == {
        "account_id",
        "currency",
        "initial_capital",
        "independent_account",
    }
    assert set(main["as_of_state"]) == {
        "cash",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "open_positions",
        "safety_status",
    }
    assert set(main["as_of_state"]["safety_status"]) == {
        "state",
        "drawdown_r",
        "loss_streak",
    }
    assert set(main["summary"]) == {
        "evaluation_status",
        "evaluation_reason",
        "expected_trades",
        "actual_trades",
        "matched",
        "missed",
        "extra",
        "average_slippage_points",
        "expectancy_delta_r",
    }
    assert set(main["interpretation"]) == {
        "owner_view",
        "categories",
        "supporting_event_refs",
    }
    assert main["schema"] == "paper-review.v1"
    assert main["snapshot"] == {
        "snapshot_id": _SNAPSHOT_ID,
        "request_id": _REQUEST_ID,
        "captured_at": "2026-07-29T01:02:03.000004Z",
        "high_water_marks": {
            "trades": 0,
            "equity": 1,
            "events": 4,
            "expected_decisions": 0,
        },
    }
    assert main["strategy"]["strategy_version_id"] == "strategy-0003"
    assert main["summary"] == {
        "evaluation_status": "not_evaluable",
        "evaluation_reason": "engine_not_enabled",
        "expected_trades": 0,
        "actual_trades": 0,
        "matched": 0,
        "missed": 0,
        "extra": 0,
        "average_slippage_points": None,
        "expectancy_delta_r": None,
    }
    assert main["members"] == [
        {
            "path": path,
            "bytes": len(members[path]),
            "sha256": sha256(members[path]).hexdigest(),
        }
        for path in _MEMBER_PATHS[1:]
    ]
    expected_refs = {
        "ledger_origin": "paper/ledger-origin.json",
        "paper_trades": "paper/trades.json",
        "paper_equity": "paper/equity.json",
        "paper_events": "paper/events.json",
        "expected_actual": "divergence/expected-actual.json",
    }
    assert main["refs"] == expected_refs
    assert json.loads(members["paper/ledger-origin.json"]) == (
        context.service.ledger_origin(context.trader.trader_id).model_dump(
            by_alias=True,
            mode="json",
        )
    )
    assert json.loads(members["paper/trades.json"]) == {
        "schema": "paper_trades.v1",
        "trader_id": _TRADER_ID,
        "captured_at": "2026-07-29T01:02:03.000004Z",
        "high_water_mark": 0,
        "count": 0,
        "trades": [],
    }
    assert json.loads(members["paper/equity.json"]) == {
        "schema": "paper_equity.v1",
        "trader_id": _TRADER_ID,
        "captured_at": "2026-07-29T01:02:03.000004Z",
        "high_water_mark": 1,
        "count": 1,
        "points": [
            {
                "ordinal": 1,
                "occurred_at": "2026-07-29T00:00:00Z",
                "currency": "USD",
                "cash": 100000.0,
                "equity": 100000.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            }
        ],
    }
    creation_events = json.loads(members["paper/events.json"])
    assert set(creation_events) == {
        "schema",
        "trader_id",
        "captured_at",
        "high_water_mark",
        "count",
        "events",
    }
    assert [
        (event["ordinal"], event["event_type"], event["occurred_at"])
        for event in creation_events["events"]
    ] == [
        (1, "trader_created", "2026-07-29T00:00:00Z"),
        (2, "account_created", "2026-07-29T00:00:00Z"),
        (3, "provisioning_authorized", "2026-07-29T00:00:00Z"),
        (4, "provisioned", "2026-07-29T00:00:00Z"),
    ]
    assert creation_events["schema"] == "paper_creation_events.v2"
    assert creation_events["high_water_mark"] == 4
    assert creation_events["count"] == 4
    assert creation_events["events"][2]["payload"]["schema"] == (
        "paper_provisioning_origin.v1"
    )
    assert creation_events["events"][2]["payload"]["runtime_readiness"] == (
        context.trader.readiness_snapshot.model_dump(
            by_alias=True,
            mode="json",
        )
    )
    assert all(
        set(event) == {"ordinal", "event_type", "occurred_at", "payload"}
        for event in creation_events["events"]
    )
    expected_actual = json.loads(members["divergence/expected-actual.json"])
    assert set(expected_actual) == {
        "schema",
        "snapshot_id",
        "trader_id",
        "evaluation_status",
        "evaluation_reason",
        "summary",
        "comparisons",
        "baseline_rejections",
    }
    assert expected_actual["summary"]["missed"] == 0
    assert expected_actual["baseline_rejections"] == {
        "count": 14,
        "closest_algorithm": "p5_structural_closest.v1",
        "closest_refs": [
            "rejection_000014",
            "rejection_000013",
            "rejection_000012",
        ],
        "source_ref": f"baseline/events/{_RUN_ID}.json",
    }

    with sqlite3.connect(context.store.path) as connection:
        connection.row_factory = sqlite3.Row
        raw_rows = connection.execute(
            """
            SELECT member.zip_path, blob.payload
            FROM paper_ledger_baseline_members AS member
            JOIN paper_evidence_blobs AS blob ON blob.sha256 = member.sha256
            ORDER BY member.ordinal
            """
        ).fetchall()
        ready_row = connection.execute(
            "SELECT * FROM paper_review_ready_artifacts"
        ).fetchone()
        event = connection.execute(
            "SELECT * FROM paper_review_status_events WHERE ordinal = 2"
        ).fetchone()
    assert {
        row["zip_path"]: bytes(row["payload"]) for row in raw_rows
    } == {path: members[path] for path in _MEMBER_PATHS[6:]}
    assert ready_row is not None
    assert event is not None
    opener = _expected_opener(
        artifact_bytes=ready.artifact_bytes,
        artifact_sha256=ready.artifact_sha256,
    )
    assert ready_row["terminal_opener_text"] == opener
    assert ready_row["terminal_opener_bytes"] == len(opener.encode("utf-8"))
    assert ready_row["terminal_opener_sha256"] == sha256(
        opener.encode("utf-8")
    ).hexdigest()
    assert ready.terminal_opener.bytes == len(opener.encode("utf-8"))
    assert ready.terminal_opener.sha256 == sha256(
        opener.encode("utf-8")
    ).hexdigest()
    assert event["occurred_at"] == ready_row["ready_at"]
    assert event["payload_json"] == _canonical_generated(
        {
            "artifact_sha256": ready.artifact_sha256,
            "completed_parts": 10,
            "total_parts": 10,
        }
    ).decode("utf-8").removesuffix("\n")
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 1,
        "paper_review_failures": 0,
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "event_type",
        "occurred_at",
        "request_id",
        "payload_sha",
        "selection",
        "runtime",
        "missing_key",
    ],
)
def test_v3_event_or_origin_drift_fails_before_artifact_publish(
    context: _Context,
    corruption: str,
) -> None:
    _mutate_creation_event(context, corruption)

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "snapshot_integrity_failed"
    assert (
        list(context.artifact_root.rglob("*.zip"))
        if context.artifact_root.exists()
        else []
    ) == []
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }


def test_deterministic_across_stores_and_independent_of_live_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_context(tmp_path / "first")
    second = _make_context(tmp_path / "second")

    def live_source_must_not_be_read(
        _self: ResultsCatalog,
        _run_id: str,
    ) -> object:
        raise AssertionError("artifact builder reread live results")

    monkeypatch.setattr(
        ResultsCatalog,
        "get_export_artifacts",
        live_source_must_not_be_read,
    )
    original_open = Path.open

    def results_source_must_be_unavailable(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        if path.resolve().is_relative_to(_RESULTS_ROOT.resolve()):
            raise AssertionError("artifact builder opened live results source")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", results_source_must_be_unavailable)
    clock_calls = [0, 0]

    def clock(index: int) -> Callable[[], datetime]:
        def read() -> datetime:
            clock_calls[index] += 1
            return _READY_AT

        return read

    first_status = PaperReviewArtifactBuilder(
        first.service,
        artifact_root=first.artifact_root,
        completion_clock=clock(0),
    ).build(_REQUEST_ID)
    second_status = PaperReviewArtifactBuilder(
        second.service,
        artifact_root=second.artifact_root,
        completion_clock=clock(1),
    ).build(_REQUEST_ID)

    assert first_status.ready is not None
    assert second_status.ready is not None
    assert first_status.ready.artifact_sha256 == (
        second_status.ready.artifact_sha256
    )
    assert _artifact_path(first).read_bytes() == _artifact_path(second).read_bytes()
    assert _zip_members(_artifact_path(first)) == _zip_members(
        _artifact_path(second)
    )
    assert clock_calls == [1, 1]


def test_ready_replay_is_zero_build_clock_db_and_artifact_write(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)
    before_db = _db_facts(context.store.path)
    before_artifact = (
        _artifact_path(context).read_bytes(),
        _artifact_path(context).stat().st_mtime_ns,
    )
    monkeypatch.setattr(
        PaperReviewArtifactBuilder,
        "_terminal_opener",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ready replay regenerated terminal opener")
        ),
    )

    replay = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: (_ for _ in ()).throw(
            AssertionError("ready replay read completion clock")
        ),
        step_hook=lambda step: (_ for _ in ()).throw(
            AssertionError(f"ready replay ran build step {step}")
        ),
    ).build(_REQUEST_ID)

    assert replay == first
    assert _db_facts(context.store.path) == before_db
    assert (
        _artifact_path(context).read_bytes(),
        _artifact_path(context).stat().st_mtime_ns,
    ) == before_artifact


def test_single_builder_claim_prevents_second_artifact_build(
    context: _Context,
) -> None:
    reached = Event()
    release = Event()

    def hook(step: str) -> None:
        if step == "temp_write":
            reached.set()
            if not release.wait(timeout=5):
                raise AssertionError("single-builder barrier was not released")

    first_builder = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
        step_hook=hook,
    )
    second_builder = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: (_ for _ in ()).throw(
            AssertionError("second builder read completion clock")
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_builder.build, _REQUEST_ID)
        assert reached.wait(timeout=5)
        second = second_builder.build(_REQUEST_ID)
        assert second.status == "preparing"
        release.set()
        first = first_future.result(timeout=5)

    assert first.status == "ready"
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 1,
        "paper_review_failures": 0,
    }
    assert len(list(context.artifact_root.rglob("*.zip"))) == 1


def test_corrupt_temp_zip_is_rejected_before_atomic_publish(
    context: _Context,
) -> None:
    def corrupt(step: str) -> None:
        if step != "temp_write":
            return
        staging_files = list(
            (context.artifact_root / ".staging").glob("*.zip")
        )
        assert len(staging_files) == 1
        with staging_files[0].open("r+b") as stream:
            stream.truncate(staging_files[0].stat().st_size // 2)

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
        step_hook=corrupt,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "artifact_build_failed"
    assert not (context.artifact_root / "sha256").exists()
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }


def test_corrupt_published_file_is_rejected_before_ready_pair(
    context: _Context,
) -> None:
    def corrupt(step: str) -> None:
        if step != "atomic_publish":
            return
        published = list((context.artifact_root / "sha256").rglob("*.zip"))
        assert len(published) == 1
        published[0].write_bytes(b"externally-corrupted")

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
        step_hook=corrupt,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "artifact_unavailable"
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }


@pytest.mark.parametrize(
    "failure_step",
    [
        "member_generation",
        "temp_write",
        "temp_verify",
        "whole_hash",
        "atomic_publish",
        "final_verify",
        "ready_row",
        "ready_event",
        "commit",
    ],
)
def test_crash_matrix_has_no_partial_ready_and_exact_orphan_boundary(
    context: _Context,
    failure_step: str,
) -> None:
    def fail(step: str) -> None:
        if step == failure_step:
            raise RuntimeError(f"injected {step}")

    with pytest.raises(RuntimeError, match=failure_step):
        PaperReviewArtifactBuilder(
            context.service,
            artifact_root=context.artifact_root,
            completion_clock=lambda: _READY_AT,
            step_hook=fail,
        ).build(_REQUEST_ID)

    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 1,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 0,
    }
    status = context.service.review_request_status(_REQUEST_ID)
    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "build_interrupted"
    staging = context.artifact_root / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []
    cas_files = list((context.artifact_root / "sha256").rglob("*.zip")) if (
        context.artifact_root / "sha256"
    ).exists() else []
    should_have_orphan = failure_step in {
        "atomic_publish",
        "final_verify",
        "ready_row",
        "ready_event",
        "commit",
    }
    assert len(cas_files) == int(should_have_orphan)


def test_caught_source_hash_drift_persists_atomic_failure(
    context: _Context,
) -> None:
    with sqlite3.connect(context.store.path) as connection:
        trigger = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'paper_evidence_blobs_are_immutable'
            """
        ).fetchone()
        assert trigger is not None and trigger[0] is not None
        row = connection.execute(
            "SELECT sha256, payload FROM paper_evidence_blobs ORDER BY sha256 LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = bytes(row[1])
        corrupted = bytes([payload[0] ^ 1]) + payload[1:]
        connection.execute("DROP TRIGGER paper_evidence_blobs_are_immutable")
        connection.execute(
            "UPDATE paper_evidence_blobs SET payload = ? WHERE sha256 = ?",
            (corrupted, row[0]),
        )
        connection.execute(trigger[0])
        connection.commit()

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "snapshot_integrity_failed"
    assert status.error.retryable is False
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }
    with sqlite3.connect(context.store.path) as connection:
        event = connection.execute(
            "SELECT status, payload_json FROM paper_review_status_events WHERE ordinal=2"
        ).fetchone()
        failure = connection.execute(
            "SELECT http_status, error_json, error_sha256 FROM paper_review_failures"
        ).fetchone()
    assert event is not None and event[0] == "failed"
    assert failure is not None and failure[0] == 503
    assert sha256(failure[1].encode("utf-8")).hexdigest() == failure[2]
    assert not context.artifact_root.exists()
    before_replay = _db_facts(context.store.path)
    assert context.service.review_request_status(_REQUEST_ID) == status
    assert PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: (_ for _ in ()).throw(
            AssertionError("failed replay read completion clock")
        ),
    ).build(_REQUEST_ID) == status
    assert _db_facts(context.store.path) == before_replay
    assert not context.artifact_root.exists()


def test_existing_exact_cas_is_adopted_without_overwrite(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared-artifacts"
    first = _make_context(tmp_path / "first", artifact_root=shared)
    second = _make_context(tmp_path / "second", artifact_root=shared)
    first_status = PaperReviewArtifactBuilder(
        first.service,
        artifact_root=shared,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)
    assert first_status.ready is not None
    artifact = _artifact_path(first)
    before = (artifact.read_bytes(), artifact.stat().st_mtime_ns)

    second_status = PaperReviewArtifactBuilder(
        second.service,
        artifact_root=shared,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)

    assert second_status.ready is not None
    assert second_status.ready.artifact_sha256 == (
        first_status.ready.artifact_sha256
    )
    assert (artifact.read_bytes(), artifact.stat().st_mtime_ns) == before


def test_existing_cas_mismatch_is_not_overwritten_and_fails_terminal(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared-artifacts"
    first = _make_context(tmp_path / "first", artifact_root=shared)
    second = _make_context(tmp_path / "second", artifact_root=shared)
    first_status = PaperReviewArtifactBuilder(
        first.service,
        artifact_root=shared,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)
    assert first_status.ready is not None
    artifact = _artifact_path(first)
    corrupt = b"not-the-original-cas"
    artifact.write_bytes(corrupt)

    status = PaperReviewArtifactBuilder(
        second.service,
        artifact_root=shared,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "artifact_unavailable"
    assert artifact.read_bytes() == corrupt
    assert _review_counts(second.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }


def test_filesystem_publish_failure_persists_classified_terminal_pair(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_publish(_source: object, _target: object) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(artifact_module.os, "link", fail_publish)

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
    ).build(_REQUEST_ID)

    assert status.status == "failed"
    assert status.error is not None
    assert status.error.code == "artifact_build_failed"
    assert status.error.progress is not None
    assert status.error.progress.completed_parts == 9
    assert _review_counts(context.store.path) == {
        "paper_review_requests": 1,
        "paper_review_status_events": 2,
        "paper_review_ready_artifacts": 0,
        "paper_review_failures": 1,
    }
    assert list(context.artifact_root.rglob("*.zip")) == []
    assert list((context.artifact_root / ".staging").iterdir()) == []


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../escape",
        "/absolute",
        "C:drive/file",
        r"paper\events.json",
        "./paper/events.json",
        "paper/../events.json",
        "paper//events.json",
        ".hidden",
    ],
)
def test_unsafe_member_path_is_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        _validate_member_path(path)


def test_symlink_artifact_root_is_rejected_when_supported(
    context: _Context,
    tmp_path: Path,
) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="symlink|reparse"):
        PaperReviewArtifactBuilder(
            context.service,
            artifact_root=link,
            completion_clock=lambda: _READY_AT,
        ).build(_REQUEST_ID)


def test_symlink_staging_directory_is_rejected_when_supported(
    context: _Context,
    tmp_path: Path,
) -> None:
    context.artifact_root.mkdir()
    target = tmp_path / "real-staging"
    target.mkdir()
    link = context.artifact_root / ".staging"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this platform")

    with pytest.raises(ValueError, match="symlink|reparse"):
        PaperReviewArtifactBuilder(
            context.service,
            artifact_root=context.artifact_root,
            completion_clock=lambda: _READY_AT,
        ).build(_REQUEST_ID)


def test_import_construct_and_missing_status_do_not_create_artifact_root(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "never-created-artifacts"
    store_path = tmp_path / "never-created-db" / "paper.sqlite3"
    service = PaperReviewService(PaperTraderStore(store_path))
    builder = PaperReviewArtifactBuilder(
        service,
        artifact_root=artifact_root,
        completion_clock=lambda: _READY_AT,
    )

    with pytest.raises(PaperReviewDomainError) as raised:
        builder.build(_REQUEST_ID)

    assert raised.value.code == "request_not_found"
    assert not artifact_root.exists()
    assert not store_path.exists()
    assert not (_PROJECT_ROOT / "data" / "paper").exists()


def test_product_uses_chunked_zip_hashing_without_bytesio(
    context: _Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes

    def no_whole_zip_read(self: Path) -> bytes:
        if self.suffix == ".zip":
            raise AssertionError("product read whole ZIP into memory")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", no_whole_zip_read)

    status = PaperReviewArtifactBuilder(
        context.service,
        artifact_root=context.artifact_root,
        completion_clock=lambda: _READY_AT,
        hash_chunk_size=257,
    ).build(_REQUEST_ID)

    assert status.status == "ready"
    source = inspect.getsource(
        __import__(
            "futures_research.api.paper_review_artifact",
            fromlist=["PaperReviewArtifactBuilder"],
        )
    )
    assert "BytesIO" not in source
