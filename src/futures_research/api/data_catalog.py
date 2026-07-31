"""P3 data-plane catalog: coverage, quality reports, owner blacklist (WO-006 / 6-4)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from futures_research.data.contracts import ContractRegistry, ContractSpec
from futures_research.data.coverage import (
    CatalogCoverageMetadata,
    ContractCoverageSnapshot,
    build_contract_coverage_snapshot,
    read_contract_catalog_metadata,
)
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT

Decision = Literal["exclude", "trust"]


@dataclass(frozen=True, slots=True)
class DataPaths:
    """Filesystem roots for market data, quality reports, and owner decisions."""

    data_root: Path

    @property
    def market_root(self) -> Path:
        return self.data_root / "market"

    @property
    def daily_market_root(self) -> Path:
        return self.data_root / "market-daily"

    @property
    def quality_root(self) -> Path:
        return self.data_root / "quality-reports"

    @property
    def blacklist_path(self) -> Path:
        return self.data_root / "blacklists" / "owner-excluded.v1.json"

    @property
    def download_jobs_root(self) -> Path:
        return self.data_root / "jobs" / "downloads"


def default_data_paths() -> DataPaths:
    return DataPaths(data_root=PROJECT_ROOT / "data")


def list_coverage(
    paths: DataPaths | None = None,
    *,
    contracts_config: Path | None = None,
    view: Literal["catalog", "full"] = "full",
) -> dict[str, Any]:
    """Return configured-contract identity or the exact full P3 coverage document."""
    if view not in {"catalog", "full"}:
        msg = "view must be catalog|full"
        raise ValueError(msg)
    resolved = paths or default_data_paths()
    registry = ContractRegistry.from_yaml(
        contracts_config or (PROJECT_ROOT / "config" / "contracts.yaml")
    )
    store = CanonicalStore(resolved.market_root)
    native_daily_store = (
        CanonicalStore(resolved.daily_market_root) if view == "full" else None
    )
    rows: list[dict[str, Any]] = []
    for symbol, contract in sorted(registry.contracts.items()):
        roll = sorted(contract.roll_blackout_dates())
        owner = _owner_entries_for_contract(resolved, contract.contract_id)
        issue_summary = _latest_quality_summary(resolved, contract.contract_id)
        metadata: CatalogCoverageMetadata | ContractCoverageSnapshot
        if view == "catalog":
            metadata = read_contract_catalog_metadata(store, contract.contract_id)
            trading_day_coverage: dict[str, Any] | None = None
            native_daily_coverage: dict[str, Any] | None = None
        else:
            if native_daily_store is None:
                raise RuntimeError("full coverage requires the native daily store")
            snapshot = build_contract_coverage_snapshot(
                contract,
                minute_store=store,
                native_daily_store=native_daily_store,
                owner_entries=owner,
            )
            metadata = snapshot
            trading_day_coverage = snapshot.trading_day_coverage
            native_daily_coverage = snapshot.native_daily_coverage
        first_iso = (
            metadata.first_timestamp.isoformat().replace("+00:00", "Z")
            if metadata.first_timestamp is not None
            else None
        )
        last_iso = (
            metadata.last_timestamp.isoformat().replace("+00:00", "Z")
            if metadata.last_timestamp is not None
            else None
        )
        row = {
            "symbol": symbol,
            "contract_id": contract.contract_id,
            "display_name": contract.display_name,
            "asset_class": contract.asset_class,
            "currency": contract.currency,
            "sessions_available": sorted(contract.sessions),
            "partition_count": metadata.partition_count,
            "bar_count": metadata.bar_count,
            "first_timestamp": first_iso,
            "last_timestamp": last_iso,
            "roll_blackout_dates": [d.isoformat() for d in roll],
            "owner_excluded_dates": [
                e["trading_date"] for e in owner if e.get("decision") == "exclude"
            ],
            "quality": issue_summary,
        }
        if trading_day_coverage is not None and native_daily_coverage is not None:
            row["trading_day_coverage"] = trading_day_coverage
            row["native_daily_coverage"] = native_daily_coverage
        rows.append(row)
    return {"schema": "data_coverage.v1", "count": len(rows), "contracts": rows}


def list_quality_reports(
    paths: DataPaths | None = None,
    *,
    contract_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List recent quality report JSON files (newest first)."""
    resolved = paths or default_data_paths()
    root = resolved.quality_root
    if not root.is_dir():
        return {"schema": "quality_report_list.v1", "count": 0, "reports": []}
    files: list[Path] = []
    if contract_id:
        files = sorted((root / contract_id).glob("*.json"), reverse=True)
    else:
        for sub in sorted(root.iterdir()):
            if sub.is_dir():
                files.extend(sub.glob("*.json"))
        files = sorted(files, key=lambda p: p.name, reverse=True)
    reports: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        issues_raw = payload.get("issues")
        issues: list[Any] = issues_raw if isinstance(issues_raw, list) else []
        error_count = 0
        for issue in issues:
            if isinstance(issue, dict) and issue.get("severity") == "error":
                error_count += 1
        reports.append(
            {
                "report_id": f"{path.parent.name}/{path.name}",
                "contract_id": payload.get("contract_id") or path.parent.name,
                "checked_at": payload.get("checked_at"),
                "total_bars": payload.get("total_bars"),
                "issue_count": len(issues),
                "error_count": error_count,
                "path": str(path.relative_to(resolved.data_root)).replace("\\", "/"),
            }
        )
    return {"schema": "quality_report_list.v1", "count": len(reports), "reports": reports}


