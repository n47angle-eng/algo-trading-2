"""Immutable, byte-preserving filesystem store for ``insight.v1`` documents.

P2 tab ④ keeps insights as a pure notebook (design constraints #22/#23): they
are never linked to a strategy or a run, and the execution layer never queries
them at runtime.

Design decisions:

* identity is the composite ``origin + insight_id`` (docs/05 §3.5.1 v1.1) — two
  independent apps both mint ``insight-007``, so the origin is what tells them
  apart;
* versions are immutable: a change is a new ``version`` file, never an update,
  so there is no overwrite operation anywhere in this module;
* the exact YAML the terminal AI wrote is preserved verbatim as ``source_text``
  — re-emitting from a parsed object always drifts;
* a copy that is missing its earlier versions is still accepted and flagged,
  because the iron rule is "an insight version is immutable", not "this machine
  owns the whole version chain" (rejecting it would break cross-app copy).
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import ValidationError

from futures_research.data.contracts import ContractRegistry
from futures_research.insight.models import (
    InsightDocument,
    InsightIdentityError,
    parse_insight_id,
)
from futures_research.paths import PROJECT_ROOT
from futures_research.sketch.identity import SketchIdentityError, parse_sketch_origin
from futures_research.sketch.store import (
    SketchNotFoundError,
    SketchStore,
    SketchValidationError,
    default_sketch_store,
)

_RECORD_SCHEMA = "insight_version.v1"
_ARCHIVE_SCHEMA = "insight_delete_archive.v1"
_ARCHIVE_MANIFEST = "_archive.yaml"
_VERSION_FILE = re.compile(r"^v(?P<version>[1-9][0-9]*)\.yaml$")
_ARCHIVE_ID = re.compile(r"^delete-(?P<uuid>[0-9a-f]{32})$")
_CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARCHIVE_KEYS = {
    "schema",
    "archive_id",
    "origin",
    "insight_id",
    "deleted_at",
    "version_count",
    "versions",
}
_ARCHIVE_VERSION_KEYS = {
    "version",
    "filename",
    "file_sha256",
    "source_content_sha256",
}

# Serializes version publication across the API's worker threads.
_STORE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class InsightValidationIssue:
    """One actionable problem, in the docs/05 paste-back error format."""

    path: str
    message: str
    fix: str

    def format_line(self) -> str:
        return f"{self.path}: {self.message} — {self.fix}"


class InsightValidationError(ValueError):
    """Raised only after the whole document has been checked without writing it."""

    def __init__(self, issues: list[InsightValidationIssue]) -> None:
        if not issues:
            msg = "InsightValidationError requires at least one issue"
            raise ValueError(msg)
        self.issues = tuple(issues)
        super().__init__(self.format_report())

    def format_report(self) -> str:
        return "\n".join(issue.format_line() for issue in self.issues)


class InsightVersionCollisionError(RuntimeError):
    """Raised when a stored version exists with different content."""


class InsightConflictError(RuntimeError):
    """Raised when a safe archive/import/restore target is no longer exclusive."""


class InsightNotFoundError(LookupError):
    """Raised when a composite insight identity has no stored version."""


class InsightStorageError(RuntimeError):
    """Raised when insight storage cannot prove or publish its truth."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """Result of one import attempt on an already-validated document."""

    record: dict[str, Any]
    #: True when a byte-identical version already existed; nothing was written.
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class ArchiveOutcome:
    """Result of moving one complete active identity into recoverable storage."""

    origin: str
    insight_id: str
    archive_id: str
    deleted_at: str
    version_count: int


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    """Result of restoring one complete archive without changing its bytes."""

    origin: str
    insight_id: str
    archive_id: str
    restored_at: str
    version_count: int


@dataclass(frozen=True, slots=True)
class _StoredVersion:
    """One proven stored record plus the exact bytes that encode it."""

    version: int
    filename: str
    file_sha256: str
    source_content_sha256: str
    data: bytes
    record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Archive:
    """One fully validated recoverable archive."""

    origin: str
    insight_id: str
    archive_id: str
    deleted_at: str
    versions: tuple[_StoredVersion, ...]


@dataclass(frozen=True, slots=True)
class InsightStore:
    """Store each accepted version at ``data/insights/<origin>/<id>/v<n>.yaml``."""

    root: Path
    sketch_store: SketchStore | None = None
    contracts_config: Path | None = None

    # --- writes ------------------------------------------------------------

    def import_document(self, source_text: str) -> ImportOutcome:
        """Validate a whole ``insight.v1`` document, then publish it immutably."""
        document = self.validate_document(source_text)
        digest = content_digest(source_text)
        version_path = self._version_path(
            document.origin,
            document.insight_id,
            document.version,
        )

        with _STORE_LOCK:
            archives = self._archives_for_identity(
                document.origin,
                document.insight_id,
            )
            if archives:
                if self._insight_directory(
                    document.origin,
                    document.insight_id,
                ).exists():
                    msg = "active and archived insight states both exist"
                    raise InsightStorageError(msg)
                msg = (
                    "insight identity is archived; restore it before importing: "
                    f"({document.origin}, {document.insight_id})"
                )
                raise InsightConflictError(msg)
            if version_path.is_file():
                existing = self._read_version(
                    version_path,
                    origin=document.origin,
                    insight_id=document.insight_id,
                    version=document.version,
                )
                if existing.get("source_text") == source_text:
                    return ImportOutcome(record=existing, deduplicated=True)
                msg = (
                    "insight version already exists with different content: "
                    f"({document.origin}, {document.insight_id}, v{document.version})"
                )
                raise InsightVersionCollisionError(msg)

            record = _build_record(
                document=document,
                source_text=source_text,
                digest=digest,
                imported_from_origin=(
                    document.origin
                    if self._has_missing_prior_versions(document)
                    else None
                ),
            )
            self._publish(version_path, record)

        return ImportOutcome(record=record, deduplicated=False)

    def archive(self, origin: str, insight_id: str) -> ArchiveOutcome:
        """Archive every version of one active identity without changing bytes."""
        canonical_origin, canonical_id = _parse_public_identity(origin, insight_id)
        with _STORE_LOCK:
            active = self._insight_directory(canonical_origin, canonical_id)
            existing_archives = self._archives_for_identity(
                canonical_origin,
                canonical_id,
            )
            if existing_archives:
                if active.exists():
                    msg = "active and archived insight states both exist"
                    raise InsightStorageError(msg)
                msg = (
                    "archive target already exists; active and archive storage "
                    "were not overwritten"
                )
                raise InsightConflictError(msg)

            versions = self._capture_active_directory(
                active,
                origin=canonical_origin,
                insight_id=canonical_id,
                missing_is_not_found=True,
            )
            archive_id = self._unused_archive_id()
            deleted_at = _canonical_now()
            manifest = _archive_manifest(
                origin=canonical_origin,
                insight_id=canonical_id,
                archive_id=archive_id,
                deleted_at=deleted_at,
                versions=versions,
            )
            parent = self._archive_identity_directory(canonical_origin, canonical_id)
            created = self._create_directory_chain(parent)
            target = parent / archive_id
            temporary = parent / (
                f".{archive_id}.{os.getpid()}.{uuid4().hex}.tmp"
            )
            published = False
            active_removed = False
            try:
                temporary.mkdir()
                self._materialize_archive(temporary, manifest, versions)
                self._read_archive_directory(
                    temporary,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    archive_id=archive_id,
                )
                if target.exists():
                    raise FileExistsError(target)
                self._publish_archive_directory(temporary, target)
                published = True
                self._read_archive_directory(
                    target,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    archive_id=archive_id,
                )
                self._remove_active_directory(active)
                active_removed = True
                proven = self._read_archive_directory(
                    target,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    archive_id=archive_id,
                )
                if active.exists() or not _same_version_bytes(proven.versions, versions):
                    msg = "archive post-verification could not prove the final state"
                    raise InsightStorageError(msg)
            except FileExistsError as exc:
                msg = (
                    "archive target changed during publication; active and archive "
                    "storage were not overwritten"
                )
                raise InsightConflictError(msg) from exc
            except (InsightConflictError, InsightNotFoundError, InsightStorageError):
                raise
            except OSError as exc:
                msg = (
                    "archive operation failed; no success was reported and storage "
                    "state must be re-read"
                )
                raise InsightStorageError(msg) from exc
            finally:
                if temporary.exists():
                    with suppress(OSError):
                        shutil.rmtree(temporary)
                if published and not active_removed and self._active_matches(
                    active,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    versions=versions,
                ):
                    with suppress(OSError):
                        self._rollback_archive_directory(target)
                    published = target.exists()
                if not published:
                    self._remove_empty_directories(created)

            return ArchiveOutcome(
                origin=canonical_origin,
                insight_id=canonical_id,
                archive_id=archive_id,
                deleted_at=deleted_at,
                version_count=len(versions),
            )

    def list_archives(self) -> list[dict[str, Any]]:
        """Return every fully proven archive, newest deletion first."""
        with _STORE_LOCK:
            archives = self._all_archives()
            items = [
                {
                    "archive_id": archive.archive_id,
                    "origin": archive.origin,
                    "insight_id": archive.insight_id,
                    "deleted_at": archive.deleted_at,
                    "version_count": len(archive.versions),
                }
                for archive in archives
            ]
        items.sort(
            key=lambda item: (
                str(item["origin"]),
                str(item["insight_id"]),
                str(item["archive_id"]),
            )
        )
        items.sort(key=lambda item: str(item["deleted_at"]), reverse=True)
        return items

    def restore(
        self,
        origin: str,
        insight_id: str,
        archive_id: str,
    ) -> RestoreOutcome:
        """Restore one proven archive if its active identity is wholly absent."""
        canonical_origin, canonical_id = _parse_public_identity(origin, insight_id)
        canonical_archive_id = _parse_archive_id(archive_id)
        with _STORE_LOCK:
            archives = self._archives_for_identity(canonical_origin, canonical_id)
            matches = [
                archive
                for archive in archives
                if archive.archive_id == canonical_archive_id
            ]
            if not matches:
                msg = (
                    "unknown insight archive; active and archive storage are unchanged"
                )
                raise InsightNotFoundError(msg)
            archive = matches[0]

            active = self._insight_directory(canonical_origin, canonical_id)
            if active.exists():
                msg = (
                    "active insight already exists; active and archive storage are "
                    "unchanged and nothing was overwritten"
                )
                raise InsightConflictError(msg)

            parent = active.parent
            created = self._create_directory_chain(parent)
            temporary = parent / (
                f".{canonical_id}.restore.{os.getpid()}.{uuid4().hex}.tmp"
            )
            published = False
            archive_consumed = False
            archive_path = self._archive_path(
                canonical_origin,
                canonical_id,
                canonical_archive_id,
            )
            try:
                temporary.mkdir()
                self._materialize_restore(temporary, archive.versions)
                restored_temp = self._capture_active_directory(
                    temporary,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    missing_is_not_found=False,
                )
                if not _same_version_bytes(restored_temp, archive.versions):
                    msg = "restore temp bytes do not match the proven archive"
                    raise InsightStorageError(msg)
                if active.exists():
                    raise FileExistsError(active)
                self._publish_restore_directory(temporary, active)
                published = True
                restored = self._capture_active_directory(
                    active,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    missing_is_not_found=False,
                )
                if not _same_version_bytes(restored, archive.versions):
                    msg = "restored active bytes do not match the proven archive"
                    raise InsightStorageError(msg)
                self._consume_archive_directory(archive_path)
                archive_consumed = True
                final = self._capture_active_directory(
                    active,
                    origin=canonical_origin,
                    insight_id=canonical_id,
                    missing_is_not_found=False,
                )
                if archive_path.exists() or not _same_version_bytes(
                    final,
                    archive.versions,
                ):
                    msg = "restore post-verification could not prove the final state"
                    raise InsightStorageError(msg)
            except FileExistsError as exc:
                msg = (
                    "active restore target changed during publication; active and "
                    "archive storage were not overwritten"
                )
                raise InsightConflictError(msg) from exc
            except (InsightConflictError, InsightNotFoundError, InsightStorageError):
                raise
            except OSError as exc:
                msg = (
                    "restore operation failed; no success was reported and storage "
                    "state must be re-read"
                )
                raise InsightStorageError(msg) from exc
            finally:
                if temporary.exists():
                    with suppress(OSError):
                        shutil.rmtree(temporary)
                if (
                    published
                    and not archive_consumed
                    and self._archive_matches(archive_path, archive)
                ):
                    with suppress(OSError):
                        self._rollback_restored_directory(active)
                    published = active.exists()
                if archive_consumed:
                    self._remove_empty_archive_parents(
                        canonical_origin,
                        canonical_id,
                    )
                if not published:
                    self._remove_empty_directories(created)

            return RestoreOutcome(
                origin=canonical_origin,
                insight_id=canonical_id,
                archive_id=canonical_archive_id,
                restored_at=_canonical_now(),
                version_count=len(archive.versions),
            )

    # --- reads -------------------------------------------------------------

    def list_insights(self) -> list[dict[str, Any]]:
        """Return one summary per composite identity, newest import first."""
        with _STORE_LOCK:
            summaries: list[dict[str, Any]] = []
            for origin, insight_id in self._identities():
                if self._archives_for_identity(origin, insight_id):
                    msg = "active and archived insight states both exist"
                    raise InsightStorageError(msg)
                versions = self._read_all_versions(origin, insight_id)
                if not versions:
                    continue
                latest = versions[-1]
                summary = {
                    key: value for key, value in latest.items() if key != "source_text"
                }
                summary["versions"] = [int(record["version"]) for record in versions]
                summary["latest_version"] = int(latest["version"])
                summaries.append(summary)
        summaries.sort(
            key=lambda summary: (
                str(summary["imported_at"]),
                str(summary["origin"]),
                str(summary["insight_id"]),
            ),
            reverse=True,
        )
        return summaries

    def get(self, origin: str, insight_id: str) -> dict[str, Any]:
        """Return every stored version of one composite identity, oldest first."""
        try:
            canonical_origin = parse_sketch_origin(origin)
            canonical_id = parse_insight_id(insight_id)
        except (SketchIdentityError, InsightIdentityError) as exc:
            raise InsightNotFoundError(str(exc)) from exc
        with _STORE_LOCK:
            archives = self._archives_for_identity(canonical_origin, canonical_id)
            active = self._insight_directory(canonical_origin, canonical_id)
            if archives and active.exists():
                msg = "active and archived insight states both exist"
                raise InsightStorageError(msg)
            versions = self._read_all_versions(canonical_origin, canonical_id)
        if not versions:
            msg = f"unknown insight identity: ({canonical_origin}, {canonical_id})"
            raise InsightNotFoundError(msg)
        return {
            "schema": "insight_detail.v1",
            "origin": canonical_origin,
            "insight_id": canonical_id,
            "latest_version": int(versions[-1]["version"]),
            "version_count": len(versions),
            "versions": versions,
        }

    # --- validation --------------------------------------------------------

    def validate_document(self, source_text: str) -> InsightDocument:
        """Run every layer before any write; the caller renders issues verbatim."""
        try:
            raw = yaml.safe_load(source_text)
        except yaml.YAMLError as exc:
            raise InsightValidationError(
                [
                    InsightValidationIssue(
                        path="(root)",
                        message=f"YAML syntax error ({exc})",
                        fix="fix the document so it parses as an insight.v1 mapping",
                    )
                ]
            ) from exc
        if not isinstance(raw, dict):
            raise InsightValidationError(
                [
                    InsightValidationIssue(
                        path="(root)",
                        message="document must be a YAML mapping",
                        fix="start the document with schema: insight.v1",
                    )
                ]
            )
        try:
            document = InsightDocument.model_validate(raw)
        except ValidationError as exc:
            raise InsightValidationError(_pydantic_issues(exc)) from exc

        issues = _catalog_identity_issues(document, self._registry())
        issues.extend(
            _local_sketch_identity_issues(
                document,
                sketch_store=self.sketch_store or default_sketch_store(),
            )
        )
        if issues:
            raise InsightValidationError(issues)
        return document

    # --- internals ---------------------------------------------------------

    def _registry(self) -> ContractRegistry:
        try:
            return ContractRegistry.from_yaml(
                self.contracts_config or (PROJECT_ROOT / "config" / "contracts.yaml")
            )
        except (OSError, ValueError) as exc:
            msg = "canonical contract catalog is unavailable"
            raise InsightStorageError(msg) from exc

    def _insight_directory(self, origin: str, insight_id: str) -> Path:
        return self.root / origin / insight_id

    def _version_path(self, origin: str, insight_id: str, version: int) -> Path:
        return self._insight_directory(origin, insight_id) / f"v{version}.yaml"

    def _archive_root(self) -> Path:
        return self.root / "_deleted"

    def _archive_identity_directory(self, origin: str, insight_id: str) -> Path:
        return self._archive_root() / origin / insight_id

    def _archive_path(
        self,
        origin: str,
        insight_id: str,
        archive_id: str,
    ) -> Path:
        return self._archive_identity_directory(origin, insight_id) / archive_id

    def _archives_for_identity(self, origin: str, insight_id: str) -> list[_Archive]:
        directory = self._archive_identity_directory(origin, insight_id)
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.is_dir():
            msg = "insight archive identity location is not a directory"
            raise InsightStorageError(msg)
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            msg = "insight archive identity could not be listed"
            raise InsightStorageError(msg) from exc
        paths: list[Path] = []
        for path in entries:
            if path.name.startswith("."):
                continue
            if (
                path.is_symlink()
                or not path.is_dir()
                or not _archive_id_is_valid(path.name)
            ):
                msg = "insight archive identity contains a malformed entry"
                raise InsightStorageError(msg)
            paths.append(path)
        if len(paths) > 1:
            msg = "insight identity has more than one unresolved archive"
            raise InsightStorageError(msg)
        return [
            self._read_archive_directory(
                path,
                origin=origin,
                insight_id=insight_id,
                archive_id=path.name,
            )
            for path in paths
        ]

    def _all_archives(self) -> list[_Archive]:
        archive_root = self._archive_root()
        if not archive_root.exists():
            return []
        if archive_root.is_symlink() or not archive_root.is_dir():
            msg = "insight archive root is not a directory"
            raise InsightStorageError(msg)
        try:
            origin_paths = sorted(archive_root.iterdir())
        except OSError as exc:
            msg = "insight archive root could not be listed"
            raise InsightStorageError(msg) from exc
        archives: list[_Archive] = []
        for origin_path in origin_paths:
            if origin_path.name.startswith("."):
                continue
            if origin_path.is_symlink() or not origin_path.is_dir():
                msg = "insight archive root contains a malformed origin"
                raise InsightStorageError(msg)
            try:
                origin = parse_sketch_origin(origin_path.name)
            except SketchIdentityError as exc:
                msg = "insight archive root contains a malformed origin"
                raise InsightStorageError(msg) from exc
            try:
                identity_paths = sorted(origin_path.iterdir())
            except OSError as exc:
                msg = "insight archive origin could not be listed"
                raise InsightStorageError(msg) from exc
            for identity_path in identity_paths:
                if identity_path.name.startswith("."):
                    continue
                if identity_path.is_symlink() or not identity_path.is_dir():
                    msg = "insight archive origin contains a malformed identity"
                    raise InsightStorageError(msg)
                try:
                    insight_id = parse_insight_id(identity_path.name)
                except InsightIdentityError as exc:
                    msg = "insight archive origin contains a malformed identity"
                    raise InsightStorageError(msg) from exc
                identity_archives = self._archives_for_identity(origin, insight_id)
                if identity_archives and self._insight_directory(
                    origin,
                    insight_id,
                ).exists():
                    msg = "active and archived insight states both exist"
                    raise InsightStorageError(msg)
                archives.extend(identity_archives)
        return archives

    def _unused_archive_id(self) -> str:
        existing = {archive.archive_id for archive in self._all_archives()}
        for _attempt in range(8):
            candidate = self._new_archive_id()
            if candidate not in existing:
                return candidate
        msg = "could not allocate a unique insight archive id"
        raise InsightStorageError(msg)

    @staticmethod
    def _new_archive_id() -> str:
        return f"delete-{uuid4().hex}"

    def _capture_active_directory(
        self,
        directory: Path,
        *,
        origin: str,
        insight_id: str,
        missing_is_not_found: bool,
    ) -> tuple[_StoredVersion, ...]:
        if not directory.exists():
            if missing_is_not_found:
                msg = (
                    "unknown active insight; active and archive storage are unchanged"
                )
                raise InsightNotFoundError(msg)
            msg = "active insight directory disappeared during verification"
            raise InsightStorageError(msg)
        if directory.is_symlink() or not directory.is_dir():
            msg = "active insight storage location is not a directory"
            raise InsightStorageError(msg)
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            msg = "active insight versions could not be listed"
            raise InsightStorageError(msg) from exc
        version_paths: list[tuple[int, Path]] = []
        for path in entries:
            match = _VERSION_FILE.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or match is None:
                msg = "active insight directory contains an unprovable entry"
                raise InsightStorageError(msg)
            version_paths.append((int(match.group("version")), path))
        if not version_paths:
            msg = "active insight identity has no provable version"
            raise InsightStorageError(msg)
        version_paths.sort(key=lambda item: item[0])
        return tuple(
            self._read_stored_version(
                path,
                origin=origin,
                insight_id=insight_id,
                version=version,
            )
            for version, path in version_paths
        )

    @staticmethod
    def _read_stored_version(
        path: Path,
        *,
        origin: str,
        insight_id: str,
        version: int,
    ) -> _StoredVersion:
        try:
            data = path.read_bytes()
        except OSError as exc:
            msg = f"stored insight version is unreadable: {origin}/{insight_id}/v{version}"
            raise InsightStorageError(msg) from exc
        record = InsightStore._decode_stored_version(
            data,
            origin=origin,
            insight_id=insight_id,
            version=version,
        )
        source_digest = record["content_sha256"]
        if not isinstance(source_digest, str):
            msg = "stored insight source digest has an invalid type"
            raise InsightStorageError(msg)
        return _StoredVersion(
            version=version,
            filename=f"v{version}.yaml",
            file_sha256=sha256(data).hexdigest(),
            source_content_sha256=source_digest,
            data=data,
            record=record,
        )

    @staticmethod
    def _read_archive_directory(
        directory: Path,
        *,
        origin: str,
        insight_id: str,
        archive_id: str,
    ) -> _Archive:
        if (
            not directory.exists()
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            msg = "insight archive directory is missing or invalid"
            raise InsightStorageError(msg)
        manifest_path = directory / _ARCHIVE_MANIFEST
        try:
            raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            entries = sorted(directory.iterdir())
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            msg = "insight archive manifest is unreadable"
            raise InsightStorageError(msg) from exc
        if not isinstance(raw_manifest, dict) or set(raw_manifest) != _ARCHIVE_KEYS:
            msg = "insight archive manifest keys are not exact"
            raise InsightStorageError(msg)
        if (
            raw_manifest.get("schema") != _ARCHIVE_SCHEMA
            or raw_manifest.get("archive_id") != archive_id
            or raw_manifest.get("origin") != origin
            or raw_manifest.get("insight_id") != insight_id
            or not _archive_id_is_valid(archive_id)
        ):
            msg = "insight archive manifest identity does not match its storage key"
            raise InsightStorageError(msg)
        deleted_at = raw_manifest.get("deleted_at")
        if not isinstance(deleted_at, str) or not _is_canonical_utc(deleted_at):
            msg = "insight archive deletion time is not canonical UTC"
            raise InsightStorageError(msg)
        version_count = raw_manifest.get("version_count")
        raw_versions = raw_manifest.get("versions")
        if (
            type(version_count) is not int
            or version_count < 1
            or not isinstance(raw_versions, list)
            or version_count != len(raw_versions)
        ):
            msg = "insight archive version count is invalid"
            raise InsightStorageError(msg)

        expected_names = {_ARCHIVE_MANIFEST}
        versions: list[_StoredVersion] = []
        prior_version = 0
        for raw_version in raw_versions:
            if (
                not isinstance(raw_version, dict)
                or set(raw_version) != _ARCHIVE_VERSION_KEYS
            ):
                msg = "insight archive version manifest keys are not exact"
                raise InsightStorageError(msg)
            version = raw_version.get("version")
            if type(version) is not int or version <= prior_version:
                msg = "insight archive versions are not in numeric ascending order"
                raise InsightStorageError(msg)
            prior_version = version
            filename = raw_version.get("filename")
            file_digest = raw_version.get("file_sha256")
            source_digest = raw_version.get("source_content_sha256")
            if (
                filename != f"v{version}.yaml"
                or not isinstance(file_digest, str)
                or _SHA256.fullmatch(file_digest) is None
                or not isinstance(source_digest, str)
                or _SHA256.fullmatch(source_digest) is None
            ):
                msg = "insight archive version metadata is not canonical"
                raise InsightStorageError(msg)
            expected_names.add(filename)
            stored = InsightStore._read_stored_version(
                directory / filename,
                origin=origin,
                insight_id=insight_id,
                version=version,
            )
            if (
                stored.file_sha256 != file_digest
                or stored.source_content_sha256 != source_digest
            ):
                msg = "insight archive version digest verification failed"
                raise InsightStorageError(msg)
            versions.append(stored)

        actual_names: set[str] = set()
        for entry in entries:
            if entry.is_symlink() or not entry.is_file():
                msg = "insight archive contains an unprovable entry"
                raise InsightStorageError(msg)
            actual_names.add(entry.name)
        if actual_names != expected_names:
            msg = "insight archive files do not exactly match its manifest"
            raise InsightStorageError(msg)
        return _Archive(
            origin=origin,
            insight_id=insight_id,
            archive_id=archive_id,
            deleted_at=deleted_at,
            versions=tuple(versions),
        )

    @staticmethod
    def _materialize_archive(
        directory: Path,
        manifest: dict[str, Any],
        versions: tuple[_StoredVersion, ...],
    ) -> None:
        for version in versions:
            InsightStore._write_bytes_exclusive(
                directory / version.filename,
                version.data,
            )
        text = yaml.safe_dump(
            manifest,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        if yaml.safe_load(text) != manifest:
            msg = "insight archive manifest serialization changed its meaning"
            raise InsightStorageError(msg)
        InsightStore._write_bytes_exclusive(
            directory / _ARCHIVE_MANIFEST,
            text.encode("utf-8"),
        )

    @staticmethod
    def _materialize_restore(
        directory: Path,
        versions: tuple[_StoredVersion, ...],
    ) -> None:
        for version in versions:
            InsightStore._write_bytes_exclusive(
                directory / version.filename,
                version.data,
            )

    @staticmethod
    def _write_bytes_exclusive(path: Path, data: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _publish_archive_directory(temporary: Path, target: Path) -> None:
        """Publish one complete hidden directory without intentionally replacing."""
        os.rename(temporary, target)

    @staticmethod
    def _remove_active_directory(active: Path) -> None:
        shutil.rmtree(active)

    @staticmethod
    def _rollback_archive_directory(archive: Path) -> None:
        shutil.rmtree(archive)

    @staticmethod
    def _publish_restore_directory(temporary: Path, active: Path) -> None:
        """Publish one complete active directory without intentionally replacing."""
        os.rename(temporary, active)

    @staticmethod
    def _consume_archive_directory(archive: Path) -> None:
        shutil.rmtree(archive)

    @staticmethod
    def _rollback_restored_directory(active: Path) -> None:
        shutil.rmtree(active)

    def _active_matches(
        self,
        directory: Path,
        *,
        origin: str,
        insight_id: str,
        versions: tuple[_StoredVersion, ...],
    ) -> bool:
        try:
            current = self._capture_active_directory(
                directory,
                origin=origin,
                insight_id=insight_id,
                missing_is_not_found=False,
            )
        except (InsightNotFoundError, InsightStorageError, OSError):
            return False
        return _same_version_bytes(current, versions)

    def _archive_matches(self, directory: Path, archive: _Archive) -> bool:
        try:
            current = self._read_archive_directory(
                directory,
                origin=archive.origin,
                insight_id=archive.insight_id,
                archive_id=archive.archive_id,
            )
        except (InsightStorageError, OSError):
            return False
        return _same_version_bytes(current.versions, archive.versions)

    @staticmethod
    def _create_directory_chain(target: Path) -> list[Path]:
        missing: list[Path] = []
        candidate = target
        while not candidate.exists():
            missing.append(candidate)
            if candidate.parent == candidate:
                msg = "insight storage has no existing parent directory"
                raise InsightStorageError(msg)
            candidate = candidate.parent
        if candidate.is_symlink() or not candidate.is_dir():
            msg = "insight storage parent is not a directory"
            raise InsightStorageError(msg)
        created: list[Path] = []
        try:
            for directory in reversed(missing):
                directory.mkdir()
                created.append(directory)
        except OSError as exc:
            InsightStore._remove_empty_directories(created)
            msg = "insight storage directory could not be created"
            raise InsightStorageError(msg) from exc
        return created

    @staticmethod
    def _remove_empty_directories(directories: list[Path]) -> None:
        for directory in reversed(directories):
            with suppress(OSError):
                directory.rmdir()

    def _remove_empty_archive_parents(self, origin: str, insight_id: str) -> None:
        for directory in (
            self._archive_identity_directory(origin, insight_id),
            self._archive_root() / origin,
            self._archive_root(),
        ):
            with suppress(OSError):
                directory.rmdir()

    def _identities(self) -> list[tuple[str, str]]:
        if not self.root.is_dir():
            return []
        identities: list[tuple[str, str]] = []
        try:
            origin_folders = sorted(self.root.iterdir())
        except OSError as exc:
            msg = "insight repository could not be listed"
            raise InsightStorageError(msg) from exc
        for origin_folder in origin_folders:
            if not origin_folder.is_dir():
                continue
            if origin_folder.name == "_deleted":
                continue
            try:
                origin = parse_sketch_origin(origin_folder.name)
            except SketchIdentityError:
                continue
            try:
                insight_folders = sorted(origin_folder.iterdir())
            except OSError as exc:
                msg = "insight repository could not be listed"
                raise InsightStorageError(msg) from exc
            for folder in insight_folders:
                if not folder.is_dir():
                    continue
                try:
                    insight_id = parse_insight_id(folder.name)
                except InsightIdentityError:
                    continue
                identities.append((origin, insight_id))
        return identities

    def _stored_versions(self, origin: str, insight_id: str) -> list[int]:
        directory = self._insight_directory(origin, insight_id)
        if not directory.is_dir():
            return []
        versions: list[int] = []
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            msg = f"insight versions could not be listed: ({origin}, {insight_id})"
            raise InsightStorageError(msg) from exc
        for path in entries:
            if not path.is_file():
                continue
            match = _VERSION_FILE.fullmatch(path.name)
            if match is None:
                continue
            versions.append(int(match.group("version")))
        return sorted(versions)

    def _read_all_versions(self, origin: str, insight_id: str) -> list[dict[str, Any]]:
        return [
            self._read_version(
                self._version_path(origin, insight_id, version),
                origin=origin,
                insight_id=insight_id,
                version=version,
            )
            for version in self._stored_versions(origin, insight_id)
        ]

    def _has_missing_prior_versions(self, document: InsightDocument) -> bool:
        """Flag a copy whose earlier versions are not on this machine."""
        if document.version == 1:
            return False
        stored = set(self._stored_versions(document.origin, document.insight_id))
        return any(version not in stored for version in range(1, document.version))

    @staticmethod
    def _read_version(
        path: Path,
        *,
        origin: str,
        insight_id: str,
        version: int,
    ) -> dict[str, Any]:
        """Read one stored version and prove it against its own storage key."""
        return InsightStore._read_stored_version(
            path,
            origin=origin,
            insight_id=insight_id,
            version=version,
        ).record

    @staticmethod
    def _decode_stored_version(
        data: bytes,
        *,
        origin: str,
        insight_id: str,
        version: int,
    ) -> dict[str, Any]:
        """Decode and prove stored bytes against their expected identity key."""
        try:
            payload = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            msg = f"stored insight version is unreadable: {origin}/{insight_id}/v{version}"
            raise InsightStorageError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"stored insight version is not a mapping: {origin}/{insight_id}/v{version}"
            raise InsightStorageError(msg)
        if (
            payload.get("schema") != _RECORD_SCHEMA
            or payload.get("origin") != origin
            or payload.get("insight_id") != insight_id
            or payload.get("version") != version
        ):
            msg = (
                "stored insight version identity does not match its storage key: "
                f"{origin}/{insight_id}/v{version}"
            )
            raise InsightStorageError(msg)
        source_text = payload.get("source_text")
        digest = payload.get("content_sha256")
        if not isinstance(source_text, str) or not source_text:
            msg = f"stored insight version has no source_text: {origin}/{insight_id}/v{version}"
            raise InsightStorageError(msg)
        if not isinstance(digest, str) or digest != content_digest(source_text):
            msg = (
                "stored insight version digest does not match its source_text: "
                f"{origin}/{insight_id}/v{version}"
            )
            raise InsightStorageError(msg)
        return payload

    def _publish(self, path: Path, record: dict[str, Any]) -> None:
        """Materialize the complete version, then link it into place without overwrite."""
        text = yaml.safe_dump(
            record,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        if yaml.safe_load(text) != record:
            msg = "insight serialization did not preserve the imported source text"
            raise InsightStorageError(msg)

        directory = path.parent
        created: list[Path] = []
        temporary: Path | None = None
        try:
            for candidate in (self.root, directory.parent, directory):
                if not candidate.exists():
                    candidate.mkdir()
                    created.append(candidate)
                elif not candidate.is_dir():
                    msg = "insight storage location is not a directory"
                    raise InsightStorageError(msg)
            temporary = directory / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
            with temporary.open("xb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                self._link_version(temporary, path)
            except FileExistsError as exc:
                msg = f"insight version already exists: {path.name}"
                raise InsightVersionCollisionError(msg) from exc
            temporary.unlink()
            temporary = None
            created.clear()
        except InsightStorageError:
            raise
        except OSError as exc:
            msg = "insight version could not be published atomically"
            raise InsightStorageError(msg) from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
            for candidate in reversed(created):
                with suppress(OSError):
                    candidate.rmdir()

    @staticmethod
    def _link_version(temporary: Path, target: Path) -> None:
        """Publish atomically; an existing target is never replaced or merged."""
        os.link(temporary, target)


def _parse_public_identity(origin: str, insight_id: str) -> tuple[str, str]:
    try:
        return parse_sketch_origin(origin), parse_insight_id(insight_id)
    except (SketchIdentityError, InsightIdentityError) as exc:
        msg = "invalid insight path; active and archive storage are unchanged"
        raise InsightNotFoundError(msg) from exc


def _parse_archive_id(archive_id: str) -> str:
    if not _archive_id_is_valid(archive_id):
        msg = "invalid archive path; active and archive storage are unchanged"
        raise InsightNotFoundError(msg)
    return archive_id


def _archive_id_is_valid(archive_id: object) -> bool:
    if not isinstance(archive_id, str):
        return False
    match = _ARCHIVE_ID.fullmatch(archive_id)
    if match is None:
        return False
    hexadecimal = match.group("uuid")
    return hexadecimal[12] == "4" and hexadecimal[16] in "89ab"


def _is_canonical_utc(value: str) -> bool:
    if _CANONICAL_UTC.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    return True


def _archive_manifest(
    *,
    origin: str,
    insight_id: str,
    archive_id: str,
    deleted_at: str,
    versions: tuple[_StoredVersion, ...],
) -> dict[str, Any]:
    return {
        "schema": _ARCHIVE_SCHEMA,
        "archive_id": archive_id,
        "origin": origin,
        "insight_id": insight_id,
        "deleted_at": deleted_at,
        "version_count": len(versions),
        "versions": [
            {
                "version": version.version,
                "filename": version.filename,
                "file_sha256": version.file_sha256,
                "source_content_sha256": version.source_content_sha256,
            }
            for version in versions
        ],
    }


def _same_version_bytes(
    left: tuple[_StoredVersion, ...],
    right: tuple[_StoredVersion, ...],
) -> bool:
    if len(left) != len(right):
        return False
    return all(
        (
            left_version.version == right_version.version
            and left_version.filename == right_version.filename
            and left_version.file_sha256 == right_version.file_sha256
            and left_version.source_content_sha256
            == right_version.source_content_sha256
            and left_version.data == right_version.data
        )
        for left_version, right_version in zip(left, right, strict=True)
    )


def default_insight_store() -> InsightStore:
    """Store rooted at ``data/insights`` alongside the other local artifacts."""
    return InsightStore(root=PROJECT_ROOT / "data" / "insights")


def content_digest(source_text: str) -> str:
    """SHA-256 of the exact YAML bytes the terminal AI wrote."""
    return sha256(source_text.encode("utf-8")).hexdigest()


def _build_record(
    *,
    document: InsightDocument,
    source_text: str,
    digest: str,
    imported_from_origin: str | None,
) -> dict[str, Any]:
    return {
        "schema": _RECORD_SCHEMA,
        "insight_id": document.insight_id,
        "origin": document.origin,
        "version": document.version,
        "title": document.title,
        "instrument": document.instrument,
        "asset_class": document.asset_class,
        "tag": document.measurement.tag,
        "validation_status": document.validation_status,
        "based_on_sketch": document.based_on_sketch,
        "based_on_sketch_origin": document.based_on_sketch_origin,
        # Set only when this machine is missing an earlier version of the same
        # insight: the copy came from the other app rather than from here.
        "imported_from_origin": imported_from_origin,
        "content_sha256": digest,
        "imported_at": _now_iso(),
        # Verbatim YAML: never re-emitted from the parsed model.
        "source_text": source_text,
    }


def _catalog_identity_issues(
    document: InsightDocument,
    registry: ContractRegistry,
) -> list[InsightValidationIssue]:
    """Require the declared instrument identity to match the canonical catalog."""
    try:
        contract = registry.by_symbol(document.instrument)
    except KeyError:
        return [
            InsightValidationIssue(
                path="instrument",
                message=f"instrument {document.instrument!r} is not in config/contracts.yaml",
                fix="use one configured root symbol without changing its case or whitespace",
            )
        ]
    if document.asset_class != contract.asset_class:
        return [
            InsightValidationIssue(
                path="asset_class",
                message=(
                    f"asset_class {document.asset_class!r} does not match catalog class "
                    f"{contract.asset_class!r} for {document.instrument}"
                ),
                fix="copy the instrument's exact asset_class from config/contracts.yaml",
            )
        ]
    return []


def _local_sketch_identity_issues(
    document: InsightDocument,
    *,
    sketch_store: SketchStore,
) -> list[InsightValidationIssue]:
    """Compare against a local sketch when there is one; absence stays valid.

    A complete composite lineage may point at another app or machine, so a
    missing package is not an error (docs/05 §1.1a).  A package that exists but
    cannot prove its own catalog identity is not equivalent to absence.
    """
    try:
        sketch = sketch_store.get(document.based_on_sketch_origin, document.based_on_sketch)
    except SketchNotFoundError:
        return []
    except SketchValidationError:
        return [
            InsightValidationIssue(
                path="based_on_sketch",
                message="local sketch package is incomplete or invalid for identity checks",
                fix="duplicate it as a new sketch.v1 package with instrument and asset_class",
            )
        ]

    meta = sketch.meta
    if meta.instrument is None or meta.asset_class is None:
        return [
            InsightValidationIssue(
                path="based_on_sketch",
                message="local legacy sketch lacks instrument and/or asset_class",
                fix="duplicate it as a new sketch.v1 package with complete catalog identity",
            )
        ]

    issues: list[InsightValidationIssue] = []
    if meta.instrument != document.instrument:
        issues.append(
            InsightValidationIssue(
                path="instrument",
                message=(
                    f"local sketch instrument {meta.instrument!r} does not match insight "
                    f"instrument {document.instrument!r}"
                ),
                fix="set instrument to the referenced sketch instrument, or cite the right sketch",
            )
        )
    if meta.asset_class != document.asset_class:
        issues.append(
            InsightValidationIssue(
                path="asset_class",
                message=(
                    f"local sketch asset_class {meta.asset_class!r} does not match insight "
                    f"asset_class {document.asset_class!r}"
                ),
                fix="make the insight match the referenced sketch catalog identity",
            )
        )
    return issues


def _pydantic_issues(exc: ValidationError) -> list[InsightValidationIssue]:
    issues: list[InsightValidationIssue] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        path = ".".join("schema" if item == "schema_name" else str(item) for item in loc)
        issues.append(
            InsightValidationIssue(
                path=path or "(root)",
                message=str(error.get("msg") or "invalid value"),
                fix="correct the field to match insight.v1 (docs/05 §3.5.1)",
            )
        )
    return issues


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
