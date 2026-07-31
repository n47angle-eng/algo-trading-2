"""Correction contract for composite ``sketch.v1`` persistence and read APIs."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from futures_research.api.main import app
from futures_research.sketch import store as sketch_store_module
from futures_research.sketch.store import SketchStore

SKETCH_ID = "sketch-20260726-01"
WORKSHOP = "workshop"
JOURNAL = "journal-app"
ARABIC_INDIC_SKETCH_ID = "sketch-\u0662\u0660\u0662\u0666\u0660\u0667\u0662\u0666-\u0660\u0661"
FULLWIDTH_SKETCH_ID = "sketch-\uff12\uff10\uff12\uff16\uff10\uff17\uff12\uff16-\uff10\uff11"
ARABIC_INDIC_DATE = "\u0662\u0660\u0662\u0666-\u0660\u0667-\u0662\u0666"
FULLWIDTH_RANGE_START = "\uff12\uff10\uff12\uff16-\uff10\uff15-\uff10\uff11"
FULLWIDTH_RANGE_END = "\uff12\uff10\uff12\uff16-\uff10\uff17-\uff12\uff16"
EXPECTED_FILES = {
    "meta.yaml",
    "INSTRUCTIONS.md",
    "chart-D.png",
    "chart-1H.png",
    "chart-30m.png",
    "chart-5m.png",
}
CHART_FILES = tuple(sorted(filename for filename in EXPECTED_FILES if filename.endswith(".png")))

META_YAML = """\
schema: sketch.v1
sketch_id: sketch-20260726-01
kind: strategy
origin: workshop
chart_source: rendered
instructions_template: instructions.v1
instrument: NQ
asset_class: equity_index_futures
created: 2026-07-26
title: 大框架武裝細框架回踩
rationale: |
  大時間框架趨勢中嘅細框架回調，等細框架 signal bar 確認。
charts:
  - file: chart-D.png
    timeframe: D
    role: bias
    range: [2026-05-01, 2026-07-26]
    indicators_shown: [ema18, ema50, ema90]
    drawings:
      - {type: hline, price: 23150, label: 區間底}
    owner_view: 日線係交易日標籤，唔可以因時區移前一日。
  - file: chart-1H.png
    timeframe: 1H
    role: mid
    indicators_shown: [ema18, ema90]
    owner_view: 1H 黃金交叉後，等第一次回踩。
  - file: chart-30m.png
    timeframe: 30m
    role: auxiliary
    indicators_shown: []
    indicators_other: [Owner 手畫趨勢線]
    owner_view: 30m 只做輔助，唔取代 1H 判斷。
  - file: chart-5m.png
    timeframe: 5m
    role: entry
    indicators_shown: [ema18]
    owner_view: 5m 要 signal bar 完成先入。
"""
INSTRUCTIONS = """\
<!-- instructions.v1 -->
# Terminal AI 指令書

