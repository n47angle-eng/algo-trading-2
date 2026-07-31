"""Atomic, byte-preserving filesystem store for ``sketch.v1`` ZIP packages."""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import yaml
from pydantic import ValidationError

from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT
from futures_research.sketch.identity import (
    SketchIdentityError,
    SketchOrigin,
    parse_sketch_id,
    parse_sketch_origin,
)
from futures_research.sketch.models import EXPECTED_CHART_FILES, SketchMeta

EXPECTED_PACKAGE_FILES = frozenset(
    {
        "meta.yaml",
        "INSTRUCTIONS.md",
        *EXPECTED_CHART_FILES,
    }
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_STORE_LOCK = threading.Lock()
_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SketchValidationIssue:
    """One actionable problem in the package or its metadata."""

    path: str
    message: str
    fix: str

    def format_line(self) -> str:
        return f"{self.path}: {self.message} — {self.fix}"


class SketchValidationError(ValueError):
    """Raised only after the complete package has been checked without writing it."""

    def __init__(self, issues: list[SketchValidationIssue]) -> None:
        super().__init__("\n".join(issue.format_line() for issue in issues))
        self.issues = tuple(issues)

    def format_report(self) -> str:
        return "\n".join(issue.format_line() for issue in self.issues)


class SketchAlreadyExistsError(FileExistsError):
    """Raised when a sketch id already has a target directory."""


class SketchNotFoundError(LookupError):
    """Raised when a sketch id or one of its canonical images does not exist."""


@dataclass(frozen=True, slots=True)
class StoredSketch:
    """Validated stored package plus verbatim text members."""

    folder: Path
    meta: SketchMeta
    meta_yaml: str
    instructions_markdown: str

    def summary(self) -> dict[str, Any]:
        return {
            "sketch_id": self.meta.sketch_id,
            "kind": self.meta.kind,
            "origin": self.meta.origin,
            "chart_source": self.meta.chart_source,
            "instrument": self.meta.instrument,
            "asset_class": self.meta.asset_class,
            "created": self.meta.created.isoformat(),
            "title": self.meta.title,
            "chart_count": len(self.meta.charts),
        }

    def detail(self) -> dict[str, Any]:
        images = []
        for chart in self.meta.charts:
            content = (self.folder / chart.file).read_bytes()
            images.append(
                {
                    "file": chart.file,
                    "url": (
                        "/api/v1/sketches/"
                        f"{self.meta.origin}/{self.meta.sketch_id}/images/{chart.file}"
                    ),
                    "content_type": "image/png",
                    "byte_count": len(content),
                    "sha256": sha256(content).hexdigest(),
                }
            )
        return {
            "schema": "sketch_detail.v1",
            "meta": self.meta.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "instructions_markdown": self.instructions_markdown,
            "images": images,
        }


@dataclass(frozen=True, slots=True)
class SketchStore:
    """Store each accepted package at ``data/sketches/<origin>/<sketch-id>/``."""

    root: Path
    contracts_config: Path | None = None

    def import_zip(self, bundle: bytes) -> StoredSketch:
        """Validate the whole ZIP, then atomically publish its six original files."""
        members, container = _read_zip_members(bundle)
        package = _validate_members(
            members,
            container=container,
            require_catalog_identity=True,
            registry=self._registry(),
        )
        origin_root = self.root / package.meta.origin
        target = origin_root / package.meta.sketch_id

        with _STORE_LOCK:
            if target.exists():
                raise SketchAlreadyExistsError(
                    "sketch target already exists: "
                    f"({package.meta.origin}, {package.meta.sketch_id})"
                )
            origin_root.mkdir(parents=True, exist_ok=True)
            staging = origin_root / (
                f".{package.meta.sketch_id}.{os.getpid()}.{uuid4().hex}.tmp"
            )
            staging.mkdir()
            try:
                for filename in sorted(EXPECTED_PACKAGE_FILES):
                    _write_member(staging / filename, members[filename])
                try:
                    staging.rename(target)
                except OSError as exc:
                    if target.exists():
                        raise SketchAlreadyExistsError(
                            "sketch target already exists: "
                            f"({package.meta.origin}, {package.meta.sketch_id})"
                        ) from exc
                    raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
                if origin_root.exists() and not any(origin_root.iterdir()):
                    origin_root.rmdir()

        return self.get(package.meta.origin, package.meta.sketch_id)

    def list(self) -> list[StoredSketch]:
        if not self.root.is_dir():
            return []
        packages: list[StoredSketch] = []
        for origin_folder in self.root.iterdir():
            if not origin_folder.is_dir():
                continue
            try:
                origin = parse_sketch_origin(origin_folder.name)
            except SketchIdentityError:
                continue
            for folder in origin_folder.iterdir():
                if not folder.is_dir():
                    continue
                try:
                    sketch_id = parse_sketch_id(folder.name)
                except SketchIdentityError:
                    continue
                packages.append(
                    self._read_folder(
                        folder,
                        expected_origin=origin,
                        expected_sketch_id=sketch_id,
                    )
                )
        packages.sort(
            key=lambda package: (
                package.meta.created,
                package.meta.origin,
                package.meta.sketch_id,
            ),
            reverse=True,
        )
        return packages

    def get(self, origin: str, sketch_id: str) -> StoredSketch:
        try:
            canonical_origin = parse_sketch_origin(origin)
            canonical_sketch_id = parse_sketch_id(sketch_id)
        except SketchIdentityError as exc:
            raise SketchNotFoundError(str(exc)) from exc
        folder = self.root / canonical_origin / canonical_sketch_id
        if not folder.is_dir():
            raise SketchNotFoundError(
                f"unknown sketch identity: ({canonical_origin}, {canonical_sketch_id})"
            )
        return self._read_folder(
            folder,
            expected_origin=canonical_origin,
            expected_sketch_id=canonical_sketch_id,
        )

    def image_path(self, origin: str, sketch_id: str, filename: str) -> Path:
        if filename not in EXPECTED_CHART_FILES:
            raise SketchNotFoundError(f"unknown sketch image: {filename}")
        package = self.get(origin, sketch_id)
        path = package.folder / filename
        if not path.is_file():
            raise SketchNotFoundError(f"unknown sketch image: {filename}")
        return path

    def _read_folder(
        self,
        folder: Path,
        *,
        expected_origin: SketchOrigin,
        expected_sketch_id: str,
    ) -> StoredSketch:
        missing = [
            filename for filename in EXPECTED_PACKAGE_FILES if not (folder / filename).is_file()
        ]
        if missing:
            raise SketchValidationError(
                [
                    SketchValidationIssue(
                        path="package",
                        message=f"stored package is missing files: {sorted(missing)}",
                        fix="restore the original six-file sketch.v1 package",
                    )
                ]
            )
        members = {
            filename: (folder / filename).read_bytes() for filename in EXPECTED_PACKAGE_FILES
        }
        package = _validate_members(members, container=folder.name)
        identity_issues: list[SketchValidationIssue] = []
        if package.meta.origin != expected_origin:
            identity_issues.append(
                SketchValidationIssue(
                    path="origin",
                    message=(
                        f"stored meta origin {package.meta.origin!r} does not match "
                        f"directory origin {expected_origin!r}"
                    ),
                    fix="restore the package under the directory matching meta.yaml origin",
                )
            )
        if package.meta.sketch_id != expected_sketch_id:
            identity_issues.append(
                SketchValidationIssue(
                    path="sketch_id",
                    message=(
                        f"stored meta sketch_id {package.meta.sketch_id!r} does not match "
                        f"directory name {expected_sketch_id!r}"
                    ),
                    fix="restore the package under the directory matching meta.yaml sketch_id",
                )
            )
        if identity_issues:
            raise SketchValidationError(identity_issues)
        return StoredSketch(
            folder=folder,
            meta=package.meta,
            meta_yaml=package.meta_yaml,
            instructions_markdown=package.instructions_markdown,
        )

    def _registry(self) -> ContractRegistry:
        return ContractRegistry.from_yaml(
            self.contracts_config or (PROJECT_ROOT / "config" / "contracts.yaml")
        )


def default_sketch_store() -> SketchStore:
    return SketchStore(root=PROJECT_ROOT / "data" / "sketches")


def _write_member(path: Path, content: bytes) -> None:
    """Write one validated original member; isolated for atomic-failure testing."""
    path.write_bytes(content)


def _read_zip_members(bundle: bytes) -> tuple[dict[str, bytes], str | None]:
    try:
        with ZipFile(BytesIO(bundle)) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            if not files:
                raise SketchValidationError(
                    [
                        SketchValidationIssue(
                            path="package",
                            message="ZIP contains no files",
                            fix="upload the six-file sketch.v1 ZIP package",
                        )
                    ]
                )
            if sum(info.file_size for info in files) > _MAX_UNCOMPRESSED_BYTES:
                raise SketchValidationError(
                    [
                        SketchValidationIssue(
                            path="package",
                            message="uncompressed package is larger than 256 MiB",
                            fix="export the four original PNGs without unrelated files",
                        )
                    ]
                )
            paths = [_safe_zip_path(info.filename) for info in files]
            container = _container_for(paths)
            names = [path.name for path in paths]
            if len({name.casefold() for name in names}) != len(names):
                raise SketchValidationError(
                    [
                        SketchValidationIssue(
                            path="package",
                            message="ZIP contains duplicate filenames",
                            fix="keep one copy of each canonical sketch.v1 member",
                        )
                    ]
                )
            members = {
                path.name: archive.read(info)
                for path, info in zip(paths, files, strict=True)
            }
    except SketchValidationError:
        raise
    except (BadZipFile, RuntimeError, ValueError) as exc:
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path="package",
                    message=f"cannot read ZIP package ({exc})",
                    fix="upload a valid, unencrypted sketch.v1 ZIP",
                )
            ]
        ) from exc
    return members, container


