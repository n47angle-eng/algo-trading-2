"""Deterministic immutable artifact builder for accepted P6 review requests."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pydantic import ValidationError

from futures_research.api.paper_review import (
    PaperEvidenceMember,
    PaperLedgerOrigin,
    PaperReviewDomainError,
    PaperReviewErrorCode,
    PaperReviewErrorPayload,
    PaperReviewIssue,
    PaperReviewProgress,
    PaperReviewReady,
    PaperReviewReadyResource,
    PaperReviewService,
    PaperReviewStatus,
    PaperTerminalOpenerClaim,
    _canonical_json,
    _canonical_utc,
    _decode_canonical_json,
    _RequestIdentity,
    _RequestRecord,
    _review_member_paths,
    _timestamp_text,
)
from futures_research.api.paper_traders import (
    PaperTraderStoreIntegrityError,
    PaperTraderStoreSchemaUpgradeRequiredError,
)

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_MODE = 0o600
_TOTAL_PARTS = 10
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class _FrozenSource:
    identity: _RequestIdentity
    request: _RequestRecord
    ledger: PaperLedgerOrigin
    equity_point: Mapping[str, object]
    creation_events: tuple[Mapping[str, object], ...]
    baseline_members: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class _Package:
    members: tuple[tuple[str, bytes], ...]
    manifest: tuple[PaperEvidenceMember, ...]
    display_filename: str


class _SnapshotIntegrityFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        issue: PaperReviewIssue | None = None,
    ) -> None:
        super().__init__(message)
        self.issue = issue or PaperReviewIssue(
            kind="identity_mismatch",
            path=None,
            source_ref=None,
            expected_sha256=None,
            actual_sha256=None,
            ref_chain=(),
        )


class _ArtifactBuildFailure(RuntimeError):
    pass


class _ArtifactUnavailable(RuntimeError):
    pass


class _HookFailure(RuntimeError):
    def __init__(self, original: Exception) -> None:
        super().__init__(str(original))
        self.original = original


@dataclass(frozen=True, slots=True)
class PaperReviewArtifactDownload:
    """Fully captured immutable bytes safe to place in one HTTP response."""

    content: bytes
    display_filename: str


class PaperReviewArtifactReader:
    """Read and verify one persisted ready artifact without streaming partial data."""

    def __init__(
        self,
        service: PaperReviewService,
        *,
        artifact_root: Path,
    ) -> None:
        if not isinstance(service, PaperReviewService):
            raise TypeError("service must be a PaperReviewService")
        root = Path(artifact_root)
        if not root.is_absolute():
            raise ValueError("artifact root must be an explicit absolute path")
        self._service = service
        self._artifact_root = root

    def read(self, snapshot_id: str) -> PaperReviewArtifactDownload:
        """Capture once, then verify byte count and SHA before returning."""
        resource = self._service.ready_snapshot(
            snapshot_id,
            defer_artifact_path_validation=True,
        )
        ready = resource.status.ready
        if ready is None:  # pragma: no cover - guarded by ready_snapshot
            raise AssertionError("ready snapshot lost its ready metadata")
        actual_sha: str | None = None
        try:
            relative_text = self._service._validate_artifact_relpath(
                resource.artifact_relpath,
                artifact_sha256=ready.artifact_sha256,
            )
            relative = PurePosixPath(relative_text)
            if relative.is_absolute() or any(
                part in {"", ".", ".."} for part in relative.parts
            ):
                raise ValueError("artifact relative path is unsafe")
            artifact_path = self._artifact_root.joinpath(*relative.parts)
            _assert_no_reparse_chain(self._artifact_root)
            _assert_regular_non_reparse_file(artifact_path)
            with artifact_path.open("rb") as stream:
                content = stream.read()
            actual_sha = sha256(content).hexdigest()
        except (OSError, TypeError, ValueError) as exc:
            raise self._unavailable_error(
                resource=resource,
                actual_sha256=actual_sha,
            ) from exc
        if (
            len(content) != ready.artifact_bytes
            or actual_sha != ready.artifact_sha256
        ):
            raise self._unavailable_error(
                resource=resource,
                actual_sha256=actual_sha,
            )
        return PaperReviewArtifactDownload(
            content=content,
            display_filename=ready.display_filename,
        )

    @staticmethod
    def _unavailable_error(
        *,
        resource: PaperReviewReadyResource,
        actual_sha256: str | None,
    ) -> PaperReviewDomainError:
        ready = resource.status.ready
        expected_sha = ready.artifact_sha256 if ready is not None else None
        return PaperReviewDomainError(
            "artifact_unavailable",
            "Persisted review artifact is unavailable or differs from its claim.",
            request_id=resource.status.request_id,
            snapshot_id=resource.status.snapshot_id,
            progress=resource.status.progress,
            issues=(
                PaperReviewIssue(
                    kind="artifact_unavailable",
                    path=None,
                    source_ref=None,
                    expected_sha256=expected_sha,
                    actual_sha256=actual_sha256,
                    ref_chain=(),
                ),
            ),
        )


class PaperReviewArtifactBuilder:
    """Build one exact ZIP from a request's immutable v2-store evidence."""

    def __init__(
        self,
        service: PaperReviewService,
        *,
        artifact_root: Path,
        completion_clock: Callable[[], datetime] | None = None,
        step_hook: Callable[[str], None] | None = None,
        hash_chunk_size: int = 1024 * 1024,
    ) -> None:
        if not isinstance(service, PaperReviewService):
            raise TypeError("service must be a PaperReviewService")
        root = Path(artifact_root)
        if not root.is_absolute():
            raise ValueError("artifact root must be an explicit absolute path")
        if (
            type(hash_chunk_size) is not int
            or hash_chunk_size <= 0
            or hash_chunk_size > _MAX_SAFE_INTEGER
        ):
            raise ValueError("hash chunk size must be a positive safe integer")
        self._service = service
        self._artifact_root = root
        self._completion_clock = completion_clock or (
            lambda: datetime.now(UTC)
        )
        self._step_hook = step_hook
        self._hash_chunk_size = hash_chunk_size

    def build(self, request_id: str) -> PaperReviewStatus:
        """Build once, replay terminal state, or expose effective interruption."""
        status, identity, claimed = self._service._artifact_build_entry(
            request_id
        )
        if not claimed:
            return status

        temp_path: Path | None = None
        completed_parts = 0
        try:
            try:
                source = self._load_frozen_source(identity)
                package = self._build_package(source)
            except _SnapshotIntegrityFailure as exc:
                return self._persist_failure(
                    identity=identity,
                    code="snapshot_integrity_failed",
                    message=str(exc),
                    completed_parts=completed_parts,
                    issues=(exc.issue,),
                )
            self._call_step("member_generation")

            self._prepare_artifact_root()
            temp_path = self._new_staging_path()
            self._write_temp_zip(
                temp_path,
                package=package,
                identity=identity,
            )
            completed_parts = _TOTAL_PARTS
            self._call_step("temp_write")
            self._verify_zip(temp_path, package=package, source=source)
            self._call_step("temp_verify")
            artifact_bytes, artifact_sha = self._hash_file(temp_path)
            self._call_step("whole_hash")

            artifact_relpath, final_path = self._publish(
                temp_path,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha,
            )
            temp_path = None
            self._call_step("atomic_publish")
            self._verify_final(
                final_path,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha,
            )
            self._call_step("final_verify")

            ready_at = _timestamp_text(self._completion_clock())
            _canonical_utc(ready_at)
            opener = self._terminal_opener(
                source=source,
                display_filename=package.display_filename,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha,
            )
            opener_bytes = opener.encode("utf-8")
            ready = PaperReviewReady(
                schema="paper_review_ready.v1",
                display_filename=package.display_filename,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha,
                member_count=10,
                members=package.manifest,
                terminal_opener=PaperTerminalOpenerClaim(
                    bytes=len(opener_bytes),
                    sha256=sha256(opener_bytes).hexdigest(),
                ),
                ready_at=ready_at,
            )
            try:
                self._service._save_ready_artifact(
                    request_id=identity.request_id,
                    artifact_relpath=artifact_relpath,
                    ready=ready,
                    terminal_opener_text=opener,
                    step_hook=self._call_step,
                )
            except _HookFailure:
                raise
            except PaperReviewDomainError as exc:
                if exc.code != "snapshot_integrity_failed":
                    raise
                return self._persist_failure(
                    identity=identity,
                    code="snapshot_integrity_failed",
                    message=str(exc),
                    completed_parts=completed_parts,
                    issues=(
                        PaperReviewIssue(
                            kind="identity_mismatch",
                            path=None,
                            source_ref=None,
                            expected_sha256=None,
                            actual_sha256=None,
                            ref_chain=(),
                        ),
                    ),
                )
            return self._service.review_request_status(identity.request_id)
        except _HookFailure as exc:
            raise exc.original from None
        except _ArtifactUnavailable as exc:
            return self._persist_failure(
                identity=identity,
                code="artifact_unavailable",
                message=str(exc),
                completed_parts=completed_parts,
                issues=(
                    PaperReviewIssue(
                        kind="artifact_unavailable",
                        path=None,
                        source_ref=None,
                        expected_sha256=None,
                        actual_sha256=None,
                        ref_chain=(),
                    ),
                ),
            )
        except (BadZipFile, OSError, _ArtifactBuildFailure) as exc:
            return self._persist_failure(
                identity=identity,
                code="artifact_build_failed",
                message=f"Review artifact build failed: {exc}",
                completed_parts=completed_parts,
                issues=(
                    PaperReviewIssue(
                        kind="artifact_build_failed",
                        path=None,
                        source_ref=None,
                        expected_sha256=None,
                        actual_sha256=None,
                        ref_chain=(),
                    ),
                ),
            )
        finally:
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink(missing_ok=True)
            self._service.builder_registry.deactivate(
                store_key=self._service._store_key,
                request_id=identity.request_id,
            )

    def _persist_failure(
        self,
        *,
        identity: _RequestIdentity,
        code: PaperReviewErrorCode,
        message: str,
        completed_parts: int,
        issues: Sequence[PaperReviewIssue],
    ) -> PaperReviewStatus:
        active_progress = self._service.builder_registry.progress(
            store_key=self._service._store_key,
            request_id=identity.request_id,
            builder_instance_id=identity.builder_instance_id,
        )
        last_verified = max(
            completed_parts,
            active_progress.completed_parts
            if active_progress is not None
            else 0,
        )
        terminal_completed = min(last_verified, _TOTAL_PARTS - 1)
        progress = PaperReviewProgress(
            completed_parts=terminal_completed,
            total_parts=10,
            current_part=None,
        )
        error = PaperReviewErrorPayload(
            schema="paper_review_error.v1",
            code=code,
            message=message,
            retryable=False,
            request_id=identity.request_id,
            snapshot_id=identity.snapshot_id,
            progress=progress,
            issues=tuple(issues),
        )
        failed_at = _timestamp_text(self._completion_clock())
        _canonical_utc(failed_at)
        self._service._save_artifact_failure(
            request_id=identity.request_id,
            error=error,
            failed_at=failed_at,
        )
        status, _, _ = self._service._artifact_build_entry(
            identity.request_id
        )
        return status

    def _call_step(self, step: str) -> None:
        if self._step_hook is None:
            return
        try:
            self._step_hook(step)
        except Exception as exc:
            raise _HookFailure(exc) from exc

    def _load_frozen_source(
        self,
        expected_identity: _RequestIdentity,
    ) -> _FrozenSource:
        store = self._service._store
        try:
            connection = store._connect_readonly()
        except (
            PaperTraderStoreIntegrityError,
            PaperTraderStoreSchemaUpgradeRequiredError,
        ) as exc:
            raise _SnapshotIntegrityFailure(
                "Frozen paper review store failed its schema guard."
            ) from exc
        try:
            connection.execute("BEGIN")
            request_row = connection.execute(
                """
                SELECT *
                FROM paper_review_requests
                WHERE request_id = ?
                """,
                (expected_identity.request_id,),
            ).fetchone()
            if request_row is None:
                raise _SnapshotIntegrityFailure(
                    "Accepted paper review request disappeared."
                )
            identity = self._service._request_identity(request_row)
            if identity != expected_identity:
                raise _SnapshotIntegrityFailure(
                    "Frozen paper review request identity changed."
                )

            trader_row = connection.execute(
                "SELECT * FROM paper_traders WHERE trader_id = ?",
                (identity.trader_id,),
            ).fetchone()
            if trader_row is None:
                raise _SnapshotIntegrityFailure(
                    "Frozen paper trader disappeared."
                )
            run_id = trader_row["baseline_run_id"]
            paths = _review_member_paths(run_id)
            baseline_paths = paths[6:]
            raw_rows = connection.execute(
                """
                SELECT member.ordinal, member.zip_path, member.byte_count,
                       member.sha256, blob.byte_count AS blob_byte_count,
                       blob.payload
                FROM paper_ledger_baseline_members AS member
                JOIN paper_evidence_blobs AS blob
                  ON blob.sha256 = member.sha256
                WHERE member.ledger_origin_id = ?
                ORDER BY member.ordinal
                """,
                (identity.ledger_origin_id,),
            ).fetchall()
            if len(raw_rows) != 4:
                raise _SnapshotIntegrityFailure(
                    "Frozen baseline does not contain exactly four members.",
                    issue=PaperReviewIssue(
                        kind="count_mismatch",
                        path=None,
                        source_ref="paper/ledger-origin.json#baseline.members",
                        expected_sha256=None,
                        actual_sha256=None,
                        ref_chain=("paper-review.json#baseline.result_ref",),
                    ),
                )
            baseline_members: list[tuple[str, bytes]] = []
            for ordinal, (row, expected_path) in enumerate(
                zip(raw_rows, baseline_paths, strict=True),
                start=1,
            ):
                payload = bytes(row["payload"])
                actual_sha = sha256(payload).hexdigest()
                if (
                    row["ordinal"] != ordinal
                    or row["zip_path"] != expected_path
                    or row["byte_count"] != len(payload)
                    or row["blob_byte_count"] != len(payload)
                ):
                    raise _SnapshotIntegrityFailure(
                        "Frozen baseline member identity or byte count differs.",
                        issue=PaperReviewIssue(
                            kind="identity_mismatch",
                            path=expected_path,
                            source_ref="paper/ledger-origin.json#baseline.members",
                            expected_sha256=cast(str, row["sha256"]),
                            actual_sha256=actual_sha,
                            ref_chain=(
                                "paper-review.json#baseline.result_ref",
                            ),
                        ),
                    )
                if row["sha256"] != actual_sha:
                    raise _SnapshotIntegrityFailure(
                        "Frozen baseline member SHA-256 differs.",
                        issue=PaperReviewIssue(
                            kind="hash_mismatch",
                            path=expected_path,
                            source_ref="paper/ledger-origin.json#baseline.members",
                            expected_sha256=cast(str, row["sha256"]),
                            actual_sha256=actual_sha,
                            ref_chain=(
                                "paper-review.json#baseline.result_ref",
                            ),
                        ),
                    )
                baseline_members.append((expected_path, payload))

            request = self._service._request_record(
                connection,
                request_row,
            )
            origin_rows = connection.execute(
                """
                SELECT *
                FROM paper_ledger_origins
                WHERE ledger_origin_id = ?
                """,
                (identity.ledger_origin_id,),
            ).fetchall()
            if len(origin_rows) != 1:
                raise _SnapshotIntegrityFailure(
                    "Frozen ledger origin identity is not unique."
                )
            ledger = self._service._build_ledger_origin(
                connection,
                record=request.trader,
                origin=origin_rows[0],
            )
            if (
                ledger.baseline.rejection_count != 14
                or ledger.baseline.closest_rejection_refs
                != (
                    "rejection_000014",
                    "rejection_000013",
                    "rejection_000012",
                )
            ):
                raise _SnapshotIntegrityFailure(
                    "Frozen baseline rejection profile differs from the approved profile.",
                    issue=PaperReviewIssue(
                        kind="count_mismatch",
                        path=f"baseline/events/{ledger.baseline.run_id}.json",
                        source_ref="paper/ledger-origin.json#baseline",
                        expected_sha256=None,
                        actual_sha256=None,
                        ref_chain=(
                            "paper-review.json#baseline.result_ref",
                            "baseline/result.json#events_ref",
                        ),
                    ),
                )
            equity_rows = connection.execute(
                """
                SELECT *
                FROM paper_ledger_equity_points
                WHERE ledger_origin_id = ?
                ORDER BY ordinal
                """,
                (identity.ledger_origin_id,),
            ).fetchall()
            event_rows = connection.execute(
                """
                SELECT ordinal, event_type, payload_json, occurred_at
                FROM paper_trader_events
                WHERE trader_id = ?
                ORDER BY ordinal
                """,
                (identity.trader_id,),
            ).fetchall()
            if len(equity_rows) != 1 or len(event_rows) != 4:
                raise _SnapshotIntegrityFailure(
                    "Frozen paper streams differ from their high-water marks."
                )
            equity = {
                key: equity_rows[0][key]
                for key in (
                    "ordinal",
                    "occurred_at",
                    "currency",
                    "cash",
                    "equity",
                    "realized_pnl",
                    "unrealized_pnl",
                )
            }
            events: list[Mapping[str, object]] = []
            for row in event_rows:
                event_payload = _decode_canonical_json(row["payload_json"])
                if not isinstance(event_payload, dict):
                    raise _SnapshotIntegrityFailure(
                        "Frozen creation event payload is not an object."
                    )
                events.append(
                    {
                        "ordinal": row["ordinal"],
                        "event_type": row["event_type"],
                        "occurred_at": row["occurred_at"],
                        "payload": event_payload,
                    }
                )
            return _FrozenSource(
                identity=identity,
                request=request,
                ledger=ledger,
                equity_point=equity,
                creation_events=tuple(events),
                baseline_members=tuple(baseline_members),
            )
        except _SnapshotIntegrityFailure:
            raise
        except (
            json.JSONDecodeError,
            KeyError,
            PaperReviewDomainError,
            PaperTraderStoreIntegrityError,
            sqlite3.Error,
            TypeError,
            UnicodeDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise _SnapshotIntegrityFailure(
                "Frozen paper review evidence is malformed or inconsistent."
            ) from exc
        finally:
            connection.close()

    def _build_package(self, source: _FrozenSource) -> _Package:
        identity = source.identity
        request = source.request
        ledger = source.ledger
        run_id = request.trader.baseline.run_id
        expected_paths = _review_member_paths(run_id)
        if tuple(path for path, _ in source.baseline_members) != expected_paths[6:]:
            raise _SnapshotIntegrityFailure(
                "Frozen baseline member paths differ from the review profile."
            )

        ledger_document = ledger.model_dump(by_alias=True, mode="json")
        trades_document = {
            "schema": "paper_trades.v1",
            "trader_id": identity.trader_id,
            "captured_at": identity.captured_at,
            "high_water_mark": 0,
            "count": 0,
            "trades": [],
        }
        equity_document = {
            "schema": "paper_equity.v1",
            "trader_id": identity.trader_id,
            "captured_at": identity.captured_at,
            "high_water_mark": 1,
            "count": 1,
            "points": [dict(source.equity_point)],
        }
        events_document = {
            "schema": "paper_creation_events.v2",
            "trader_id": identity.trader_id,
            "captured_at": identity.captured_at,
            "high_water_mark": 4,
            "count": 4,
            "events": [dict(event) for event in source.creation_events],
        }
        closest_refs = list(ledger.baseline.closest_rejection_refs)
        expected_actual_document = {
            "schema": "paper_expected_actual.v1",
            "snapshot_id": identity.snapshot_id,
            "trader_id": identity.trader_id,
            "evaluation_status": "not_evaluable",
            "evaluation_reason": "engine_not_enabled",
            "summary": {
                "expected": 0,
                "actual": 0,
                "matched": 0,
                "missed": 0,
                "extra": 0,
                "average_slippage_points": None,
                "expectancy_delta_r": None,
            },
            "comparisons": [],
            "baseline_rejections": {
                "count": ledger.baseline.rejection_count,
                "closest_algorithm": ledger.baseline.closest_algorithm,
                "closest_refs": closest_refs,
                "source_ref": expected_paths[9],
            },
        }

        sidecars: list[tuple[str, bytes]] = [
            (expected_paths[1], _generated_json_bytes(ledger_document)),
            (expected_paths[2], _generated_json_bytes(trades_document)),
            (expected_paths[3], _generated_json_bytes(equity_document)),
            (expected_paths[4], _generated_json_bytes(events_document)),
            (
                expected_paths[5],
                _generated_json_bytes(expected_actual_document),
            ),
            *source.baseline_members,
        ]
        sidecar_manifest = [
            {
                "path": path,
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
            for path, payload in sidecars
        ]
        main_document = {
            "schema": "paper-review.v1",
            "snapshot": {
                "snapshot_id": identity.snapshot_id,
                "request_id": identity.request_id,
                "captured_at": identity.captured_at,
                "high_water_marks": {
                    "trades": 0,
                    "equity": 1,
                    "events": 4,
                    "expected_decisions": 0,
                },
            },
            "trader": {
                "trader_id": identity.trader_id,
                "status": "provisioned",
                "created_at": request.trader.created_at,
                "last_status_change_at": request.trader.lifecycle.as_of,
            },
            "strategy": {
                "strategy_version_id": ledger.strategy.strategy_id,
                "name": ledger.strategy.name,
                "content_sha256": ledger.strategy.content_sha256,
            },
            "contract": {
                "contract_id": ledger.contract.contract_id,
                "exchange": ledger.contract.exchange,
                "timezone": ledger.contract.timezone,
            },
            "baseline": {
                "run_id": ledger.baseline.run_id,
                "result_sha256": ledger.baseline.result_sha256,
                "result_ref": expected_paths[6],
            },
            "account_origin": {
                "account_id": ledger.account.account_id,
                "currency": ledger.account.currency,
                "initial_capital": ledger.account.initial_capital,
                "independent_account": ledger.account.independent_account,
            },
            "as_of_state": {
                "cash": ledger.balances.cash,
                "equity": ledger.balances.equity,
                "realized_pnl": ledger.balances.realized_pnl,
                "unrealized_pnl": ledger.balances.unrealized_pnl,
                "open_positions": [],
                "safety_status": {
                    "state": ledger.safety.state,
                    "drawdown_r": ledger.safety.drawdown_r,
                    "loss_streak": ledger.safety.loss_streak,
                },
            },
            "summary": {
                "evaluation_status": "not_evaluable",
                "evaluation_reason": "engine_not_enabled",
                "expected_trades": 0,
                "actual_trades": 0,
                "matched": 0,
                "missed": 0,
                "extra": 0,
                "average_slippage_points": None,
                "expectancy_delta_r": None,
            },
            "interpretation": {
                "owner_view": ledger.interpretation.owner_view,
                "categories": list(ledger.interpretation.categories),
                "supporting_event_refs": [
                    {
                        "path": item.path,
                        "event_id": item.evidence_id,
                    }
                    for item in ledger.interpretation.supporting_evidence_refs
                ],
            },
            "refs": {
                "ledger_origin": expected_paths[1],
                "paper_trades": expected_paths[2],
                "paper_equity": expected_paths[3],
                "paper_events": expected_paths[4],
                "expected_actual": expected_paths[5],
            },
            "members": sidecar_manifest,
        }
        main_bytes = _generated_json_bytes(main_document)
        members = ((expected_paths[0], main_bytes), *sidecars)
        if tuple(path for path, _ in members) != expected_paths:
            raise _SnapshotIntegrityFailure(
                "Generated review member order differs from the exact profile."
            )
        manifest = tuple(
            PaperEvidenceMember(
                path=path,
                bytes=len(payload),
                sha256=sha256(payload).hexdigest(),
            )
            for path, payload in members
        )
        return _Package(
            members=members,
            manifest=manifest,
            display_filename=_display_filename(source.identity),
        )

    def _terminal_opener(
        self,
        *,
        source: _FrozenSource,
        display_filename: str,
        artifact_bytes: int,
        artifact_sha256: str,
    ) -> str:
        if (
            type(artifact_bytes) is not int
            or artifact_bytes <= 0
            or artifact_bytes > _MAX_SAFE_INTEGER
            or len(artifact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in artifact_sha256)
        ):
            raise _ArtifactBuildFailure(
                "artifact identity is not canonical for the terminal opener"
            )
        identity = source.identity
        strategy = source.ledger.strategy
        baseline = source.ledger.baseline
        return f"""你會收到一個不可變 P6 review ZIP：{display_filename}

身份（先同 paper-review.json 逐項核對）：
- schema：paper-review.v1
- snapshot_id：{identity.snapshot_id}
- request_id：{identity.request_id}
- trader_id：{identity.trader_id}
- captured_at UTC：{identity.captured_at}
- strategy_version_id：{strategy.strategy_id}
- strategy content_sha256：{strategy.content_sha256}
- baseline run_id：{baseline.run_id}
- baseline result_sha256：{baseline.result_sha256}
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
8. baseline/trades/{baseline.run_id}.json
9. baseline/equity/{baseline.run_id}.json
10. baseline/events/{baseline.run_id}.json

請按以下次序處理：
1. 先驗 paper-review.json 以上身份，再逐項驗 members path、bytes、SHA-256及引用。
2. 讀 divergence/expected-actual.json；零交易 profile 必須保持 not_evaluable，不能寫成「沒有偏離」。
3. 如需追查，再讀 paper sidecars、baseline/result.json及它引用的三個 baseline sidecars。
4. 分開判斷 market、data、execution、strategy-understanding、unknown；每個結論附 ZIP 內 path 及 evidence id。
5. 明確列出「已證明」、「未能判斷」及「下一步需要甚麼證據」。

禁止修改或覆蓋舊 strategy、trader、baseline、ledger、snapshot或 ZIP。
如證據支持修改策略，只可輸出一個直接由 {strategy.strategy_id} 衍生的新 strategy.v1，
保留 parent strategy ID及content SHA lineage；不要啟動交易、IB、Telegram或寫入舊記錄。
新策略必須回到策略工作台匯入、驗證、Owner確認，再經數據、回測、結果及建立新 trader。
"""

    def _prepare_artifact_root(self) -> None:
        _ensure_safe_directory(self._artifact_root)
        _ensure_safe_directory(self._artifact_root / ".staging")

    def _new_staging_path(self) -> Path:
        staging = self._artifact_root / ".staging"
        _assert_no_reparse_chain(staging)
        descriptor, name = tempfile.mkstemp(
            prefix=".paper-review-",
            suffix=".zip",
            dir=staging,
        )
        os.close(descriptor)
        path = Path(name)
        _assert_regular_non_reparse_file(path)
        return path

    def _write_temp_zip(
        self,
        path: Path,
        *,
        package: _Package,
        identity: _RequestIdentity,
    ) -> None:
        names = tuple(member_path for member_path, _ in package.members)
        if len(names) != len(set(names)):
            raise _ArtifactBuildFailure("review ZIP member paths are duplicated")
        for name in names:
            _validate_member_path(name)
        try:
            with ZipFile(
                path,
                mode="w",
                compression=ZIP_STORED,
                allowZip64=True,
            ) as archive:
                archive.comment = b""
                for index, (member_path, payload) in enumerate(
                    package.members,
                    start=1,
                ):
                    self._set_progress(
                        identity,
                        completed_parts=index - 1,
                        current_part=member_path,
                    )
                    info = ZipInfo(
                        filename=member_path,
                        date_time=_ZIP_TIMESTAMP,
                    )
                    info.compress_type = ZIP_STORED
                    info.create_system = 3
                    info.external_attr = _ZIP_MODE << 16
                    info.extra = b""
                    info.comment = b""
                    archive.writestr(
                        info,
                        payload,
                        compress_type=ZIP_STORED,
                    )
                    self._set_progress(
                        identity,
                        completed_parts=index,
                        current_part=None,
                    )
        except (BadZipFile, OSError):
            raise
        except Exception as exc:
            if isinstance(exc, _HookFailure):
                raise
            raise _ArtifactBuildFailure(
                "temporary review ZIP could not be written"
            ) from exc

    def _set_progress(
        self,
        identity: _RequestIdentity,
        *,
        completed_parts: int,
        current_part: str | None,
    ) -> None:
        self._service.builder_registry.update(
            store_key=self._service._store_key,
            request_id=identity.request_id,
            builder_instance_id=identity.builder_instance_id,
            progress=PaperReviewProgress(
                completed_parts=completed_parts,
                total_parts=10,
                current_part=current_part,
            ),
        )

    def _verify_zip(
        self,
        path: Path,
        *,
        package: _Package,
        source: _FrozenSource,
    ) -> None:
        _assert_regular_non_reparse_file(path)
        expected_names = [name for name, _ in package.members]
        try:
            with ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if archive.comment != b"":
                    raise _ArtifactBuildFailure(
                        "review ZIP archive comment is not empty"
                    )
                if [info.filename for info in infos] != expected_names:
                    raise _ArtifactBuildFailure(
                        "review ZIP member order differs"
                    )
                if len({info.filename for info in infos}) != _TOTAL_PARTS:
                    raise _ArtifactBuildFailure(
                        "review ZIP has duplicate or missing members"
                    )
                actual_members: dict[str, bytes] = {}
                for info, (expected_path, expected_payload) in zip(
                    infos,
                    package.members,
                    strict=True,
                ):
                    _validate_member_path(info.filename)
                    if (
                        info.filename != expected_path
                        or info.compress_type != ZIP_STORED
                        or info.date_time != _ZIP_TIMESTAMP
                        or info.extra != b""
                        or info.comment != b""
                        or ((info.external_attr >> 16) & 0o777) != _ZIP_MODE
                    ):
                        raise _ArtifactBuildFailure(
                            "review ZIP member metadata differs"
                        )
                    payload = archive.read(info)
                    if (
                        payload != expected_payload
                        or len(payload) != package.manifest[
                            len(actual_members)
                        ].bytes
                        or sha256(payload).hexdigest()
                        != package.manifest[len(actual_members)].sha256
                    ):
                        raise _ArtifactBuildFailure(
                            "review ZIP member bytes or SHA-256 differ"
                        )
                    actual_members[expected_path] = payload
        except _ArtifactBuildFailure:
            raise
        except (BadZipFile, OSError):
            raise
        except Exception as exc:
            raise _ArtifactBuildFailure(
                "temporary review ZIP could not be verified"
            ) from exc
        self._verify_package_documents(
            actual_members,
            package=package,
            source=source,
        )

    @staticmethod
    def _verify_package_documents(
        members: Mapping[str, bytes],
        *,
        package: _Package,
        source: _FrozenSource,
    ) -> None:
        parsed: dict[str, object] = {}
        for path, expected_payload in package.members[:6]:
            try:
                value = json.loads(
                    expected_payload.decode("utf-8"),
                    parse_constant=lambda value: (_reject_constant(value)),
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise _ArtifactBuildFailure(
                    f"generated member {path} is not strict JSON"
                ) from exc
            if _generated_json_bytes(value) != expected_payload:
                raise _ArtifactBuildFailure(
                    f"generated member {path} is not canonical JSON"
                )
            parsed[path] = value

        main = parsed["paper-review.json"]
        expected_actual = parsed["divergence/expected-actual.json"]
        if not isinstance(main, dict) or not isinstance(expected_actual, dict):
            raise _ArtifactBuildFailure(
                "review main or divergence member is not an object"
            )
        expected_main_keys = {
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
        if set(main) != expected_main_keys:
            raise _ArtifactBuildFailure(
                "review main top-level keys differ from the exact profile"
            )
        expected_manifest = [
            member.model_dump(mode="json")
            for member in package.manifest[1:]
        ]
        if main.get("members") != expected_manifest:
            raise _ArtifactBuildFailure(
                "review main sidecar manifest differs"
            )
        expected_refs = {
            "ledger_origin": package.members[1][0],
            "paper_trades": package.members[2][0],
            "paper_equity": package.members[3][0],
            "paper_events": package.members[4][0],
            "expected_actual": package.members[5][0],
        }
        if main.get("refs") != expected_refs:
            raise _ArtifactBuildFailure("review main refs differ")

        run_id = source.ledger.baseline.run_id
        try:
            baseline_main = json.loads(
                members["baseline/result.json"].decode("utf-8"),
                parse_constant=lambda value: (_reject_constant(value)),
            )
            baseline_events = json.loads(
                members[f"baseline/events/{run_id}.json"].decode("utf-8"),
                parse_constant=lambda value: (_reject_constant(value)),
            )
        except (
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            raise _ArtifactBuildFailure(
                "captured baseline refs cannot be verified"
            ) from exc
        if not isinstance(baseline_main, dict) or not isinstance(
            baseline_events, dict
        ):
            raise _ArtifactBuildFailure(
                "captured baseline main or events is not an object"
            )
        expected_baseline_refs = {
            "trades_ref": f"trades/{run_id}.json",
            "equity_curve_ref": f"equity/{run_id}.json",
            "events_ref": f"events/{run_id}.json",
        }
        if any(
            baseline_main.get(key) != value
            for key, value in expected_baseline_refs.items()
        ):
            raise _ArtifactBuildFailure(
                "captured baseline result refs differ"
            )
        rejection_evidence = baseline_events.get("rejection_evidence")
        if not isinstance(rejection_evidence, list):
            raise _ArtifactBuildFailure(
                "captured baseline rejection evidence is missing"
            )
        evidence_ids = [
            item.get("evidence_id")
            for item in rejection_evidence
            if isinstance(item, dict)
        ]
        closest_refs = list(source.ledger.baseline.closest_rejection_refs)
        if (
            len(evidence_ids) != source.ledger.baseline.rejection_count
            or len(set(evidence_ids)) != len(evidence_ids)
            or any(reference not in evidence_ids for reference in closest_refs)
        ):
            raise _ArtifactBuildFailure(
                "captured baseline closest evidence refs differ"
            )
        baseline_rejections = expected_actual.get("baseline_rejections")
        if (
            not isinstance(baseline_rejections, dict)
            or baseline_rejections.get("count")
            != source.ledger.baseline.rejection_count
            or baseline_rejections.get("closest_refs") != closest_refs
            or baseline_rejections.get("source_ref")
            != f"baseline/events/{run_id}.json"
        ):
            raise _ArtifactBuildFailure(
                "review divergence baseline refs differ"
            )

    def _hash_file(self, path: Path) -> tuple[int, str]:
        _assert_regular_non_reparse_file(path)
        digest = sha256()
        byte_count = 0
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(self._hash_chunk_size)
                if not chunk:
                    break
                byte_count += len(chunk)
                digest.update(chunk)
        if byte_count <= 0 or byte_count > _MAX_SAFE_INTEGER:
            raise _ArtifactBuildFailure(
                "review ZIP byte count is not a positive safe integer"
            )
        return byte_count, digest.hexdigest()

    def _publish(
        self,
        temp_path: Path,
        *,
        artifact_bytes: int,
        artifact_sha256: str,
    ) -> tuple[str, Path]:
        relative = (
            PurePosixPath("sha256")
            / artifact_sha256[:2]
            / f"{artifact_sha256}.zip"
        )
        relative_text = str(relative)
        final_parent = self._artifact_root / "sha256" / artifact_sha256[:2]
        _ensure_safe_directory(final_parent)
        final_path = final_parent / f"{artifact_sha256}.zip"
        if os.path.lexists(final_path):
            self._adopt_existing(
                final_path,
                temp_path=temp_path,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha256,
            )
            return relative_text, final_path
        try:
            os.link(temp_path, final_path)
        except FileExistsError:
            self._adopt_existing(
                final_path,
                temp_path=temp_path,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_sha256,
            )
            return relative_text, final_path
        _assert_regular_non_reparse_file(final_path)
        temp_path.unlink()
        return relative_text, final_path

    def _adopt_existing(
        self,
        final_path: Path,
        *,
        temp_path: Path,
        artifact_bytes: int,
        artifact_sha256: str,
    ) -> None:
        try:
            _assert_regular_non_reparse_file(final_path)
            existing_bytes, existing_sha = self._hash_file(final_path)
        except (OSError, ValueError, _ArtifactBuildFailure) as exc:
            raise _ArtifactUnavailable(
                "Existing content-addressed artifact cannot be verified."
            ) from exc
        if (
            existing_bytes != artifact_bytes
            or existing_sha != artifact_sha256
        ):
            raise _ArtifactUnavailable(
                "Existing content-addressed artifact differs and was not overwritten."
            )
        temp_path.unlink()

    def _verify_final(
        self,
        final_path: Path,
        *,
        artifact_bytes: int,
        artifact_sha256: str,
    ) -> None:
        try:
            final_bytes, final_sha = self._hash_file(final_path)
        except (OSError, ValueError, _ArtifactBuildFailure) as exc:
            raise _ArtifactUnavailable(
                "Published content-addressed artifact cannot be verified."
            ) from exc
        if final_bytes != artifact_bytes or final_sha != artifact_sha256:
            raise _ArtifactUnavailable(
                "Published content-addressed artifact identity differs."
            )


def _generated_json_bytes(value: object) -> bytes:
    return _canonical_json(value).encode("utf-8") + b"\n"


def _display_filename(identity: _RequestIdentity) -> str:
    captured = datetime.fromisoformat(
        identity.captured_at.removesuffix("Z") + "+00:00"
    )
    if captured.tzinfo is None or captured.utcoffset() != UTC.utcoffset(captured):
        raise _SnapshotIntegrityFailure(
            "Review captured_at is not canonical UTC."
        )
    compact = captured.strftime("%Y%m%dT%H%M%S") + (
        f"{captured.microsecond:06d}Z"
    )
    return f"paper-review-{identity.trader_id}-{compact}.zip"


def _validate_member_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path != path.strip()
        or "\x00" in path
        or "\\" in path
        or ":" in path
    ):
        raise ValueError("ZIP member path is unsafe")
    candidate = PurePosixPath(path)
    parts = path.split("/")
    if (
        candidate.is_absolute()
        or str(candidate) != path
        or any(
            not part
            or part in {".", ".."}
            or part.startswith(".")
            for part in parts
        )
    ):
        raise ValueError("ZIP member path is unsafe")
    return path


def _ensure_safe_directory(path: Path) -> None:
    _assert_no_reparse_chain(path)
    path.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_chain(path)
    if not path.is_dir():
        raise ValueError("artifact directory is not a directory")


def _assert_no_reparse_chain(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if not os.path.lexists(current):
            continue
        metadata = os.lstat(current)
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(metadata.st_mode) or (
            reparse_flag and attributes & reparse_flag
        ):
            raise ValueError(
                "artifact path contains a symlink or reparse point"
            )


def _assert_regular_non_reparse_file(path: Path) -> None:
    _assert_no_reparse_chain(path)
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("artifact path is not a regular file")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")
