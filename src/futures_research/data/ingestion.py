"""Coordinate non-destructive quality reporting with canonical persistence."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from futures_research.data.models import CanonicalBar, QualityReport
from futures_research.data.quality import DataQualityChecker
from futures_research.data.storage import CanonicalStore, WriteSummary


@dataclass(frozen=True)
class IngestionResult:
    """Result of one data-module ingestion batch."""

    quality_report: QualityReport
    report_path: Path
    write_summary: WriteSummary


class DataIngestionService:
    """Run quality checks, persist canonical bars, and retain a JSON audit report."""

    def __init__(
        self,
        store: CanonicalStore,
        reports_root: Path,
        quality_checker: DataQualityChecker | None = None,
    ) -> None:
        self._store = store
        self._reports_root = reports_root
        self._quality_checker = quality_checker or DataQualityChecker()

    def ingest(
        self,
        contract_id: str,
        bars: Sequence[CanonicalBar],
        *,
        expected_timestamps: Iterable[datetime] | None = None,
        reference_bars: Sequence[CanonicalBar] | None = None,
        reference_interval: timedelta = timedelta(minutes=5),
        tick_size: float | None = None,
    ) -> IngestionResult:
        """Ingest one individual-contract batch without silently dropping suspect data."""
        wrong_contracts = {bar.contract_id for bar in bars if bar.contract_id != contract_id}
        if wrong_contracts:
            msg = f"batch contains bars for another contract: {sorted(wrong_contracts)}"
            raise ValueError(msg)

        report = self._quality_checker.check(
            contract_id,
            bars,
            expected_timestamps=expected_timestamps,
            reference_bars=reference_bars,
            reference_interval=reference_interval,
            tick_size=tick_size,
        )
        write_summary = self._store.append(bars)
        report_path = self._write_report(report)
        return IngestionResult(
            quality_report=report,
            report_path=report_path,
            write_summary=write_summary,
        )

    def _write_report(self, report: QualityReport) -> Path:
        directory = self._reports_root / self._safe_component(report.contract_id)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{report.checked_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        destination = directory / filename
        destination.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", value)