def _safe_zip_path(filename: str) -> PurePosixPath:
    if "\\" in filename:
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path="package",
                    message=f"unsafe ZIP member path: {filename}",
                    fix="use flat filenames or one sketch-id top-level folder",
                )
            ]
        )
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts or len(path.parts) not in (1, 2):
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path="package",
                    message=f"unsafe ZIP member path: {filename}",
                    fix="use flat filenames or one sketch-id top-level folder",
                )
            ]
        )
    return path


def _container_for(paths: list[PurePosixPath]) -> str | None:
    part_counts = {len(path.parts) for path in paths}
    if part_counts == {1}:
        return None
    if part_counts == {2}:
        containers = {path.parts[0] for path in paths}
        if len(containers) == 1:
            return next(iter(containers))
    raise SketchValidationError(
        [
            SketchValidationIssue(
                path="package",
                message="ZIP members do not share one package root",
                fix="put all six files flat or under one sketch-id folder",
            )
        ]
    )


@dataclass(frozen=True, slots=True)
class _ValidatedMembers:
    meta: SketchMeta
    meta_yaml: str
    instructions_markdown: str


def _validate_members(
    members: dict[str, bytes],
    *,
    container: str | None,
    require_catalog_identity: bool = False,
    registry: ContractRegistry | None = None,
) -> _ValidatedMembers:
    issues: list[SketchValidationIssue] = []
    missing = EXPECTED_PACKAGE_FILES - members.keys()
    extras = members.keys() - EXPECTED_PACKAGE_FILES
    if missing:
        issues.append(
            SketchValidationIssue(
                path="package",
                message=f"missing required files: {sorted(missing)}",
                fix="include meta.yaml, INSTRUCTIONS.md, and the four canonical PNG filenames",
            )
        )
    if extras:
        issues.append(
            SketchValidationIssue(
                path="package",
                message=f"unexpected files: {sorted(extras)}",
                fix="upload the original six-file sketch package only",
            )
        )
    if issues:
        raise SketchValidationError(issues)

    meta_yaml = _decode_utf8(members["meta.yaml"], path="meta.yaml")
    instructions = _decode_utf8(members["INSTRUCTIONS.md"], path="INSTRUCTIONS.md")
    try:
        raw = yaml.safe_load(meta_yaml)
    except yaml.YAMLError as exc:
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path="meta.yaml",
                    message=f"YAML syntax error ({exc})",
                    fix="fix meta.yaml so it parses as a sketch.v1 mapping",
                )
            ]
        ) from exc
    if not isinstance(raw, dict):
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path="meta.yaml",
                    message="metadata must be a YAML mapping",
                    fix="start meta.yaml with schema: sketch.v1",
                )
            ]
        )
    try:
        meta = SketchMeta.model_validate(raw)
    except ValidationError as exc:
        raise SketchValidationError(_pydantic_issues(exc)) from exc

    if container is not None and container != meta.sketch_id:
        issues.append(
            SketchValidationIssue(
                path="package",
                message=(
                    f"top-level folder {container!r} does not match "
                    f"meta.yaml sketch_id {meta.sketch_id!r}"
                ),
                fix=f"rename the ZIP root folder to {meta.sketch_id}",
            )
        )
    first_line = instructions.splitlines()[0].strip() if instructions.splitlines() else ""
    expected_header = f"<!-- {meta.instructions_template} -->"
    if first_line != expected_header:
        issues.append(
            SketchValidationIssue(
                path="INSTRUCTIONS.md",
                message=(
                    f"first line must be {expected_header!r}, got {first_line!r}"
                ),
                fix="put the instructions_template version in the first-line HTML comment",
            )
        )
    for filename in EXPECTED_CHART_FILES:
        if not members[filename].startswith(_PNG_SIGNATURE):
            issues.append(
                SketchValidationIssue(
                    path=filename,
                    message="file does not have the PNG signature",
                    fix="export the original chart as PNG without renaming another file type",
                )
            )
    if require_catalog_identity:
        if registry is None:  # pragma: no cover - programming guard for store callers
            msg = "new sketch package validation requires a contract registry"
            raise RuntimeError(msg)
        issues.extend(_catalog_identity_issues(meta, registry))
    if issues:
        raise SketchValidationError(issues)
    return _ValidatedMembers(
        meta=meta,
        meta_yaml=meta_yaml,
        instructions_markdown=instructions,
    )


