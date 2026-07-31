"""Recoverable whole-identity archive contract for the P2 insight notebook."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest
import yaml

from futures_research.api.main import app
from futures_research.insight.store import (
    InsightConflictError,
    InsightNotFoundError,
    InsightStorageError,
    InsightStore,
    InsightVersionCollisionError,
)
from futures_research.sketch.store import SketchStore

_ARCHIVE_ID = re.compile(r"^delete-[0-9a-f]{32}$")
_CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_UNKNOWN_ARCHIVE_ID = "delete-00000000000040008000000000000000"


def _source(
    *,
    insight_id: str = "insight-007",
    origin: str = "workshop",
    version: int = 1,
    title: str | None = None,
) -> str:
    chosen_title = title or f"Archive contract version {version}"
    status = "recording" if version > 1 else "unverified"
    return "\n".join(
        [
            "schema: insight.v1",
            f"insight_id: {insight_id}",
            f"origin: {origin}",
            f"version: {version}",
            "based_on_sketch: sketch-20260725-02",
            f"based_on_sketch_origin: {origin}",
            "instrument: NQ",
            "asset_class: equity_index_futures",
            f"title: {chosen_title}",
            "condition:",
            "  type: session_time_filter",
            "  suggested_params: {skip_first_minutes: 30}",
            "measurement:",
            "  tag: entry_within_open_30m",
            "  hypothesis: Entries in the opening window have lower expectancy",
            f"validation_status: {status}",
            "",
        ]
    )


def _store(tmp_path: Path) -> InsightStore:
    sketch_root = tmp_path / "sketches"
    sketch_root.mkdir(parents=True, exist_ok=True)
    return InsightStore(
        root=tmp_path / "insights",
        sketch_store=SketchStore(root=sketch_root),
    )


@contextmanager
def _api_dependency(store: InsightStore) -> Iterator[None]:
    missing = object()
    prior = getattr(app.state, "insight_store", missing)
    app.state.insight_store = store
    try:
        yield
    finally:
        if prior is missing:
            delattr(app.state, "insight_store")
        else:
            app.state.insight_store = prior


@contextmanager
def _client() -> Iterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")


async def _import(client: httpx.AsyncClient, source_text: str) -> httpx.Response:
    return await client.post(
        "/api/v1/insights/import",
        json={"source_text": source_text},
    )


def _inventory(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _active_inventory(store: InsightStore) -> dict[str, bytes]:
    return _inventory(store.root / "workshop" / "insight-007")


def _archive_directory(store: InsightStore, response: httpx.Response) -> Path:
    body = response.json()
    return (
        store.root
        / "_deleted"
        / body["origin"]
        / body["insight_id"]
        / body["archive_id"]
    )


def _manifest(directory: Path) -> dict[str, Any]:
    payload = yaml.safe_load((directory / "_archive.yaml").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _rewrite_manifest(directory: Path, payload: dict[str, Any]) -> None:
    (directory / "_archive.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_two_version_archive_moves_whole_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _source(version=1)
    second = _source(version=2)
    with _api_dependency(store), _client() as client:
        async with client:
            assert (await _import(client, first)).status_code == 200
            assert (await _import(client, second)).status_code == 200
            active_before = _active_inventory(store)
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "schema",
        "origin",
        "insight_id",
        "archive_id",
        "deleted_at",
        "version_count",
        "archived_to",
    }
    assert body["schema"] == "insight_archive.v1"
    assert body["version_count"] == 2
    assert _ARCHIVE_ID.fullmatch(body["archive_id"])
    hexadecimal = body["archive_id"].removeprefix("delete-")
    assert hexadecimal[12] == "4"
    assert hexadecimal[16] in "89ab"
    assert _CANONICAL_UTC.fullmatch(body["deleted_at"])
    assert body["archived_to"] == (
        "data/insights/_deleted/workshop/insight-007/" + body["archive_id"]
    )
    assert "\\" not in body["archived_to"]
    assert ".." not in body["archived_to"]
    assert not (store.root / "workshop" / "insight-007").exists()
    archived = _inventory(_archive_directory(store, response))
    assert archived["v1.yaml"] == active_before["v1.yaml"]
    assert archived["v2.yaml"] == active_before["v2.yaml"]


@pytest.mark.asyncio
async def test_version_gap_archive_preserves_names_without_renumbering(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source(version=1))
            await _import(client, _source(version=3))
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert response.status_code == 200
    directory = _archive_directory(store, response)
    assert sorted(path.name for path in directory.glob("v*.yaml")) == [
        "v1.yaml",
        "v3.yaml",
    ]
    assert [item["version"] for item in _manifest(directory)["versions"]] == [1, 3]
    assert not (directory / "v2.yaml").exists()


@pytest.mark.asyncio
async def test_archive_version_files_are_byte_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = _source(version=1)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, source)
            original = _active_inventory(store)
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    archived = _inventory(_archive_directory(store, response))
    assert archived["v1.yaml"] == original["v1.yaml"]
    assert sha256(archived["v1.yaml"]).hexdigest() == sha256(
        original["v1.yaml"]
    ).hexdigest()


@pytest.mark.asyncio
async def test_archive_manifest_has_exact_schema_time_order_and_both_digests(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source(version=1))
            await _import(client, _source(version=3))
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    directory = _archive_directory(store, response)
    manifest = _manifest(directory)
    assert set(manifest) == {
        "schema",
        "archive_id",
        "origin",
        "insight_id",
        "deleted_at",
        "version_count",
        "versions",
    }
    assert manifest["schema"] == "insight_delete_archive.v1"
    assert manifest["archive_id"] == response.json()["archive_id"]
    assert manifest["origin"] == "workshop"
    assert manifest["insight_id"] == "insight-007"
    assert _CANONICAL_UTC.fullmatch(manifest["deleted_at"])
    datetime.strptime(manifest["deleted_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    assert manifest["version_count"] == len(manifest["versions"]) == 2
    assert [item["version"] for item in manifest["versions"]] == [1, 3]
    for item in manifest["versions"]:
        assert set(item) == {
            "version",
            "filename",
            "file_sha256",
            "source_content_sha256",
        }
        version_file = directory / item["filename"]
        record = yaml.safe_load(version_file.read_text(encoding="utf-8"))
        assert item["filename"] == f"v{item['version']}.yaml"
        assert item["file_sha256"] == sha256(version_file.read_bytes()).hexdigest()
        assert item["source_content_sha256"] == record["content_sha256"]
        assert item["source_content_sha256"] == sha256(
            record["source_text"].encode("utf-8")
        ).hexdigest()


@pytest.mark.asyncio
async def test_active_list_and_detail_exclude_archived_identity(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source())
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            listed = await client.get("/api/v1/insights")
            detail = await client.get(
                "/api/v1/insights/workshop/insight-007"
            )
            archive_list = await client.get("/api/v1/insights/archives")

    assert archived.status_code == 200
    assert listed.json() == {
        "schema": "insight_list.v1",
        "count": 0,
        "insights": [],
    }
    assert detail.status_code == 404
    assert archive_list.json()["count"] == 1


@pytest.mark.asyncio
async def test_archive_list_has_exact_schema_and_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(
                client,
                _source(insight_id="insight-008", origin="workshop"),
            )
            first = await client.delete(
                "/api/v1/insights/workshop/insight-008"
            )
            await _import(
                client,
                _source(insight_id="insight-007", origin="journal-app"),
            )
            second = await client.delete(
                "/api/v1/insights/journal-app/insight-007"
            )

            tied_time = "2026-07-28T12:34:56.123456Z"
            for response in (first, second):
                directory = _archive_directory(store, response)
                manifest = _manifest(directory)
                manifest["deleted_at"] = tied_time
                _rewrite_manifest(directory, manifest)
            listed = await client.get("/api/v1/insights/archives")

    assert listed.status_code == 200
    body = listed.json()
    assert set(body) == {"schema", "count", "archives"}
    assert body["schema"] == "insight_archive_list.v1"
    assert body["count"] == 2
    assert [
        (item["origin"], item["insight_id"]) for item in body["archives"]
    ] == [
        ("journal-app", "insight-007"),
        ("workshop", "insight-008"),
    ]
    for item in body["archives"]:
        assert set(item) == {
            "archive_id",
            "origin",
            "insight_id",
            "deleted_at",
            "version_count",
        }
        assert item["deleted_at"] == tied_time


@pytest.mark.asyncio
async def test_restore_recovers_exact_bytes_and_consumes_archive(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source(version=1))
            await _import(client, _source(version=2))
            original = _active_inventory(store)
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            archive_id = archived.json()["archive_id"]
            restored = await client.post(
                "/api/v1/insights/archives/"
                f"workshop/insight-007/{archive_id}/restore"
            )
            active_list = await client.get("/api/v1/insights")
            archive_list = await client.get("/api/v1/insights/archives")

    assert restored.status_code == 200
    body = restored.json()
    assert set(body) == {
        "schema",
        "origin",
        "insight_id",
        "archive_id",
        "restored_at",
        "version_count",
        "restored_to",
    }
    assert body == {
        "schema": "insight_restore.v1",
        "origin": "workshop",
        "insight_id": "insight-007",
        "archive_id": archive_id,
        "restored_at": body["restored_at"],
        "version_count": 2,
        "restored_to": "data/insights/workshop/insight-007",
    }
    assert _CANONICAL_UTC.fullmatch(body["restored_at"])
    assert _active_inventory(store) == original
    assert active_list.json()["count"] == 1
    assert archive_list.json()["count"] == 0


@pytest.mark.asyncio
async def test_import_of_archived_identity_is_409_and_unchanged(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source(version=1))
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            before = _inventory(store.root)
            retry = await _import(client, _source(version=1))
            next_version = await _import(client, _source(version=2))

    assert retry.status_code == 409
    assert next_version.status_code == 409
    assert "restore" in retry.json()["detail"]["message"]
    assert "unchanged" in retry.json()["detail"]["message"]
    assert _inventory(store.root) == before
    assert _archive_directory(store, archived).is_dir()


@pytest.mark.asyncio
async def test_restore_active_collision_is_409_without_merge_or_overwrite(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source())
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            archive_directory = _archive_directory(store, archived)
            archive_before = _inventory(archive_directory)
            active = store.root / "workshop" / "insight-007"
            active.mkdir(parents=True)
            (active / "collision.txt").write_bytes(b"owner-active-collision")
            active_before = _inventory(active)
            archive_id = archived.json()["archive_id"]
            response = await client.post(
                "/api/v1/insights/archives/"
                f"workshop/insight-007/{archive_id}/restore"
            )

    assert response.status_code == 409
    assert "unchanged" in response.json()["detail"]
    assert _inventory(active) == active_before
    assert _inventory(archive_directory) == archive_before


@pytest.mark.asyncio
async def test_active_and_archive_ambiguity_is_503_and_byte_exact(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source(version=1))
    store.import_document(_source(version=2))
    active = store.root / "workshop" / "insight-007"
    original_active = _inventory(active)

    outcome = store.archive("workshop", "insight-007")
    archive = (
        store.root
        / "_deleted"
        / "workshop"
        / "insight-007"
        / outcome.archive_id
    )
    original_archive = _inventory(archive)

    active.mkdir(parents=True)
    for relative_path, data in original_active.items():
        (active / relative_path).write_bytes(data)
    ambiguous_inventory = _inventory(store.root)

    with _api_dependency(store), _client() as client:
        async with client:
            ambiguous = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert ambiguous.status_code == 503
    assert ambiguous.status_code not in {404, 409}
    assert "could not be proven" in ambiguous.json()["detail"]
    assert "no success" in ambiguous.json()["detail"]
    assert _inventory(active) == original_active
    assert _inventory(archive) == original_archive
    assert _inventory(store.root) == ambiguous_inventory

    with pytest.raises(
        InsightStorageError,
        match="active and archived insight states both exist",
    ):
        store.archive("workshop", "insight-007")
    assert _inventory(store.root) == ambiguous_inventory

    archive_parent = archive.parent
    assert [path.name for path in archive_parent.iterdir()] == [outcome.archive_id]
    assert not [
        path for path in store.root.rglob("*") if path.name.startswith(".")
    ]

    for version_file in active.iterdir():
        version_file.unlink()
    active.rmdir()
    archived_only_inventory = _inventory(store.root)

    with pytest.raises(InsightConflictError):
        store.archive("workshop", "insight-007")
    with _api_dependency(store), _client() as client:
        async with client:
            archived_only = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert archived_only.status_code == 409
    assert _inventory(store.root) == archived_only_inventory
    assert _inventory(archive) == original_archive


@pytest.mark.asyncio
async def test_archive_target_collision_is_409_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    active_before = _active_inventory(store)
    sentinel = b"concurrent-owner-archive"

    def collide(_temporary: Path, target: Path) -> None:
        target.mkdir()
        (target / "sentinel.bin").write_bytes(sentinel)
        raise FileExistsError(target)

    monkeypatch.setattr(
        InsightStore,
        "_publish_archive_directory",
        staticmethod(collide),
    )
    with _api_dependency(store), _client() as client:
        async with client:
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert response.status_code == 409
    assert _active_inventory(store) == active_before
    sentinels = list((store.root / "_deleted").rglob("sentinel.bin"))
    assert len(sentinels) == 1
    assert sentinels[0].read_bytes() == sentinel


@pytest.mark.asyncio
async def test_invalid_archive_paths_are_404_without_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    before = _inventory(store.root)
    with _api_dependency(store), _client() as client:
        async with client:
            responses = [
                await client.delete(
                    "/api/v1/insights/%2E%2E%2Fworkshop/insight-007"
                ),
                await client.delete(
                    "/api/v1/insights/workshop/insight-007%2F%2E%2E"
                ),
                await client.post(
                    "/api/v1/insights/archives/workshop/insight-007/"
                    "delete-00000000000000000000000000000000/restore"
                ),
                await client.post(
                    "/api/v1/insights/archives/workshop/insight-007/"
                    f"{_UNKNOWN_ARCHIVE_ID}%2F%2E%2E/restore"
                ),
            ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    with pytest.raises(InsightNotFoundError):
        store.archive("../workshop", "insight-007")
    with pytest.raises(InsightNotFoundError):
        store.archive("workshop", "../insight-007")
    with pytest.raises(InsightNotFoundError):
        store.restore("workshop", "insight-007", "../archive")
    assert _inventory(store.root) == before


@pytest.mark.asyncio
async def test_corrupt_manifest_is_503_not_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source())
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            directory = _archive_directory(store, archived)
            manifest = _manifest(directory)
            manifest["schema"] = "insight_delete_archive.v2"
            _rewrite_manifest(directory, manifest)
            before = _inventory(store.root)
            listed = await client.get("/api/v1/insights/archives")
            restored = await client.post(
                "/api/v1/insights/archives/workshop/insight-007/"
                f"{archived.json()['archive_id']}/restore"
            )

    assert listed.status_code == 503
    assert restored.status_code == 503
    assert "could not be proven" in listed.json()["detail"]
    assert _inventory(store.root) == before


@pytest.mark.asyncio
async def test_corrupt_archived_file_sha_is_503(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source())
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            directory = _archive_directory(store, archived)
            version_file = directory / "v1.yaml"
            version_file.write_bytes(version_file.read_bytes() + b"\n")
            before = _inventory(store.root)
            listed = await client.get("/api/v1/insights/archives")

    assert listed.status_code == 503
    assert _inventory(store.root) == before


@pytest.mark.asyncio
async def test_corrupt_embedded_source_digest_is_503(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with _api_dependency(store), _client() as client:
        async with client:
            await _import(client, _source())
            archived = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )
            directory = _archive_directory(store, archived)
            version_file = directory / "v1.yaml"
            record = yaml.safe_load(version_file.read_text(encoding="utf-8"))
            record["source_text"] += "# embedded source tampered\n"
            changed = yaml.safe_dump(
                record,
                allow_unicode=True,
                sort_keys=False,
            ).encode()
            version_file.write_bytes(changed)
            manifest = _manifest(directory)
            manifest["versions"][0]["file_sha256"] = sha256(changed).hexdigest()
            _rewrite_manifest(directory, manifest)
            before = _inventory(store.root)
            listed = await client.get("/api/v1/insights/archives")

    assert listed.status_code == 503
    assert _inventory(store.root) == before


@pytest.mark.asyncio
async def test_archive_publish_failure_keeps_active_and_no_visible_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    before = _active_inventory(store)

    def fail_publish(_temporary: Path, _target: Path) -> None:
        raise OSError("injected archive publication failure")

    monkeypatch.setattr(
        InsightStore,
        "_publish_archive_directory",
        staticmethod(fail_publish),
    )
    with _api_dependency(store), _client() as client:
        async with client:
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert response.status_code == 503
    assert "injected" not in response.text
    assert _active_inventory(store) == before
    assert not list((store.root / "_deleted").rglob("delete-*"))


@pytest.mark.asyncio
async def test_active_removal_failure_never_loses_active_or_false_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    before = _active_inventory(store)

    def fail_remove(_active: Path) -> None:
        raise OSError("injected active removal failure")

    monkeypatch.setattr(
        InsightStore,
        "_remove_active_directory",
        staticmethod(fail_remove),
    )
    with _api_dependency(store), _client() as client:
        async with client:
            response = await client.delete(
                "/api/v1/insights/workshop/insight-007"
            )

    assert response.status_code == 503
    assert _active_inventory(store) == before
    assert not list((store.root / "_deleted").rglob("delete-*"))


@pytest.mark.asyncio
async def test_restore_publication_failure_keeps_archive_and_zero_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    outcome = store.archive("workshop", "insight-007")
    archive = (
        store.root
        / "_deleted"
        / "workshop"
        / "insight-007"
        / outcome.archive_id
    )
    before = _inventory(archive)

    def fail_publish(_temporary: Path, _active: Path) -> None:
        raise OSError("injected restore publication failure")

    monkeypatch.setattr(
        InsightStore,
        "_publish_restore_directory",
        staticmethod(fail_publish),
    )
    with _api_dependency(store), _client() as client:
        async with client:
            response = await client.post(
                "/api/v1/insights/archives/workshop/insight-007/"
                f"{outcome.archive_id}/restore"
            )

    assert response.status_code == 503
    assert not (store.root / "workshop" / "insight-007").exists()
    assert _inventory(archive) == before


@pytest.mark.asyncio
async def test_archive_consume_failure_never_false_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    outcome = store.archive("workshop", "insight-007")
    archive = (
        store.root
        / "_deleted"
        / "workshop"
        / "insight-007"
        / outcome.archive_id
    )
    before = _inventory(archive)

    def fail_consume(_archive: Path) -> None:
        raise OSError("injected archive consume failure")

    monkeypatch.setattr(
        InsightStore,
        "_consume_archive_directory",
        staticmethod(fail_consume),
    )
    with _api_dependency(store), _client() as client:
        async with client:
            response = await client.post(
                "/api/v1/insights/archives/workshop/insight-007/"
                f"{outcome.archive_id}/restore"
            )

    assert response.status_code == 503
    assert not (store.root / "workshop" / "insight-007").exists()
    assert _inventory(archive) == before


def _thread_result(call: Callable[[], object]) -> tuple[str, str]:
    try:
        return "ok", type(call()).__name__
    except (
        InsightConflictError,
        InsightNotFoundError,
        InsightStorageError,
        InsightVersionCollisionError,
    ) as exc:
        return "error", type(exc).__name__


@pytest.mark.parametrize(
    "case",
    [
        "archive-vs-archive",
        "archive-vs-import-next",
        "restore-vs-restore",
        "restore-vs-import",
    ],
)
def test_mutation_races_have_one_winner_and_proven_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    store = _store(tmp_path)
    store.import_document(_source())
    original = _active_inventory(store)
    entered = Event()
    release = Event()

    if case.startswith("archive"):
        original_materialize = InsightStore._materialize_archive

        def gated_archive(
            directory: Path,
            manifest: dict[str, Any],
            versions: tuple[Any, ...],
        ) -> None:
            entered.set()
            assert release.wait(timeout=10)
            original_materialize(directory, manifest, versions)

        monkeypatch.setattr(
            InsightStore,
            "_materialize_archive",
            staticmethod(gated_archive),
        )

        def archive_first() -> object:
            return store.archive("workshop", "insight-007")

        def archive_second() -> object:
            return store.archive("workshop", "insight-007")

        def import_next() -> object:
            return store.import_document(_source(version=2))

        first_call: Callable[[], object] = archive_first
        second_call: Callable[[], object] = (
            archive_second if case == "archive-vs-archive" else import_next
        )
        final_state = "archive"
    else:
        archived = store.archive("workshop", "insight-007")
        original_materialize = InsightStore._materialize_restore

        def gated_restore(
            directory: Path,
            versions: tuple[Any, ...],
        ) -> None:
            entered.set()
            assert release.wait(timeout=10)
            original_materialize(directory, versions)

        monkeypatch.setattr(
            InsightStore,
            "_materialize_restore",
            staticmethod(gated_restore),
        )

        def restore_first() -> object:
            return store.restore(
                "workshop",
                "insight-007",
                archived.archive_id,
            )

        def restore_second() -> object:
            return store.restore(
                "workshop",
                "insight-007",
                archived.archive_id,
            )

        def import_conflict() -> object:
            return store.import_document(
                _source(title="Concurrent conflicting import")
            )

        first_call = restore_first
        second_call = (
            restore_second if case == "restore-vs-restore" else import_conflict
        )
        final_state = "active"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_thread_result, first_call)
        assert entered.wait(timeout=10)
        second = executor.submit(_thread_result, second_call)
        release.set()
        results = [first.result(timeout=20), second.result(timeout=20)]

    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("error") == 1
    if final_state == "archive":
        archives = store.list_archives()
        assert len(archives) == 1
        archive = (
            store.root
            / "_deleted"
            / "workshop"
            / "insight-007"
            / archives[0]["archive_id"]
        )
        archived_files = _inventory(archive)
        assert archived_files["v1.yaml"] == original["v1.yaml"]
        assert not (store.root / "workshop" / "insight-007").exists()
    else:
        assert store.list_archives() == []
        assert _active_inventory(store) == original
