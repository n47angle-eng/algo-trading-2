"""A2 StrategyVersion store — immutable confirmed strategy documents (WO-006 / 6-5).

Journey S2c: a validated ``strategy.v1`` document becomes an immutable
``StrategyVersion`` that the Owner confirms, and that P4 can then run.

Design decisions (channel [083] Q2):

* ids are system-assigned running numbers ``strategy-NNNN`` (docs/05 §1.1 format);
* the raw YAML ``source_text`` is stored verbatim — re-emitting from a parsed
  object always drifts, and the original text is what the terminal AI wrote;
* de-duplication is by SHA-256 of that text: importing byte-identical content
  returns the existing version instead of minting a twin;
* active records still have only two states — ``draft`` and ``confirmed``;
  final deletion preserves the exact source in a separate, non-runnable
  ``_deleted`` archive instead of adding a third active status.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml

from futures_research.paths import PROJECT_ROOT
from futures_research.strategy.models import StrategyDocument
from futures_research.strategy.parser import (
    ParsedStrategy,
    parse_strategy_document,
    required_provenance_paths,
)

StrategyStatus = Literal["draft", "confirmed"]

_ID_PATTERN = re.compile(r"^strategy-(\d{4,})$")
_SCHEMA = "strategy_version.v1"

# Serializes id allocation and status flips across the API's worker threads.
_STORE_LOCK = threading.Lock()


class StrategyVersionNotFoundError(LookupError):
    """Raised when a strategy id has no stored version file."""


class StrategyDerivationError(ValueError):
    """Raised when a numeric-only derive request cannot be applied exactly."""


class StrategyArchiveCollisionError(RuntimeError):
    """Raised when the immutable archive target already exists."""


class StrategyStorageError(RuntimeError):
    """Raised when strategy storage cannot prove or publish lifecycle truth."""


class StrategyStorageIntegrityError(StrategyStorageError):
    """Raised when active record bytes cannot prove their stored identity or digest."""


@dataclass(frozen=True, slots=True)
class NumericPatch:
    """One exact numeric leaf replacement approved by the parent parameter catalog."""

    path: str
    value: int | float


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """Result of an import attempt on an already-validated document."""

    record: dict[str, Any]
    #: True when byte-identical content already existed; no new version was minted.
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class DeriveOutcome:
    """Result of deriving one immutable direct-child strategy version."""

    parent_strategy_id: str
    changed_paths: tuple[str, ...]
    record: dict[str, Any]
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    """Public facts from one completed lossless strategy archive."""

    strategy_id: str
    deleted_at: str
    archived_status: StrategyStatus

    @property
    def archived_to(self) -> str:
        """Return the fixed repo-relative public path, never the injected local root."""
        return f"data/strategies/_deleted/{self.strategy_id}.yaml"


@dataclass(frozen=True, slots=True)
class StrategyStore:
    """Filesystem-backed A2 StrategyVersion catalog."""

    root: Path

    # --- reads -------------------------------------------------------------

    def list_versions(self, *, status: StrategyStatus | None = None) -> list[dict[str, Any]]:
        """Return stored versions, newest id first, optionally filtered by status."""
        records = [self._read(path) for path in sorted(self._version_paths())]
        if status is not None:
            records = [record for record in records if record.get("status") == status]
        records.sort(key=lambda record: str(record.get("strategy_id") or ""), reverse=True)
        return records

    def get(self, strategy_id: str) -> dict[str, Any]:
        """Return one stored version document."""
        path = self._path_for(strategy_id)
        if not path.is_file():
            msg = f"unknown strategy_id: {strategy_id}"
            raise StrategyVersionNotFoundError(msg)
        return self._read(path)

    def find_by_digest(self, content_sha256: str) -> dict[str, Any] | None:
        """Return the version whose stored YAML hashes to ``content_sha256``."""
        for record in self.list_versions():
            if record.get("content_sha256") == content_sha256:
                return record
        return None

    # --- writes ------------------------------------------------------------

    def import_document(self, source_text: str) -> ImportOutcome:
        """Validate then store a ``strategy.v1`` document as a ``draft`` version.

        Raises ``StrategyValidationError`` when any of the four layers fail — the
        caller renders those issues verbatim for paste-back to the terminal AI.
        """
        parsed = parse_strategy_document(source_text)
        digest = content_digest(source_text)
        with _STORE_LOCK:
            existing = self.find_by_digest(digest)
            if existing is not None:
                return ImportOutcome(record=existing, deduplicated=True)
            record = _build_record(
                strategy_id=self._next_id(),
                source_text=source_text,
                digest=digest,
                parsed=parsed,
            )
            self._write(record)
        return ImportOutcome(record=record, deduplicated=False)

    def confirm(self, strategy_id: str) -> dict[str, Any]:
        """Mark a version confirmed (S2c gate). Idempotent; content never changes."""
        with _STORE_LOCK:
            path = self._path_for(strategy_id)
            if not path.is_file():
                msg = f"unknown strategy_id: {strategy_id}"
                raise StrategyVersionNotFoundError(msg)
            record = self._read_raw(path)
            if record.get("status") == "confirmed":
                return self._with_response_defaults(record)
            # Re-run the single canonical parser immediately before the state
            # transition.  This keeps confirm on the same universe/lineage gate
            # as validate, import, preview, and a new run, even if a draft file
            # was manually damaged after import.
            source_text = record.get("source_text")
            if not isinstance(source_text, str) or not source_text:
                msg = "draft strategy has no readable source_text for canonical validation"
                raise ValueError(msg)
            parse_strategy_document(source_text)
            confirmed = {
                **record,
                "status": "confirmed",
                "confirmed_at": _now_iso(),
            }
            self._write(confirmed)
        return self._with_response_defaults(confirmed)

    def derive(
        self,
        parent_strategy_id: str,
        patches: Sequence[NumericPatch],
    ) -> DeriveOutcome:
        """Create or return one canonical numeric-only immutable direct child."""
        ordered_patches = _validate_patch_request(patches)
        with _STORE_LOCK:
            parent_path = self._path_for(parent_strategy_id)
            if not parent_path.is_file():
                msg = f"unknown strategy_id: {parent_strategy_id}"
                raise StrategyVersionNotFoundError(msg)
            parent_record = self._read_lifecycle_record(
                parent_path,
                strategy_id=parent_strategy_id,
            )
            parent_source = _verified_record_source(
                parent_record,
                strategy_id=parent_strategy_id,
            )

            # This is deliberately the one existing canonical parser. Legacy
            # or manually damaged source fails here rather than entering a
            # lifecycle-only compatibility lane.
            parent_parsed = parse_strategy_document(parent_source)
            allowed_paths = _numeric_catalog_paths(parent_record, parent_parsed)
            for patch in ordered_patches:
                if patch.path not in allowed_paths:
                    msg = (
                        f"patch path is not an existing numeric parameter: {patch.path}; "
                        "choose an exact parameters[].kind=numeric path from the parent"
                    )
                    raise StrategyDerivationError(msg)

            derived_source, derived_parsed = _derive_source_text(
                parent_source=parent_source,
                parent_parsed=parent_parsed,
                parent_strategy_id=parent_strategy_id,
                patches=ordered_patches,
            )
            digest = content_digest(derived_source)
            try:
                existing = self.find_by_digest(digest)
            except (OSError, ValueError) as exc:
                msg = "strategy catalog could not be read safely for derive deduplication"
                raise StrategyStorageError(msg) from exc
            if existing is not None:
                if (
                    existing.get("source_text") != derived_source
                    or existing.get("based_on") != parent_strategy_id
                ):
                    msg = "strategy digest collision or direct-child identity drift"
                    raise StrategyStorageError(msg)
                return DeriveOutcome(
                    parent_strategy_id=parent_strategy_id,
                    changed_paths=tuple(patch.path for patch in ordered_patches),
                    record=existing,
                    deduplicated=True,
                )

            record = _build_record(
                strategy_id=self._next_id(),
                source_text=derived_source,
                digest=digest,
                parsed=derived_parsed,
            )
            try:
                self._write(record)
            except (OSError, ValueError) as exc:
                msg = "derived strategy could not be published atomically"
                raise StrategyStorageError(msg) from exc
        return DeriveOutcome(
            parent_strategy_id=parent_strategy_id,
            changed_paths=tuple(patch.path for patch in ordered_patches),
            record=record,
            deduplicated=False,
        )

    def archive_delete(self, strategy_id: str) -> ArchiveOutcome:
        """Losslessly archive one active record, then remove only its active JSON."""
        with _STORE_LOCK:
            active_path = self._path_for(strategy_id)
            if not active_path.is_file():
                msg = f"unknown strategy_id: {strategy_id}"
                raise StrategyVersionNotFoundError(msg)
            archive_directory = self.root / "_deleted"
            archive_path = archive_directory / f"{strategy_id}.yaml"
            if archive_path.exists():
                msg = f"strategy archive already exists: {strategy_id}"
                raise StrategyArchiveCollisionError(msg)

            record = self._read_lifecycle_record(active_path, strategy_id=strategy_id)
            source_text = _verified_record_source(record, strategy_id=strategy_id)
            status = record.get("status")
            if status not in ("draft", "confirmed"):
                msg = "active strategy has an invalid lifecycle status"
                raise StrategyStorageIntegrityError(msg)
            archived_status: StrategyStatus = status
            deleted_at = _now_iso()
            archive_payload = {
                "schema": "strategy_delete_archive.v1",
                "strategy_id": strategy_id,
                "deleted_at": deleted_at,
                "status": archived_status,
                "content_sha256": content_digest(source_text),
                "source_text": source_text,
            }
            archive_text = yaml.safe_dump(
                archive_payload,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            if yaml.safe_load(archive_text) != archive_payload:
                msg = "strategy archive serialization did not preserve the source text"
                raise StrategyStorageIntegrityError(msg)

            directory_created = False
            temporary: Path | None = None
            archive_published = False
            try:
                if not archive_directory.exists():
                    archive_directory.mkdir()
                    directory_created = True
                elif not archive_directory.is_dir():
                    msg = "strategy archive location is not a directory"
                    raise StrategyStorageError(msg)
                if archive_path.exists():
                    msg = f"strategy archive already exists: {strategy_id}"
                    raise StrategyArchiveCollisionError(msg)

                temporary = archive_directory / (
                    f".{strategy_id}.yaml.{os.getpid()}.{uuid4().hex}.tmp"
                )
                self._write_archive_temp(temporary, archive_text.encode("utf-8"))
                self._publish_archive(temporary, archive_path)
                archive_published = True
                temporary.unlink()
                temporary = None
                try:
                    self._remove_active(active_path)
                except OSError:
                    self._rollback_archive(archive_path, archive_directory)
                    archive_published = False
                    raise
            except StrategyArchiveCollisionError:
                raise
            except StrategyStorageError:
                raise
            except OSError as exc:
                msg = "strategy archive operation failed without deleting the active version"
                raise StrategyStorageError(msg) from exc
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink(missing_ok=True)
                if archive_published and active_path.exists():
                    self._rollback_archive(archive_path, archive_directory)
                    archive_published = False
                if directory_created and archive_directory.is_dir():
                    with suppress(OSError):
                        archive_directory.rmdir()

        return ArchiveOutcome(
            strategy_id=strategy_id,
            deleted_at=deleted_at,
            archived_status=archived_status,
        )

    # --- internals ---------------------------------------------------------

    def _version_paths(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return [path for path in self.root.glob("strategy-*.json") if path.is_file()]

    def _path_for(self, strategy_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(strategy_id):
            msg = f"invalid strategy_id: {strategy_id}"
            raise StrategyVersionNotFoundError(msg)
        return self.root / f"{strategy_id}.json"

    def _next_id(self) -> str:
        highest = 0
        for path in (*self._version_paths(), *self._archive_paths()):
            match = _ID_PATTERN.fullmatch(path.stem)
            if match is not None:
                highest = max(highest, int(match.group(1)))
        return f"strategy-{highest + 1:04d}"

    def _archive_paths(self) -> list[Path]:
        archive_directory = self.root / "_deleted"
        if not archive_directory.is_dir():
            return []
        return [
            path
            for path in archive_directory.glob("strategy-*.yaml")
            if path.is_file()
        ]

    @staticmethod
    def _read_lifecycle_record(path: Path, *, strategy_id: str) -> dict[str, Any]:
        try:
            record = StrategyStore._read_raw(path)
        except (OSError, ValueError) as exc:
            msg = f"active strategy record is unreadable: {strategy_id}"
            raise StrategyStorageIntegrityError(msg) from exc
        if record.get("schema") != _SCHEMA or record.get("strategy_id") != strategy_id:
            msg = "active strategy record identity does not match its storage key"
            raise StrategyStorageIntegrityError(msg)
        return record

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return StrategyStore._with_response_defaults(StrategyStore._read_raw(path))

    @staticmethod
    def _read_raw(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            msg = f"strategy version file must be a JSON object: {path}"
            raise ValueError(msg)
        return payload

    @staticmethod
    def _with_response_defaults(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an API-only null for old records without rewriting their JSON bytes."""
        if "based_on_sketch_origin" in payload:
            return payload
        return {**payload, "based_on_sketch_origin": None}

    def _write(self, record: dict[str, Any]) -> None:
        path = self._path_for(str(record["strategy_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _write_archive_temp(self, path: Path, payload: bytes) -> None:
        """Materialize complete archive bytes at a hidden same-directory path."""
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def _publish_archive(self, temporary: Path, target: Path) -> None:
        """Atomically publish without overwrite; the complete temp remains for cleanup."""
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            msg = f"strategy archive already exists: {target.stem}"
            raise StrategyArchiveCollisionError(msg) from exc

    def _remove_active(self, path: Path) -> None:
        """Remove the active JSON only after the complete archive is visible."""
        path.unlink()

    @staticmethod
    def _rollback_archive(target: Path, archive_directory: Path) -> None:
        """Hide a published archive again when active removal did not complete."""
        if target.exists():
            rollback = archive_directory / f".{target.name}.{uuid4().hex}.tmp"
            target.replace(rollback)
            rollback.unlink(missing_ok=True)


def default_strategy_store() -> StrategyStore:
    """Store rooted at ``data/strategies`` alongside the other run artifacts."""
    return StrategyStore(root=PROJECT_ROOT / "data" / "strategies")


def content_digest(source_text: str) -> str:
    """SHA-256 of the exact YAML bytes the Owner imported."""
    return sha256(source_text.encode("utf-8")).hexdigest()


def _validate_patch_request(patches: Sequence[NumericPatch]) -> tuple[NumericPatch, ...]:
    """Keep the store safe even when called outside the HTTP request model."""
    if not patches:
        msg = "patches must contain at least one numeric change"
        raise StrategyDerivationError(msg)
    seen: set[str] = set()
    ordered: list[NumericPatch] = []
    for patch in patches:
        if (
            not isinstance(patch, NumericPatch)
            or not patch.path
            or patch.path != patch.path.strip()
        ):
            msg = "each patch path must be exact, non-empty, and unpadded"
            raise StrategyDerivationError(msg)
        if patch.path in seen:
            msg = f"patch paths must be unique: {patch.path}"
            raise StrategyDerivationError(msg)
        if type(patch.value) not in (int, float):
            msg = f"patch value must be a finite JSON number: {patch.path}"
            raise StrategyDerivationError(msg)
        if isinstance(patch.value, float) and not isfinite(patch.value):
            msg = f"patch value must be finite: {patch.path}"
            raise StrategyDerivationError(msg)
        seen.add(patch.path)
        ordered.append(patch)
    return tuple(sorted(ordered, key=lambda item: item.path))


def _verified_record_source(record: Mapping[str, Any], *, strategy_id: str) -> str:
    """Require exact active identity, status, source bytes, and digest parity."""
    status = record.get("status")
    if status not in ("draft", "confirmed"):
        msg = f"strategy record has invalid status: {strategy_id}"
        raise StrategyStorageIntegrityError(msg)
    source_text = record.get("source_text")
    if not isinstance(source_text, str) or not source_text:
        msg = f"strategy record has no non-empty source_text: {strategy_id}"
        raise StrategyStorageIntegrityError(msg)
    digest = record.get("content_sha256")
    expected = content_digest(source_text)
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or digest != expected
    ):
        msg = f"strategy record digest does not match source_text: {strategy_id}"
        raise StrategyStorageIntegrityError(msg)
    return source_text


def _numeric_catalog_paths(
    record: Mapping[str, Any],
    parsed: ParsedStrategy,
) -> frozenset[str]:
    """Read the parent detail catalog and prove it matches canonical parser truth."""
    rows = record.get("parameters")
    if not isinstance(rows, list):
        msg = "strategy parameter catalog is missing"
        raise StrategyStorageError(msg)
    paths: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            msg = "strategy parameter catalog contains a malformed row"
            raise StrategyStorageError(msg)
        if row.get("kind") != "numeric":
            continue
        path = row.get("path")
        if not isinstance(path, str) or not path or path != path.strip():
            msg = "strategy numeric parameter catalog contains an invalid path"
            raise StrategyStorageError(msg)
        paths.append(path)
    if len(paths) != len(set(paths)):
        msg = "strategy numeric parameter catalog contains duplicate paths"
        raise StrategyStorageError(msg)
    canonical_paths = frozenset(required_provenance_paths(parsed.document))
    if frozenset(paths) != canonical_paths:
        msg = "strategy numeric parameter catalog does not match canonical parser truth"
        raise StrategyStorageError(msg)
    return canonical_paths


def _derive_source_text(
    *,
    parent_source: str,
    parent_parsed: ParsedStrategy,
    parent_strategy_id: str,
    patches: tuple[NumericPatch, ...],
) -> tuple[str, ParsedStrategy]:
    """Apply only the three approved semantic changes and re-run the canonical parser."""
    loaded = yaml.safe_load(parent_source)
    if not isinstance(loaded, dict):
        msg = "canonical parent source did not load as a mapping"
        raise StrategyStorageError(msg)
    candidate = _detached_copy(loaded)
    meta = candidate.get("meta")
    if not isinstance(meta, dict):
        msg = "canonical parent meta is unavailable"
        raise StrategyStorageError(msg)
    meta["based_on"] = parent_strategy_id

    for patch in patches:
        _set_numeric_path(candidate, patch.path, patch.value)
        _set_owner_provenance(candidate, patch.path)

    first_source = _dump_strategy_source(candidate)
    first_parsed = parse_strategy_document(first_source)
    parent_mapping = parent_parsed.document.model_dump(mode="python", by_alias=True)
    first_mapping = first_parsed.document.model_dump(mode="python", by_alias=True)
    for patch in patches:
        before = _path_value(parent_mapping, patch.path)
        after = _path_value(first_mapping, patch.path)
        if after == before:
            msg = f"patch is a no-op after canonical validation: {patch.path}"
            raise StrategyDerivationError(msg)
        # Normalize equivalent JSON spellings (for example 2 vs 2.0) to the
        # parser's typed value so retries generate byte-identical child YAML.
        _set_numeric_path(candidate, patch.path, after)

    derived_source = _dump_strategy_source(candidate)
    derived_parsed = parse_strategy_document(derived_source)
    return derived_source, derived_parsed


def _detached_copy(value: Any) -> Any:
    """Deep-copy without retaining YAML alias identity between separate paths."""
    if isinstance(value, dict):
        return {_detached_copy(key): _detached_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detached_copy(item) for item in value]
    return deepcopy(value)


def _path_value(mapping: Mapping[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                msg = f"numeric path does not exist in canonical source: {path}"
                raise StrategyStorageError(msg)
            current = current[part]
            continue
        if isinstance(current, list):
            matches = [
                item
                for item in current
                if isinstance(item, Mapping) and item.get("id") == part
            ]
            if len(matches) != 1:
                msg = f"numeric path has no unique id-addressed row: {path}"
                raise StrategyStorageError(msg)
            current = matches[0]
            continue
        msg = f"numeric path crosses a scalar value: {path}"
        raise StrategyStorageError(msg)
    return current


def _set_numeric_path(mapping: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: Any = mapping
    for part in parts[:-1]:
        if isinstance(current, dict):
            if part not in current:
                msg = f"numeric path does not exist in source: {path}"
                raise StrategyStorageError(msg)
            current = current[part]
            continue
        if isinstance(current, list):
            matches = [
                item
                for item in current
                if isinstance(item, dict) and item.get("id") == part
            ]
            if len(matches) != 1:
                msg = f"numeric path has no unique id-addressed row: {path}"
                raise StrategyStorageError(msg)
            current = matches[0]
            continue
        msg = f"numeric path crosses a scalar value: {path}"
        raise StrategyStorageError(msg)
    if not isinstance(current, dict) or parts[-1] not in current:
        msg = f"numeric leaf does not exist in source: {path}"
        raise StrategyStorageError(msg)
    existing = current[parts[-1]]
    if type(existing) not in (int, float):
        msg = f"approved numeric path does not point to a numeric YAML leaf: {path}"
        raise StrategyStorageError(msg)
    current[parts[-1]] = value


def _set_owner_provenance(mapping: dict[str, Any], path: str) -> None:
    provenance = mapping.get("provenance")
    if not isinstance(provenance, list):
        msg = "canonical strategy provenance is unavailable"
        raise StrategyStorageError(msg)
    matches = [
        item
        for item in provenance
        if isinstance(item, dict) and item.get("path") == path
    ]
    if len(matches) != 1:
        msg = f"numeric path has no unique provenance entry: {path}"
        raise StrategyStorageError(msg)
    matches[0]["source"] = "owner_explicit"
    matches[0]["note"] = "Owner UI 微調"


def _dump_strategy_source(mapping: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(mapping),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _build_record(
    *,
    strategy_id: str,
    source_text: str,
    digest: str,
    parsed: ParsedStrategy,
) -> dict[str, Any]:
    document = parsed.document
    return {
        "schema": _SCHEMA,
        "strategy_id": strategy_id,
        "status": "draft",
        "name": document.meta.name,
        "created": document.meta.created.isoformat(),
        "imported_at": _now_iso(),
        "confirmed_at": None,
        "content_sha256": digest,
        # Verbatim YAML: the terminal AI's own text, never re-emitted from the model.
        "source_text": source_text,
        "spec_ref": document.meta.spec_ref,
        "based_on": document.meta.based_on,
        "based_on_sketch": document.meta.based_on_sketch,
        "based_on_sketch_origin": document.meta.based_on_sketch_origin,
        "based_on_insights": list(document.meta.based_on_insights or []),
        "rationale": document.rationale,
        "unquantified_notes": _unquantified_notes(document),
        "universe": {
            "primary_instrument": document.universe.primary_instrument,
            "asset_class": document.universe.asset_class,
            "contracts": list(document.universe.contracts),
            "expansion_rationale": dict(document.universe.expansion_rationale),
            "session": document.universe.session,
        },
        "parameters": _parameter_rows(document),
        "spec": {
            "universe_session": parsed.spec.universe_session,
            "regime_separation_percentile": parsed.spec.regime.separation_percentile,
            "regime_slope_percentile": parsed.spec.regime.slope_percentile,
            "pullback_ema_period": parsed.spec.entry.pullback_ema_period,
            "entry_layers": parsed.spec.entry.entry_layers,
            "signal_bars": [kind.value for kind in parsed.spec.entry.signal_bars],
            "target_r_multiple": parsed.spec.risk.target_r_multiple,
            "stop_offset_ticks": parsed.spec.risk.stop_offset_ticks,
        },
    }


def _unquantified_notes(document: StrategyDocument) -> list[dict[str, Any]]:
    """Normalize the iron-gate notes; a plain string entry is still a note."""
    notes: list[dict[str, Any]] = []
    for item in document.unquantified_notes:
        if isinstance(item, str):
            notes.append({"note": item, "action_needed": None})
        else:
            notes.append({"note": item.note, "action_needed": item.action_needed})
    return notes


def _parameter_rows(document: StrategyDocument) -> list[dict[str, Any]]:
    """Read-only P2 parameter table joined with ``provenance`` (D9: UI never edits).

    Rows without a provenance path are structural declarations rather than
    numeric parameters — they are labelled as such rather than left blank, so a
    missing source is never confused with an unlabelled one.
    """
    provenance = {entry.path: entry for entry in document.provenance}

    def row(label: str, value: str, path: str | None) -> dict[str, Any]:
        entry = provenance.get(path) if path is not None else None
        return {
            "label": label,
            "value": value,
            "path": path,
            "source": entry.source if entry is not None else None,
            "note": entry.note if entry is not None else None,
            "kind": "numeric" if path is not None else "structure",
        }

    trio = document.timeframes.trio
    rows: list[dict[str, Any]] = [
        row("合約範圍", ", ".join(document.universe.contracts), None),
        row("Session", document.universe.session, None),
        row("TF trio", f"{trio.bias} · {trio.mid} · {trio.entry}", None),
        row("入市層數", str(document.timeframes.entry_layers), "timeframes.entry_layers"),
    ]
    for indicator in document.indicators:
        rows.append(
            row(
                f"{indicator.type} 週期（{indicator.id}）",
                str(indicator.period),
                f"indicators.{indicator.id}.period",
            )
        )
    for structure in document.structures:
        data = structure.as_mapping()
        if structure.type == "pullback_lifecycle":
            detail = f"layer {data.get('layer')} · touch {data.get('touch')}"
        elif structure.type == "signal_bar":
            detail = f"variant {data.get('variant')}"
        else:  # pragma: no cover — semantics layer rejects unknown types first
            detail = structure.type
        rows.append(row(f"結構 {structure.id}（{structure.type}）", detail, None))
    rows.extend(
        [
            row(
                "Regime 分離百分位",
                str(document.regime.sep_mult.value),
                "regime.sep_mult.value",
            ),
            row(
                "Regime 斜率百分位",
                str(document.regime.flat_mult.value),
                "regime.flat_mult.value",
            ),
            row("Regime 要求 Trend", str(document.regime.require_trend), None),
            row("Congestion 不交易", str(document.regime.congestion_no_trade), None),
            row("方向模式", document.direction.mode, None),
            row("層間方向一致性", document.direction.layer_consistency, None),
            row("入市觸發", document.entry.trigger.type, None),
            row("作廢規則", ", ".join(document.invalidations), None),
            row("止損錨點", document.risk.stop.anchor, None),
            row(
                "止損偏移（tick）",
                str(document.risk.stop.offset_ticks),
                "risk.stop.offset_ticks",
            ),
            row(
                f"目標（{document.risk.target.type}）",
                str(document.risk.target.value),
                "risk.target.value",
            ),
            row(
                f"每單風險 %（{document.risk.sizing.type}）",
                str(document.risk.sizing.risk_pct),
                "risk.sizing.risk_pct",
            ),
            row(
                "日虧損限額（R）",
                str(document.risk.daily_loss_limit_r),
                "risk.daily_loss_limit_r",
            ),
        ]
    )
    return rows


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
