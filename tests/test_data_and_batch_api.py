"""WO-006 / 6-4: data coverage/blacklist + batch queue APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from futures_research.api import batch_queue as batch_queue_mod
from futures_research.api import data_catalog
from futures_research.api.batch_queue import BatchQueue, BatchRecord, RunJob
from futures_research.api.data_catalog import (
    DataPaths,
    enqueue_download_job,
    list_coverage,
    upsert_owner_blacklist_entry,
)
from futures_research.api.main import app
from futures_research.backtest.runner import DayProgressSnapshot
from futures_research.data.contracts import ContractRegistry
from futures_research.paths import PROJECT_ROOT


def test_list_coverage_includes_all_configured_catalog_rows_even_without_partitions(
    tmp_path: Path,
) -> None:
    payload = list_coverage(DataPaths(data_root=tmp_path / "data"))
    assert payload["schema"] == "data_coverage.v1"
    assert payload["count"] == 3
    symbols = {row["symbol"] for row in payload["contracts"]}
    assert symbols == {"NQ", "YM", "GC"}
    nq = next(row for row in payload["contracts"] if row["symbol"] == "NQ")
    assert nq["contract_id"] == "NQ-202609-CME"
    assert nq["display_name"] == "E-mini Nasdaq-100"
    assert nq["asset_class"] == "equity_index_futures"
    assert nq["currency"] == "USD"
    assert nq["sessions_available"] == ["eth", "rth"]
    assert nq["partition_count"] == nq["bar_count"] == 0
    assert nq["trading_day_coverage"]["status"] == "known"
    assert nq["native_daily_coverage"]["status"] == "known"

    legacy_keys = {
        "symbol",
        "contract_id",
        "display_name",
        "asset_class",
        "currency",
        "sessions_available",
        "partition_count",
        "bar_count",
        "first_timestamp",
        "last_timestamp",
        "roll_blackout_dates",
        "owner_excluded_dates",
        "quality",
    }
    legacy_view = {key: nq[key] for key in legacy_keys}
    assert legacy_view["symbol"] == "NQ"
    assert legacy_view["bar_count"] == 0
    assert set(legacy_view) == legacy_keys


def test_owner_blacklist_roundtrip(tmp_path: Path) -> None:
    paths = DataPaths(data_root=tmp_path)
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract_id = registry.by_symbol("NQ").contract_id
    doc = upsert_owner_blacklist_entry(
        contract_id=contract_id,
        trading_date="2026-07-21",
        decision="exclude",
        note="test holiday gap",
        paths=paths,
    )
    assert doc["schema"] == "owner_blacklist.v1"
    assert any(
        e["trading_date"] == "2026-07-21" and e["decision"] == "exclude" for e in doc["entries"]
    )
    assert paths.blacklist_path.is_file()


@pytest.mark.parametrize("timezone_name", ["Pacific/Kiritimati", "America/Adak"])
def test_trading_date_label_is_identical_in_opposite_timezones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
) -> None:
    """A trading date is a label; only ``decided_at`` is a timezone-aware instant."""
    monkeypatch.setenv("TZ", timezone_name)
    paths = DataPaths(data_root=tmp_path / timezone_name.replace("/", "-"))
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract_id = registry.by_symbol("NQ").contract_id

    document = upsert_owner_blacklist_entry(
        contract_id=contract_id,
        trading_date="2026-07-21",
        decision="exclude",
        paths=paths,
    )

    assert document["entries"][0]["trading_date"] == "2026-07-21"
    assert document["entries"][0]["decided_at"].endswith("Z")


def test_enqueue_download_job(tmp_path: Path) -> None:
    paths = DataPaths(data_root=tmp_path)
    job = enqueue_download_job(
        symbol="NQ",
        start="2026-05-01T00:00:00Z",
        end="2026-05-02T00:00:00Z",
        paths=paths,
    )
    assert job["status"] == "queued"
    assert (paths.download_jobs_root / f"{job['job_id']}.json").is_file()


@pytest.mark.asyncio
async def test_data_coverage_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data_catalog,
        "default_data_paths",
        lambda: DataPaths(data_root=tmp_path / "data"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/data/coverage")
    assert response.status_code == 200
    assert response.json()["schema"] == "data_coverage.v1"
    row = next(row for row in response.json()["contracts"] if row["symbol"] == "GC")
    assert row["display_name"] == "Gold"
    assert row["asset_class"] == "commodity_futures"


@pytest.mark.asyncio
async def test_coverage_endpoint_fails_closed_for_invalid_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalog parse error must not return a plausible-looking partial mapping."""
    config_root = tmp_path / "project"
    config_dir = config_root / "config"
    config_dir.mkdir(parents=True)
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "contracts.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    raw["contracts"]["NQ"]["asset_class"] = "bad-class"
    (config_dir / "contracts.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", config_root)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/data/coverage")

    assert response.status_code == 503
    assert "partial" in response.json()["detail"]


@pytest.mark.asyncio
async def test_blacklist_and_download_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DataPaths(data_root=tmp_path)
    monkeypatch.setattr(
        "futures_research.api.data_catalog.default_data_paths",
        lambda: paths,
    )
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    contract_id = registry.by_symbol("GC").contract_id
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/data/blacklist",
            json={
                "contract_id": contract_id,
                "trading_date": "2026-07-21",
                "decision": "exclude",
                "note": "ui test",
            },
        )
        assert post.status_code == 200, post.text
        get = await client.get("/api/v1/data/blacklist")
        assert get.status_code == 200
        assert any(e["trading_date"] == "2026-07-21" for e in get.json()["entries"])

        dl = await client.post(
            "/api/v1/data/download",
            json={
                "symbol": "NQ",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-02T00:00:00Z",
            },
        )
        assert dl.status_code == 200
        assert dl.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_batch_submit_runs_to_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI batch worker marks jobs completed (runner execution stubbed)."""
    queue = BatchQueue(
        data_root=tmp_path,
        clock=lambda: datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
    )

    def fake_execute(self: BatchQueue, batch: BatchRecord, job: RunJob) -> None:
        job.result_path = str(tmp_path / f"{job.run_id}.json")
        Path(job.result_path).write_text("{}", encoding="utf-8")
        self._record_day_progress(
            batch.batch_id,
            job.job_id,
            DayProgressSnapshot(
                current_trading_date=date(2026, 5, 7),
                processed_trading_date_count=1,
                total_trading_date_count=1,
                trade_count=0,
                realized_net_pnl_usd=0.0,
                realized_net_r=0.0,
                reported_at=datetime(2026, 7, 27, 8, 5, tzinfo=UTC),
            ),
        )

    monkeypatch.setattr(BatchQueue, "_execute_job", fake_execute)
    monkeypatch.setattr(batch_queue_mod, "_GLOBAL_QUEUE", queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submit = await client.post(
            "/api/v1/batches/submit",
            json={
                "symbols": ["NQ", "GC"],
                "strategy_versions": ["trend-v0"],
                "range_start": "2026-05-06T22:00:00Z",
                "range_end": "2026-05-08T21:00:00Z",
                "validation_run": True,
                "skip_nautilus_replay": True,
            },
        )
        assert submit.status_code == 200, submit.text
        body = submit.json()
        assert body["summary"]["total"] == 2
        batch_id = body["batch_id"]

        final = None
        for _ in range(100):
            status = await client.get(f"/api/v1/batches/jobs/{batch_id}")
            assert status.status_code == 200
            final = status.json()
            if final["status"] in {"completed", "failed", "partial"}:
                break
            await asyncio.sleep(0.02)

    assert final is not None
    assert final["status"] == "completed"
    assert final["summary"]["completed"] == 2