def get_quality_report(report_id: str, paths: DataPaths | None = None) -> dict[str, Any]:
    """Load one quality report by ``contract_id/filename.json``."""
    resolved = paths or default_data_paths()
    # Prevent path traversal
    if ".." in report_id or report_id.startswith(("/", "\\")):
        msg = f"invalid report_id: {report_id}"
        raise LookupError(msg)
    path = (resolved.quality_root / report_id).resolve()
    try:
        path.relative_to(resolved.quality_root.resolve())
    except ValueError as exc:
        msg = "report path escapes quality root"
        raise LookupError(msg) from exc
    if not path.is_file():
        msg = f"quality report not found: {report_id}"
        raise LookupError(msg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = "quality report must be a JSON object"
        raise ValueError(msg)
    return payload


def list_owner_blacklist(paths: DataPaths | None = None) -> dict[str, Any]:
    """Return owner exclude/trust decisions (separate from roll blackout)."""
    resolved = paths or default_data_paths()
    doc = _load_blacklist_doc(resolved)
    return doc


def upsert_owner_blacklist_entry(
    *,
    contract_id: str,
    trading_date: str,
    decision: Decision,
    note: str = "",
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """Record an owner quality adjudication for one trading date."""
    resolved = paths or default_data_paths()
    # Validate date
    date.fromisoformat(trading_date)
    if decision not in ("exclude", "trust"):
        msg = "decision must be exclude|trust"
        raise ValueError(msg)
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    known = {c.contract_id for c in registry.contracts.values()}
    if contract_id not in known:
        msg = f"unknown contract_id: {contract_id}"
        raise LookupError(msg)

    doc = _load_blacklist_doc(resolved)
    entries: list[dict[str, Any]] = list(doc.get("entries") or [])
    remaining = [
        e
        for e in entries
        if not (
            isinstance(e, dict)
            and e.get("contract_id") == contract_id
            and e.get("trading_date") == trading_date
        )
    ]
    remaining.append(
        {
            "contract_id": contract_id,
            "trading_date": trading_date,
            "decision": decision,
            "note": note,
            "decided_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    )
    remaining.sort(key=lambda e: (str(e.get("contract_id")), str(e.get("trading_date"))))
    doc = {
        "schema": "owner_blacklist.v1",
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entries": remaining,
    }
    _write_json_atomic(resolved.blacklist_path, doc)
    return doc


def enqueue_download_job(
    *,
    symbol: str,
    start: str,
    end: str,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """Record a download request job (background worker may process later).

    6-4 does not require live IB success for acceptance; the job is durable and
    pollable. A worker stub marks it completed when IB is unavailable so UI flow
    can be demonstrated; real download remains CLI-primary until IB adapter is
    wired into the API process.
    """
    resolved = paths or default_data_paths()
    registry = ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
    try:
        contract: ContractSpec = registry.by_symbol(symbol)
    except KeyError as exc:
        msg = f"unknown symbol: {symbol}"
        raise LookupError(msg) from exc
    job_id = f"dl-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    job = {
        "schema": "download_job.v1",
        "job_id": job_id,
        "symbol": symbol,
        "contract_id": contract.contract_id,
        "start": start,
        "end": end,
        "status": "queued",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "message": "queued (process via CLI or future worker; API records intent)",
    }
    path = resolved.download_jobs_root / f"{job_id}.json"
    _write_json_atomic(path, job)
    return job


def list_download_jobs(paths: DataPaths | None = None, *, limit: int = 20) -> dict[str, Any]:
    resolved = paths or default_data_paths()
    root = resolved.download_jobs_root
    if not root.is_dir():
        return {"schema": "download_job_list.v1", "count": 0, "jobs": []}
    jobs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True)[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            jobs.append(payload)
    return {"schema": "download_job_list.v1", "count": len(jobs), "jobs": jobs}


def _latest_quality_summary(paths: DataPaths, contract_id: str) -> dict[str, Any]:
    directory = paths.quality_root / contract_id
    if not directory.is_dir():
        return {"report_count": 0, "latest_error_count": 0, "latest_report_id": None}
    files = sorted(directory.glob("*.json"), reverse=True)
    if not files:
        return {"report_count": 0, "latest_error_count": 0, "latest_report_id": None}
    latest = files[0]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "report_count": len(files),
            "latest_error_count": None,
            "latest_report_id": f"{contract_id}/{latest.name}",
        }
    issues_raw = payload.get("issues") if isinstance(payload, dict) else None
    issues_list: list[Any] = issues_raw if isinstance(issues_raw, list) else []
    errors = 0
    for issue in issues_list:
        if isinstance(issue, dict) and issue.get("severity") == "error":
            errors += 1
    return {
        "report_count": len(files),
        "latest_error_count": errors,
        "latest_report_id": f"{contract_id}/{latest.name}",
    }


def _owner_entries_for_contract(paths: DataPaths, contract_id: str) -> list[dict[str, Any]]:
    doc = _load_blacklist_doc(paths)
    entries_raw = doc.get("entries")
    entries: list[Any] = entries_raw if isinstance(entries_raw, list) else []
    return [e for e in entries if isinstance(e, dict) and e.get("contract_id") == contract_id]


def _load_blacklist_doc(paths: DataPaths) -> dict[str, Any]:
    path = paths.blacklist_path
    if not path.is_file():
        return {"schema": "owner_blacklist.v1", "updated_at": None, "entries": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"schema": "owner_blacklist.v1", "updated_at": None, "entries": []}
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