def _catalog_identity_issues(
    meta: SketchMeta,
    registry: ContractRegistry,
) -> list[SketchValidationIssue]:
    """Validate only newly imported package identity against the canonical catalog.

    Existing packages predate P2 and may be read without inventing fields or
    rewriting their bytes.  Intake is stricter: an accepted new package is
    always self-describing and catalog-consistent.
    """
    issues: list[SketchValidationIssue] = []
    if meta.instrument is None:
        issues.append(
            SketchValidationIssue(
                path="instrument",
                message="new sketch.v1 packages require instrument",
                fix="set instrument to an exact root symbol from config/contracts.yaml",
            )
        )
    if meta.asset_class is None:
        issues.append(
            SketchValidationIssue(
                path="asset_class",
                message="new sketch.v1 packages require asset_class",
                fix="set asset_class to the selected instrument's catalog asset_class",
            )
        )
    if meta.instrument is None or meta.asset_class is None:
        return issues
    try:
        contract = registry.by_symbol(meta.instrument)
    except KeyError:
        issues.append(
            SketchValidationIssue(
                path="instrument",
                message=f"instrument {meta.instrument!r} is not in config/contracts.yaml",
                fix="use one configured root symbol without changing its case or whitespace",
            )
        )
        return issues
    if meta.asset_class != contract.asset_class:
        issues.append(
            SketchValidationIssue(
                path="asset_class",
                message=(
                    f"asset_class {meta.asset_class!r} does not match catalog "
                    f"class {contract.asset_class!r} for {meta.instrument}"
                ),
                fix="copy the selected instrument's exact asset_class from config/contracts.yaml",
            )
        )
    return issues


def _decode_utf8(content: bytes, *, path: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SketchValidationError(
            [
                SketchValidationIssue(
                    path=path,
                    message="file is not valid UTF-8",
                    fix=f"save {path} as UTF-8 without changing its content",
                )
            ]
        ) from exc


def _pydantic_issues(exc: ValidationError) -> list[SketchValidationIssue]:
    issues: list[SketchValidationIssue] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        path = ".".join("schema" if item == "schema_name" else str(item) for item in loc)
        issues.append(
            SketchValidationIssue(
                path=path or "meta.yaml",
                message=str(error.get("msg") or "invalid value"),
                fix="correct the field to match sketch.v1 (docs/05 §3.5)",
            )
        )
    return issues