先讀四張圖同 Owner 原文；有疑問要問清楚，完成後寫 strategy.yaml。
"""
OWNER_VIEWS = {
    "chart-D.png": "日線係交易日標籤，唔可以因時區移前一日。",
    "chart-1H.png": "1H 黃金交叉後，等第一次回踩。",
    "chart-30m.png": "30m 只做輔助，唔取代 1H 判斷。",
    "chart-5m.png": "5m 要 signal bar 完成先入。",
}
INDICATORS_SHOWN = [["ema18", "ema50", "ema90"], ["ema18", "ema90"], [], ["ema18"]]


def _images(tag: str) -> dict[str, bytes]:
    return {
        filename: b"\x89PNG\r\n\x1a\n" + f"original::{tag}::{filename}".encode()
        for filename in CHART_FILES
    }


def _meta(*, origin: str = WORKSHOP, sketch_id: str = SKETCH_ID) -> str:
    return (
        META_YAML.replace(f"sketch_id: {SKETCH_ID}", f"sketch_id: {sketch_id}")
        .replace(f"origin: {WORKSHOP}", f"origin: {origin}")
    )


def _zip_bundle(
    *,
    meta_yaml: str = META_YAML,
    instructions: str = INSTRUCTIONS,
    images: Mapping[str, bytes] | None = None,
    rooted: bool = True,
    root_name: str = SKETCH_ID,
    path_overrides: Mapping[str, str] | None = None,
    omitted: frozenset[str] = frozenset(),
    extra_members: Mapping[str, bytes] | None = None,
    meta_bytes: bytes | None = None,
    instructions_bytes: bytes | None = None,
) -> bytes:
    """Hand-authored fixture package; path overrides exercise ZIP safety gates."""
    payloads: dict[str, bytes] = {
        "meta.yaml": meta_yaml.encode("utf-8") if meta_bytes is None else meta_bytes,
        "INSTRUCTIONS.md": (
            instructions.encode("utf-8") if instructions_bytes is None else instructions_bytes
        ),
        **(_images("workshop") if images is None else dict(images)),
    }
    prefix = f"{root_name}/" if rooted else ""
    overrides = path_overrides or {}
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in payloads.items():
            if filename not in omitted:
                archive.writestr(overrides.get(filename, f"{prefix}{filename}"), content)
        for filename, content in (extra_members or {}).items():
            archive.writestr(filename, content)
    return stream.getvalue()


@pytest.fixture
def sketch_store(tmp_path: Path) -> SketchStore:
    return SketchStore(root=tmp_path / "sketches")


@pytest.fixture
def sketch_api(
    sketch_store: SketchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> SketchStore:
    monkeypatch.setattr(
        "futures_research.api.routes_sketches.default_sketch_store",
        lambda: sketch_store,
    )
    return sketch_store


def _partial_paths(store: SketchStore) -> list[Path]:
    if not store.root.exists():
        return []
    return [path for path in store.root.rglob(".*.tmp")]


def _assert_no_partial(
    store: SketchStore,
    *,
    origin: str = WORKSHOP,
    sketch_id: str = SKETCH_ID,
) -> None:
    assert not (store.root / origin / sketch_id).exists()
    origin_root = store.root / origin
    assert not origin_root.exists() or list(origin_root.iterdir()) == []
    assert _partial_paths(store) == []
    assert store.list() == []


@pytest.mark.asyncio
async def test_import_writes_the_exact_six_original_files_under_composite_identity(
    sketch_api: SketchStore,
) -> None:
    images = _images("workshop")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(images=images),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 201, response.text
    folder = sketch_api.root / WORKSHOP / SKETCH_ID
    assert {path.name for path in folder.iterdir()} == EXPECTED_FILES
    assert (folder / "meta.yaml").read_bytes() == META_YAML.encode("utf-8")
    assert (folder / "INSTRUCTIONS.md").read_bytes() == INSTRUCTIONS.encode("utf-8")
    for filename, original in images.items():
        assert (folder / filename).read_bytes() == original
    assert response.json()["meta"]["instrument"] == "NQ"
    assert response.json()["meta"]["asset_class"] == "equity_index_futures"


@pytest.mark.asyncio
async def test_same_id_from_two_origins_roundtrips_as_two_distinct_packages(
    sketch_api: SketchStore,
) -> None:
    workshop_images = _images("workshop")
    journal_images = _images("journal")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=_meta(origin=WORKSHOP), images=workshop_images),
            headers={"content-type": "application/zip"},
        )
        second = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=_meta(origin=JOURNAL), images=journal_images),
            headers={"content-type": "application/zip"},
        )
        assert first.status_code == second.status_code == 201

        listed = await client.get("/api/v1/sketches")
        assert listed.status_code == 200
        list_payload = listed.json()
        assert set(list_payload) == {"schema", "count", "sketches"}
        assert list_payload["schema"] == "sketch_list.v1"
        assert list_payload["count"] == 2
        summaries = list_payload["sketches"]
        assert {(row["origin"], row["sketch_id"]) for row in summaries} == {
            (WORKSHOP, SKETCH_ID),
            (JOURNAL, SKETCH_ID),
        }

        for origin, expected_images in (
            (WORKSHOP, workshop_images),
            (JOURNAL, journal_images),
        ):
            detail_response = await client.get(f"/api/v1/sketches/{origin}/{SKETCH_ID}")
            assert detail_response.status_code == 200
            detail = detail_response.json()
            assert set(detail) == {"schema", "meta", "instructions_markdown", "images"}
            assert detail["schema"] == "sketch_detail.v1"
            assert detail["meta"]["origin"] == origin
            assert detail["meta"]["sketch_id"] == SKETCH_ID
            assert detail["meta"]["instrument"] == "NQ"
            assert detail["meta"]["asset_class"] == "equity_index_futures"
            assert len(detail["meta"]["charts"]) == 4
            assert detail["instructions_markdown"] == INSTRUCTIONS
            assert {
                chart["file"]: chart["owner_view"] for chart in detail["meta"]["charts"]
            } == OWNER_VIEWS
            assert [
                chart["indicators_shown"] for chart in detail["meta"]["charts"]
            ] == INDICATORS_SHOWN
            assert {image["file"] for image in detail["images"]} == set(expected_images)
            assert all(image["content_type"] == "image/png" for image in detail["images"])
            assert all(f"/{origin}/{SKETCH_ID}/images/" in row["url"] for row in detail["images"])
            for image in detail["images"]:
                raw = await client.get(image["url"])
                assert raw.status_code == 200
                assert raw.content == expected_images[image["file"]]

    assert workshop_images["chart-D.png"] != journal_images["chart-D.png"]
    assert (sketch_api.root / WORKSHOP / SKETCH_ID / "chart-D.png").read_bytes() == workshop_images[
        "chart-D.png"
    ]
    assert (sketch_api.root / JOURNAL / SKETCH_ID / "chart-D.png").read_bytes() == journal_images[
        "chart-D.png"
    ]


@pytest.mark.asyncio
async def test_same_composite_identity_returns_409_without_overwriting_original_bytes(
    sketch_api: SketchStore,
) -> None:
    original = _images("original")
    changed = _images("changed")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(images=original),
            headers={"content-type": "application/zip"},
        )
        duplicate = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(images=changed),
            headers={"content-type": "application/zip"},
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    folder = sketch_api.root / WORKSHOP / SKETCH_ID
    assert {path.name: path.read_bytes() for path in folder.iterdir()}["chart-D.png"] == original[
        "chart-D.png"
    ]


@pytest.mark.asyncio
async def test_id_only_detail_and_image_aliases_are_removed(sketch_api: SketchStore) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(),
            headers={"content-type": "application/zip"},
        )
        detail = await client.get(f"/api/v1/sketches/{SKETCH_ID}")
        image = await client.get(f"/api/v1/sketches/{SKETCH_ID}/images/chart-D.png")

    assert imported.status_code == 201
    assert detail.status_code == 404
    assert image.status_code == 404
    paths = app.openapi()["paths"]
    assert "/api/v1/sketches/{sketch_id}" not in paths
    assert "/api/v1/sketches/{sketch_id}/images/{filename}" not in paths


@pytest.mark.asyncio
async def test_timeframe_is_not_locked_to_chart_filename_and_may_repeat(
    sketch_api: SketchStore,
) -> None:
    metadata = META_YAML.replace("    timeframe: D\n", "    timeframe: 5m\n")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 201, response.text
    assert response.json()["meta"]["charts"][0]["timeframe"] == "5m"


@pytest.mark.asyncio
async def test_zip_root_must_be_the_sketch_id_never_the_origin(sketch_api: SketchStore) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(root_name=WORKSHOP),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 422
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "expected_path"),
    [
        (_meta(sketch_id="sketch-20260726-001"), "sketch_id"),
        (_meta(sketch_id="sketch-20260230-01"), "sketch_id"),
        (_meta(sketch_id="sketch-20261301-01"), "sketch_id"),
        (_meta(sketch_id=ARABIC_INDIC_SKETCH_ID), "sketch_id"),
        (_meta(sketch_id=FULLWIDTH_SKETCH_ID), "sketch_id"),
        (
            META_YAML.replace(
                f"sketch_id: {SKETCH_ID}",
                f'sketch_id: " {SKETCH_ID}"',
            ),
            "sketch_id",
        ),
        (
            META_YAML.replace(
                f"sketch_id: {SKETCH_ID}",
                f'sketch_id: "{SKETCH_ID} "',
            ),
            "sketch_id",
        ),
        (META_YAML.replace("origin: workshop", 'origin: "workshop "'), "origin"),
        (META_YAML.replace("created: 2026-07-26", "created: 0"), "created"),
        (META_YAML.replace("created: 2026-07-26", "created: true"), "created"),
        (
            META_YAML.replace("created: 2026-07-26", 'created: " 2026-07-26"'),
            "created",
        ),
        (
            META_YAML.replace(
                "range: [2026-05-01, 2026-07-26]",
                "range: [0, 86400]",
            ),
            "charts.0.range",
        ),
        (
            META_YAML.replace(
                "range: [2026-05-01, 2026-07-26]",
                "range: [2026-05-01T00:00:00Z, 2026-07-26]",
            ),
            "charts.0.range",
        ),
        (
            META_YAML.replace(
                "range: [2026-05-01, 2026-07-26]",
                'range: ["2026-05-01 ", "2026-07-26"]',
            ),
            "charts.0.range",
        ),
        (
            META_YAML.replace("created: 2026-07-26", f'created: "{ARABIC_INDIC_DATE}"'),
            "created",
        ),
        (
            META_YAML.replace(
                "range: [2026-05-01, 2026-07-26]",
                f'range: ["{FULLWIDTH_RANGE_START}", "{FULLWIDTH_RANGE_END}"]',
            ),
            "charts.0.range",
        ),
        (
            META_YAML.replace("[ema18, ema50, ema90]", "[madeup999]"),
            "charts.0.indicators_shown",
        ),
        (META_YAML.replace("[ema18, ema50, ema90]", "[ema0]"), "charts.0.indicators_shown"),
        (META_YAML.replace("[ema18, ema50, ema90]", "[bb20_0]"), "charts.0.indicators_shown"),
    ],
)
async def test_invalid_canonical_metadata_is_rejected_without_partial_package(
    sketch_api: SketchStore,
    metadata: str,
    expected_path: str,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata, rooted=False),
            headers={"content-type": "application/zip"},
        )
        listed = await client.get("/api/v1/sketches")

    assert response.status_code == 422
    assert any(issue["path"] == expected_path for issue in response.json()["detail"]["issues"])
    assert listed.json()["count"] == 0
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        "ema1\u0661",
        "ema1\uff11",
        "ema\u0661",
        "bb2\u0660_2",
        "bb20_2.\u0665",
        "bb2\uff10_2",
        "bb20_2.\uff15",
    ],
)
async def test_unicode_digits_in_indicator_tokens_are_rejected_with_other_field_guidance(
    sketch_api: SketchStore,
    token: str,
) -> None:
    metadata = META_YAML.replace("[ema18, ema50, ema90]", f"[{token}]")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata, rooted=False),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 422
    issue = next(
        issue
        for issue in response.json()["detail"]["issues"]
        if issue["path"] == "charts.0.indicators_shown"
    )
    assert "indicators_other" in issue["message"]
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
async def test_canonical_indicator_token_positives_are_accepted(sketch_api: SketchStore) -> None:
    metadata = (
        META_YAML.replace("[ema18, ema50, ema90]", "[sma20, atr14, rsi14]")
        .replace("[ema18, ema90]", "[vwap, volume, macd, bb20_2.5]")
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "instructions", "expected_path"),
    [
        (
            META_YAML.replace("created: 2026-07-26", "created: 2026-07-26T13:30:00Z"),
            INSTRUCTIONS,
            "created",
        ),
        (
            META_YAML.replace("    indicators_shown: []\n", ""),
            INSTRUCTIONS,
            "charts.2.indicators_shown",
        ),
        (
            META_YAML.replace("    role: bias\n", "    role: null\n"),
            INSTRUCTIONS,
            "charts.0.role",
        ),
        (
            META_YAML,
            INSTRUCTIONS.replace("instructions.v1", "instructions.v2", 1),
            "INSTRUCTIONS.md",
        ),
    ],
)
async def test_existing_package_schema_guards_remain_fail_closed(
    sketch_api: SketchStore,
    metadata: str,
    instructions: str,
    expected_path: str,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata, instructions=instructions),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 422
    assert any(issue["path"] == expected_path for issue in response.json()["detail"]["issues"])
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
@pytest.mark.parametrize("timezone_name", ["Pacific/Kiritimati", "America/Adak"])
async def test_created_trading_date_stays_the_same_in_opposite_timezones(
    sketch_api: SketchStore,
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
) -> None:
    monkeypatch.setenv("TZ", timezone_name)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        imported = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(),
            headers={"content-type": "application/zip"},
        )
        assert imported.status_code == 201
        detail = await client.get(f"/api/v1/sketches/{WORKSHOP}/{SKETCH_ID}")

    assert detail.json()["meta"]["created"] == "2026-07-26"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path_overrides", "omitted", "extra_members", "meta_bytes", "instructions_bytes", "images"),
    [
        ({"chart-D.png": "../chart-D.png"}, frozenset(), None, None, None, None),
        ({"chart-D.png": "bad\\chart-D.png"}, frozenset(), None, None, None, None),
        ({"chart-D.png": "/chart-D.png"}, frozenset(), None, None, None, None),
        ({"chart-D.png": "too/deep/chart-D.png"}, frozenset(), None, None, None, None),
        ({"chart-D.png": "chart-D.png"}, frozenset(), None, None, None, None),
        (
            None,
            frozenset(),
            {f"{SKETCH_ID}/CHART-D.PNG": b"duplicate"},
            None,
            None,
            None,
        ),
        (None, frozenset({"chart-D.png"}), None, None, None, None),
        (None, frozenset(), {"extra.txt": b"nope"}, None, None, None),
        (None, frozenset(), None, b"\xff\xfe", None, None),
        (None, frozenset(), None, None, b"\xff\xfe", None),
        (None, frozenset(), None, None, None, {**_images("fake"), "chart-D.png": b"not-a-png"}),
    ],
)
async def test_zip_member_failures_are_api_rejections_without_partial_state(
    sketch_api: SketchStore,
    path_overrides: Mapping[str, str] | None,
    omitted: frozenset[str],
    extra_members: Mapping[str, bytes] | None,
    meta_bytes: bytes | None,
    instructions_bytes: bytes | None,
    images: Mapping[str, bytes] | None,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(
                path_overrides=path_overrides,
                omitted=omitted,
                extra_members=extra_members,
                meta_bytes=meta_bytes,
                instructions_bytes=instructions_bytes,
                images=images,
            ),
            headers={"content-type": "application/zip"},
        )
        listed = await client.get("/api/v1/sketches")

    assert response.status_code == 422, response.text
    assert listed.json()["count"] == 0
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
async def test_wrong_content_type_is_rejected_without_writing(sketch_api: SketchStore) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(),
            headers={"content-type": "application/octet-stream"},
        )

    assert response.status_code == 415
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
async def test_mid_write_failure_cleans_staging_and_never_publishes_a_partial_package(
    sketch_api: SketchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_on_second_chart(path: Path, content: bytes) -> None:
        if path.name == "chart-1H.png":
            raise OSError("simulated disk failure")
        path.write_bytes(content)

    monkeypatch.setattr(sketch_store_module, "_write_member", fail_on_second_chart)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(),
            headers={"content-type": "application/zip"},
        )
        listed = await client.get("/api/v1/sketches")
        detail = await client.get(f"/api/v1/sketches/{WORKSHOP}/{SKETCH_ID}")

    assert response.status_code == 500
    assert listed.json()["count"] == 0
    assert detail.status_code == 404
    _assert_no_partial(sketch_api)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "metadata", "expected_path"),
    [
        (
            "missing instrument",
            META_YAML.replace("instrument: NQ\n", ""),
            "instrument",
        ),
        (
            "null instrument",
            META_YAML.replace("instrument: NQ", "instrument: null"),
            "instrument",
        ),
        (
            "blank instrument",
            META_YAML.replace("instrument: NQ", 'instrument: "   "'),
            "instrument",
        ),
        (
            "unknown instrument",
            META_YAML.replace("instrument: NQ", "instrument: ZZ"),
            "instrument",
        ),
        (
            "missing class",
            META_YAML.replace("asset_class: equity_index_futures\n", ""),
            "asset_class",
        ),
        (
            "null class",
            META_YAML.replace("asset_class: equity_index_futures", "asset_class: null"),
            "asset_class",
        ),
        (
            "blank class",
            META_YAML.replace("asset_class: equity_index_futures", 'asset_class: "   "'),
            "asset_class",
        ),
        (
            "unknown class",
            META_YAML.replace("asset_class: equity_index_futures", "asset_class: unknown"),
            "asset_class",
        ),
        (
            "mismatched class",
            META_YAML.replace(
                "asset_class: equity_index_futures", "asset_class: commodity_futures"
            ),
            "asset_class",
        ),
    ],
)
async def test_new_sketch_catalog_identity_is_required_and_fail_closed(
    sketch_api: SketchStore,
    case_name: str,
    metadata: str,
    expected_path: str,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sketches",
            content=_zip_bundle(meta_yaml=metadata),
            headers={"content-type": "application/zip"},
        )

    assert response.status_code == 422, case_name
    assert any(issue["path"] == expected_path for issue in response.json()["detail"]["issues"])
    _assert_no_partial(sketch_api)


def test_legacy_sketch_missing_catalog_identity_remains_readable_without_rewriting_bytes(
    sketch_store: SketchStore,
) -> None:
    """Read policy is deliberately separate from strict new-package intake policy."""
    folder = sketch_store.root / WORKSHOP / SKETCH_ID
    folder.mkdir(parents=True)
    legacy_meta = META_YAML.replace("asset_class: equity_index_futures\n", "")
    (folder / "meta.yaml").write_text(legacy_meta, encoding="utf-8")
    (folder / "INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    for filename, content in _images("legacy").items():
        (folder / filename).write_bytes(content)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}

    package = sketch_store.get(WORKSHOP, SKETCH_ID)

    assert package.meta.instrument == "NQ"
    assert package.meta.asset_class is None
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
