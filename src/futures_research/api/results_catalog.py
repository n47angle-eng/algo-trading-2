"""Read-only catalog over on-disk result.v1 main files and sidecars (WO-006 / 6-2).

Does not recompute scorecards or touch engines — pure filesystem consumer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from futures_research.api.result_main_validation import (
    ResultMainValidationError,
    ValidatedResultMain,
    validate_result_main,
)
from futures_research.backtest.evidence import (
    EvidenceValidationError,
    validate_serialized_evidence_bundle,
)

_RUN_ID_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


class ResultNotFoundError(LookupError):
    """Raised when a run_id has no main result.v1 file."""


class ResultArtifactIntegrityError(ValueError):
    """Raised when a new complete result points at missing or partial evidence.

    Batch 3 result mains deliberately carry an explicit completeness marker.
    Once that marker exists, a reader must never silently reinterpret a broken
    sidecar as an old unavailable artifact.
    """


@dataclass(frozen=True, slots=True)
class VerifiedResultSnapshot:
    """One captured result main after the shared evidence integrity gate."""

    document: dict[str, Any]
    main_bytes: bytes
    validated_main: ValidatedResultMain


@dataclass(frozen=True, slots=True)
class ResultExportArtifacts:
    """The four immutable byte payloads allowed in a single-run export."""

    run_id: str
    members: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class ResultsCatalog:
    """Filesystem-backed result artifact index."""

    results_root: Path

    def list_run_summaries(self) -> list[dict[str, Any]]:
        """Return compact rows for the P5 batch compare table."""
        summaries: list[dict[str, Any]] = []
        for path in self._main_result_paths():
            document = self._read_json(path)
            run_id = self._run_id_from_document(document, fallback=path.stem)
            self._assert_result_evidence_integrity(document, run_id=run_id)
            summaries.append(self._summary_from_document(document, path=path))
        summaries.sort(key=lambda row: str(row.get("run_id") or ""), reverse=True)
        return summaries

    def get_result(self, run_id: str) -> dict[str, Any]:
        """Return the full result.v1 main document for one run."""
        path = self._main_path_for_run(run_id)
        document = self._read_json(path)
        self._assert_result_evidence_integrity(document, run_id=run_id)
        return document

    def get_verified_snapshot(self, run_id: str) -> VerifiedResultSnapshot:
        """Capture the exact main bytes after applying the shared result gate."""
        path = self._main_path_for_run(run_id)
        main_bytes = self._read_bytes(path)
        document = self._decode_json_bytes(
            main_bytes,
            path=path,
            reject_nonstandard_constants=True,
        )
        try:
            validated_main = validate_result_main(document, expected_run_id=run_id)
        except ResultMainValidationError as exc:
            raise ResultArtifactIntegrityError(str(exc)) from exc
        self._assert_result_evidence_integrity(document, run_id=run_id)
        return VerifiedResultSnapshot(
            document=document,
            main_bytes=main_bytes,
            validated_main=validated_main,
        )

    def get_export_artifacts(self, run_id: str) -> ResultExportArtifacts:
        """Return an exportable complete bundle without rewriting any artifact."""
        snapshot = self.get_verified_snapshot(run_id)
        document = snapshot.document
        if not self._main_declares_complete_evidence(document):
            msg = (
                f"legacy result {run_id} cannot prove the complete decision evidence "
                "required for result.v1 export"
            )
            raise ResultArtifactIntegrityError(msg)
        expected_refs = {
            "trades_ref": f"trades/{run_id}.json",
            "equity_curve_ref": f"equity/{run_id}.json",
            "events_ref": f"events/{run_id}.json",
        }
        for field, expected in expected_refs.items():
            if document.get(field) != expected:
                msg = f"result {run_id} {field} must exactly equal {expected}"
                raise ResultArtifactIntegrityError(msg)

        captured: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for field, ref in expected_refs.items():
            path = (self.results_root / ref).resolve()
            try:
                self._assert_under_root(path)
            except ResultNotFoundError as exc:
                msg = f"result {run_id} has an unsafe {field}"
                raise ResultArtifactIntegrityError(msg) from exc
            if not path.is_file():
                msg = f"result {run_id} is missing required {field}: {ref}"
                raise ResultArtifactIntegrityError(msg)
            raw = self._read_bytes(path)
            captured[field] = (raw, self._decode_json_bytes(raw, path=path))

        trades = captured["trades_ref"][1]
        events = captured["events_ref"][1]
        equity = captured["equity_curve_ref"][1]
        self._assert_complete_trade_evidence(trades, expected_run_id=run_id)
        self._assert_complete_event_evidence(events, expected_run_id=run_id)
        try:
            validate_serialized_evidence_bundle(
                events=events.get("events"),
                rejection_evidence=events.get("rejection_evidence"),
                evidence_summary=events.get("evidence_summary"),
                trades=trades.get("trades"),
            )
        except EvidenceValidationError as exc:
            msg = f"complete evidence bundle for {run_id} is invalid: {exc}"
            raise ResultArtifactIntegrityError(msg) from exc
        self._assert_equity_sidecar(equity, expected_run_id=run_id)

        return ResultExportArtifacts(
            run_id=run_id,
            members=(
                ("result.json", snapshot.main_bytes),
                (expected_refs["trades_ref"], captured["trades_ref"][0]),
                (expected_refs["equity_curve_ref"], captured["equity_curve_ref"][0]),
                (expected_refs["events_ref"], captured["events_ref"][0]),
            ),
        )

    def get_trades(self, run_id: str) -> dict[str, Any]:
        """Return the trades sidecar (or empty list if ref missing / empty)."""
        document = self.get_result(run_id)
        if self._main_declares_complete_evidence(document):
            return self._complete_trades(document, run_id=run_id)
        ref = str(document.get("trades_ref") or f"trades/{run_id}.json")
        path = (self.results_root / ref).resolve()
        self._assert_under_root(path)
        if not path.is_file():
            return {
                "schema": "trades.v1",
                "run_id": run_id,
                "trades": [],
                "decision_evidence_complete": False,
                "decision_evidence_availability": "unavailable",
            }
        payload = self._read_json(path)
        if "run_id" not in payload:
            payload = {**payload, "run_id": run_id}
        if "decision_evidence_complete" not in payload:
            # Historical sidecars predate Batch 3.  Absence is not a known-empty
            # decision set, so do not fabricate ``decision_evidence: []``.
            payload = {
                **payload,
                "decision_evidence_complete": False,
                "decision_evidence_availability": "unavailable",
            }
        else:
            self._assert_complete_trade_evidence(payload, expected_run_id=run_id)
        return payload

    def get_events(self, run_id: str) -> dict[str, Any]:
        """Return the events sidecar (or empty list if ref missing / empty)."""
        document = self.get_result(run_id)
        if self._main_declares_complete_evidence(document):
            return self._complete_events(document, run_id=run_id)
        ref = str(document.get("events_ref") or f"events/{run_id}.json")
        path = (self.results_root / ref).resolve()
        self._assert_under_root(path)
        if not path.is_file():
            return {
                "schema": "events.v1",
                "run_id": run_id,
                "events": [],
                "evidence_complete": False,
                "evidence_availability": "unavailable",
            }
        payload = self._read_json(path)
        if "run_id" not in payload:
            payload = {**payload, "run_id": run_id}
        if "evidence_complete" not in payload:
            # Keep unavailable old evidence distinct from a new known-empty list.
            payload = {
                **payload,
                "evidence_complete": False,
                "evidence_availability": "unavailable",
            }
        else:
            self._assert_complete_event_evidence(payload, expected_run_id=run_id)
        return payload

    def list_batches(self) -> list[dict[str, Any]]:
        """Return synthetic batch groups until A4b BatchManifest files exist.

        Groups:
        - ``validation`` — runs with ``manifest.validation_run == true``
        - ``standard`` — all other local result mains
        """
        summaries = self.list_run_summaries()
        validation_ids = [
            str(row["run_id"]) for row in summaries if row.get("validation_run") is True
        ]
        standard_ids = [
            str(row["run_id"]) for row in summaries if row.get("validation_run") is not True
        ]
        batches: list[dict[str, Any]] = []
        if validation_ids:
            batches.append(
                {
                    "batch_id": "validation",
                    "label": "Validation runs",
                    "run_count": len(validation_ids),
                    "run_ids": validation_ids,
                    "source": "synthetic_from_result_files",
                }
            )
        if standard_ids:
            batches.append(
                {
                    "batch_id": "standard",
                    "label": "Standard runs",
                    "run_count": len(standard_ids),
                    "run_ids": standard_ids,
                    "source": "synthetic_from_result_files",
                }
            )
        batches.append(
            {
                "batch_id": "all",
                "label": "All local results",
                "run_count": len(summaries),
                "run_ids": [str(row["run_id"]) for row in summaries],
                "source": "synthetic_from_result_files",
            }
        )
        return batches

    def list_batch_runs(self, batch_id: str) -> list[dict[str, Any]]:
        """Return run summaries belonging to a synthetic batch_id."""
        batches = {item["batch_id"]: item for item in self.list_batches()}
        if batch_id not in batches:
            msg = f"unknown batch_id: {batch_id}"
            raise ResultNotFoundError(msg)
        allowed = set(batches[batch_id]["run_ids"])
        return [row for row in self.list_run_summaries() if row["run_id"] in allowed]

    def _main_result_paths(self) -> list[Path]:
        if not self.results_root.is_dir():
            return []
        paths: list[Path] = []
        for path in sorted(self.results_root.glob("*.json")):
            if path.name.startswith("."):
                continue
            paths.append(path)
        return paths

    def _main_path_for_run(self, run_id: str) -> Path:
        if not _RUN_ID_SAFE.fullmatch(run_id):
            msg = f"invalid run_id: {run_id}"
            raise ResultNotFoundError(msg)
        path = (self.results_root / f"{run_id}.json").resolve()
        self._assert_under_root(path)
        if not path.is_file():
            msg = f"result not found: {run_id}"
            raise ResultNotFoundError(msg)
        return path

    def _assert_under_root(self, path: Path) -> None:
        root = self.results_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            msg = "path escapes results root"
            raise ResultNotFoundError(msg) from exc

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = f"cannot read JSON artifact: {path.name}"
            raise ResultArtifactIntegrityError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"expected JSON object: {path}"
            raise ResultArtifactIntegrityError(msg)
        return payload

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            msg = f"cannot read artifact bytes: {path.name}"
            raise ResultArtifactIntegrityError(msg) from exc

    @staticmethod
    def _decode_json_bytes(
        payload: bytes,
        *,
        path: Path,
        reject_nonstandard_constants: bool = False,
    ) -> dict[str, Any]:
        try:
            text = payload.decode("utf-8")
            decoded = (
                json.loads(text, parse_constant=_reject_nonstandard_json_constant)
                if reject_nonstandard_constants
                else json.loads(text)
            )
        except (UnicodeDecodeError, ValueError) as exc:
            msg = f"cannot read JSON artifact: {path.name}"
            raise ResultArtifactIntegrityError(msg) from exc
        if not isinstance(decoded, dict):
            msg = f"expected JSON object: {path}"
            raise ResultArtifactIntegrityError(msg)
        return decoded

    @staticmethod
    def _run_id_from_document(document: dict[str, Any], *, fallback: str) -> str:
        run = document.get("run")
        run_id = run.get("run_id") if isinstance(run, dict) else None
        if isinstance(run_id, str) and run_id:
            return run_id
        return fallback

    @staticmethod
    def _main_declares_complete_evidence(document: dict[str, Any]) -> bool:
        """Return true only for the explicit new-artifact marker.

        Absence is the only legacy path.  A false, null, or malformed marker
        is neither a new complete artifact nor honest historical evidence.
        """
        if "decision_evidence_complete" not in document:
            return False
        if document.get("decision_evidence_complete") is not True:
            msg = "result main decision_evidence_complete must be true or absent for legacy"
            raise ResultArtifactIntegrityError(msg)
        return True

    def _assert_result_evidence_integrity(self, document: dict[str, Any], *, run_id: str) -> None:
        """Apply one gate before any consumer treats a new result as readable."""
        if not self._main_declares_complete_evidence(document):
            return
        trades = self._complete_trades(document, run_id=run_id)
        events = self._complete_events(document, run_id=run_id)
        try:
            validate_serialized_evidence_bundle(
                events=events.get("events"),
                rejection_evidence=events.get("rejection_evidence"),
                evidence_summary=events.get("evidence_summary"),
                trades=trades.get("trades"),
            )
        except EvidenceValidationError as exc:
            msg = f"complete evidence bundle for {run_id} is invalid: {exc}"
            raise ResultArtifactIntegrityError(msg) from exc

    def _complete_trades(self, document: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        payload = self._required_sidecar(document, run_id=run_id, field="trades_ref")
        self._assert_complete_trade_evidence(payload, expected_run_id=run_id)
        return payload

    def _complete_events(self, document: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        payload = self._required_sidecar(document, run_id=run_id, field="events_ref")
        self._assert_complete_event_evidence(payload, expected_run_id=run_id)
        return payload

    def _required_sidecar(
        self,
        document: dict[str, Any],
        *,
        run_id: str,
        field: str,
    ) -> dict[str, Any]:
        raw_ref = document.get(field)
        if not isinstance(raw_ref, str) or not raw_ref or raw_ref != raw_ref.strip():
            msg = f"complete result {run_id} requires a non-blank {field}"
            raise ResultArtifactIntegrityError(msg)
        path = (self.results_root / raw_ref).resolve()
        try:
            self._assert_under_root(path)
        except ResultNotFoundError as exc:
            msg = f"complete result {run_id} has an unsafe {field}"
            raise ResultArtifactIntegrityError(msg) from exc
        if not path.is_file():
            msg = f"complete result {run_id} is missing required {field}: {raw_ref}"
            raise ResultArtifactIntegrityError(msg)
        return self._read_json(path)

    @staticmethod
    def _assert_complete_trade_evidence(
        payload: dict[str, Any],
        *,
        expected_run_id: str,
    ) -> None:
        """Reject an explicit-but-partial Batch 3 trade artifact rather than guessing."""
        if payload.get("schema") != "trades.v1":
            msg = "complete trades evidence requires schema trades.v1"
            raise ResultArtifactIntegrityError(msg)
        if payload.get("run_id") != expected_run_id:
            msg = "complete trades evidence run_id must match its result main"
            raise ResultArtifactIntegrityError(msg)
        if payload.get("decision_evidence_complete") is not True:
            msg = "persisted trades evidence must be explicitly complete or absent for legacy"
            raise ResultArtifactIntegrityError(msg)
        if "decision_evidence_availability" in payload:
            msg = "persisted trades evidence cannot mix complete with availability fallback"
            raise ResultArtifactIntegrityError(msg)
        trades = payload.get("trades")
        if not isinstance(trades, list):
            msg = "complete trades evidence requires a trades list"
            raise ResultArtifactIntegrityError(msg)
        for record in trades:
            if not isinstance(record, dict) or not isinstance(
                record.get("decision_evidence"), dict
            ):
                msg = "complete trades evidence requires every trade decision record"
                raise ResultArtifactIntegrityError(msg)

    @staticmethod
    def _assert_complete_event_evidence(
        payload: dict[str, Any],
        *,
        expected_run_id: str,
    ) -> None:
        """Reject ambiguous explicit evidence states; absent fields remain honest legacy."""
        if payload.get("schema") != "events.v1":
            msg = "complete event evidence requires schema events.v1"
            raise ResultArtifactIntegrityError(msg)
        if payload.get("run_id") != expected_run_id:
            msg = "complete event evidence run_id must match its result main"
            raise ResultArtifactIntegrityError(msg)
        if payload.get("evidence_complete") is not True:
            msg = "persisted event evidence must be explicitly complete or absent for legacy"
            raise ResultArtifactIntegrityError(msg)
        if "evidence_availability" in payload:
            msg = "persisted event evidence cannot mix complete with availability fallback"
            raise ResultArtifactIntegrityError(msg)
        events = payload.get("events")
        if not isinstance(events, list):
            msg = "complete event evidence requires an events list"
            raise ResultArtifactIntegrityError(msg)
        records = payload.get("rejection_evidence")
        summary = payload.get("evidence_summary")
        if not isinstance(records, list) or not isinstance(summary, dict):
            msg = "complete event evidence requires records and a deterministic summary"
            raise ResultArtifactIntegrityError(msg)
        if summary.get("availability") != "available" or summary.get("complete") is not True:
            msg = "complete event evidence summary must be available and complete"
            raise ResultArtifactIntegrityError(msg)
        required_summary = {
            "availability",
            "complete",
            "evaluation_count",
            "rejection_count",
            "layer_reached_counts",
            "blocking_condition_counts",
            "deepest_layer",
            "trade_count",
        }
        if not required_summary.issubset(summary):
            msg = "complete event evidence summary is missing required deterministic fields"
            raise ResultArtifactIntegrityError(msg)
        for key in ("evaluation_count", "rejection_count", "trade_count"):
            value = summary.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                msg = f"complete event evidence summary {key} must be a non-negative integer"
                raise ResultArtifactIntegrityError(msg)
        for key in ("layer_reached_counts", "blocking_condition_counts"):
            value = summary.get(key)
            if not isinstance(value, dict) or any(
                not isinstance(count, int) or isinstance(count, bool) or count < 0
                for count in value.values()
            ):
                msg = f"complete event evidence summary {key} must be a count mapping"
                raise ResultArtifactIntegrityError(msg)
        deepest_layer = summary.get("deepest_layer")
        if deepest_layer is not None and not isinstance(deepest_layer, str):
            msg = "complete event evidence summary deepest_layer must be text or null"
            raise ResultArtifactIntegrityError(msg)

    @staticmethod
    def _assert_equity_sidecar(
        payload: dict[str, Any],
        *,
        expected_run_id: str,
    ) -> None:
        if payload.get("schema") != "equity_curve.v1":
            msg = "result export requires schema equity_curve.v1"
            raise ResultArtifactIntegrityError(msg)
        if payload.get("run_id") != expected_run_id:
            msg = "equity sidecar run_id must match its result main"
            raise ResultArtifactIntegrityError(msg)
        points = payload.get("points")
        if not isinstance(points, list):
            msg = "equity sidecar requires a points list"
            raise ResultArtifactIntegrityError(msg)
        required = {"timestamp", "equity", "cumulative_net_pnl"}
        for ordinal, point in enumerate(points, start=1):
            if not isinstance(point, dict) or set(point) != required:
                msg = f"equity point {ordinal} must contain exactly timestamp/equity/PnL"
                raise ResultArtifactIntegrityError(msg)
            timestamp = point.get("timestamp")
            if not isinstance(timestamp, str):
                msg = f"equity point {ordinal} timestamp must be UTC ISO-8601 text"
                raise ResultArtifactIntegrityError(msg)
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as exc:
                msg = f"equity point {ordinal} timestamp must be UTC ISO-8601 text"
                raise ResultArtifactIntegrityError(msg) from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                msg = f"equity point {ordinal} timestamp must include a timezone"
                raise ResultArtifactIntegrityError(msg)
            for field in ("equity", "cumulative_net_pnl"):
                value = point.get(field)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not isfinite(value)
                ):
                    msg = f"equity point {ordinal} {field} must be a finite native number"
                    raise ResultArtifactIntegrityError(msg)

    def _summary_from_document(
        self,
        document: dict[str, Any],
        *,
        path: Path,
    ) -> dict[str, Any]:
        run_raw = document.get("run")
        run_obj: dict[str, Any] = run_raw if isinstance(run_raw, dict) else {}
        manifest_raw = run_obj.get("manifest")
        manifest: dict[str, Any] = manifest_raw if isinstance(manifest_raw, dict) else {}
        metrics_raw = document.get("metrics")
        metrics: dict[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}
        scorecard_raw = document.get("scorecard")
        scorecard: list[Any] = scorecard_raw if isinstance(scorecard_raw, list) else []
        funnel_raw = document.get("funnel")
        funnel: dict[str, Any] | None = funnel_raw if isinstance(funnel_raw, dict) else None
        run_id = str(run_obj.get("run_id") or path.stem)
        return {
            "run_id": run_id,
            "strategy_version": run_obj.get("strategy_version"),
            "contract_id": manifest.get("contract_id"),
            "session_name": manifest.get("session_name"),
            "range_start": manifest.get("range_start"),
            "range_end": manifest.get("range_end"),
            "validation_run": bool(manifest.get("validation_run")),
            "trade_count": metrics.get("trade_count"),
            "net_r": metrics.get("net_r"),
            "net_pnl": metrics.get("net_pnl"),
            "win_rate": metrics.get("win_rate"),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown_pnl": metrics.get("max_drawdown_pnl"),
            "max_drawdown_r": metrics.get("max_drawdown_r"),
            "expectancy_r": metrics.get("expectancy_r"),
            "scorecard_statuses": [
                {
                    "dim": item.get("dim"),
                    "status": item.get("status"),
                }
                for item in scorecard
                if isinstance(item, dict)
            ],
            "funnel_status": funnel.get("status") if funnel is not None else None,
            "funnel_fills": funnel.get("fills") if funnel is not None else None,
            "has_scorecard": len(scorecard) > 0,
            "has_funnel": funnel is not None,
            "result_file": path.name,
        }
