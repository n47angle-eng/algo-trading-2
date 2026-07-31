"""Operator CLI for contract inspection, downloads, and immutable backtest replays."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from futures_research.backtest.persistence import ResultExporter, SqliteRunStore
from futures_research.backtest.runner import BacktestRunConfig, BacktestRunner
from futures_research.data.contracts import ContractRegistry
from futures_research.data.daily_consistency import run_daily_consistency_check
from futures_research.data.daily_download import (
    DailyDataIngestionService,
    DailyDownloadPolicy,
    DailyHistoricalDownloadRequest,
    NativeDailyDownloader,
)
from futures_research.data.download import (
    HistoricalDownloadRequest,
    IbConnectionManager,
    SegmentedHistoricalDownloader,
)
from futures_research.data.ib import IbConnectionConfig, NautilusIbHistoricalClient
from futures_research.data.ingestion import DataIngestionService
from futures_research.data.storage import CanonicalStore
from futures_research.paths import PROJECT_ROOT


def main() -> None:
    """Run the data-layer configuration inspection command."""
    parser = argparse.ArgumentParser(prog="futures-research")
    parser.add_argument(
        "--contracts-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "contracts.yaml",
        help="path to owner-editable contract metadata",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="root directory for ignored runtime market data and quality reports",
    )
    parser.add_argument("--symbol", help="configured futures symbol to download, for example NQ")
    parser.add_argument(
        "--start",
        type=_parse_utc,
        help="inclusive UTC ISO-8601 timestamp for download or backtest",
    )
    parser.add_argument(
        "--end",
        type=_parse_utc,
        help="exclusive UTC ISO-8601 timestamp for download or backtest",
    )
    parser.add_argument(
        "--use-rth",
        action="store_true",
        help="request the configured regular-hours session instead of ETH",
    )
    parser.add_argument(
        "--run-id",
        help="immutable run identifier for backtest, for example nq-20260723-eth-smoke-001",
    )
    parser.add_argument(
        "--strategy-version",
        default="trend-v0",
        help="immutable strategy version label for a backtest manifest",
    )
    parser.add_argument(
        "--session",
        choices=("eth", "rth"),
        default="eth",
        help="configured session used by the closed-bar backtest path (default: eth)",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=100_000.0,
        help="positive initial account capital for backtest (default: 100000)",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=1,
        help="positive contract quantity for backtest (default: 1)",
    )
    parser.add_argument(
        "--runs-db",
        type=Path,
        help=(
            "SQLite target for immutable backtest runs "
            "(default: <data-root>/backtests/runs.sqlite3)"
        ),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        help="result.v1 export root (default: <data-root>/backtests/results)",
    )
    parser.add_argument(
        "--skip-nautilus-replay",
        action="store_true",
        help="skip the independent Nautilus canonical-bar replay verification",
    )
    parser.add_argument(
        "--empty-session-limit",
        type=int,
        default=5,
        help=("stop a backfill after this many consecutive empty exchange sessions (default: 5)"),
    )
    parser.add_argument(
        "--result",
        type=Path,
        help="path to a result.v1 main JSON file (scorecard command)",
    )
    parser.add_argument(
        "--strategy",
        type=Path,
        help="path to a strategy.v1 YAML file (parse-strategy command)",
    )
    parser.add_argument(
        "command",
        choices=(
            "contracts",
            "download",
            "backfill",
            "download-daily",
            "check-daily-consistency",
            "backtest",
            "scorecard",
            "parse-strategy",
            "migrate-run-index",
        ),
        help="operator command to run",
    )
    args = parser.parse_args()
    registry = ContractRegistry.from_yaml(args.contracts_config)
    if args.command == "contracts":
        for symbol, contract in registry.contracts.items():
            print(f"{symbol}: {contract.contract_id} expiry={contract.expiry.isoformat()}")
        return

    if args.command == "download":
        if args.symbol is None or args.start is None or args.end is None:
            parser.error("download requires --symbol, --start, and --end")
        asyncio.run(_download(args, registry))
        return
    if args.command == "backfill":
        if args.symbol is None:
            parser.error("backfill requires --symbol")
        asyncio.run(_backfill(args, registry))
        return
    if args.command == "download-daily":
        if args.symbol is None:
            parser.error("download-daily requires --symbol")
        asyncio.run(_download_daily(args, registry))
        return
    if args.command == "check-daily-consistency":
        if args.symbol is None:
            parser.error("check-daily-consistency requires --symbol")
        _check_daily_consistency(args, registry)
        return
    if args.command == "scorecard":
        if args.result is None:
            parser.error("scorecard requires --result path/to/result.json")
        _scorecard(args)
        return
    if args.command == "parse-strategy":
        if args.strategy is None:
            parser.error("parse-strategy requires --strategy path/to/strategy.yaml")
        _parse_strategy(args)
        return
    if args.command == "migrate-run-index":
        runs_database = args.runs_db or args.data_root / "backtests" / "runs.sqlite3"
        summary = SqliteRunStore(runs_database).migrate_run_indexes(registry=registry)
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
        return
    if args.symbol is None or args.start is None or args.end is None or args.run_id is None:
        parser.error("backtest requires --symbol, --start, --end, and --run-id")
    _backtest(args, registry)


def _parse_utc(value: str) -> datetime:
    """Parse an operator-supplied ISO-8601 instant into canonical UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone, for example 'Z'")
    return parsed.astimezone(UTC)


async def _download(args: argparse.Namespace, registry: ContractRegistry) -> None:
    """Download one configured individual contract and print its immutable audit locations."""
    contract = registry.by_symbol(args.symbol)
    request = HistoricalDownloadRequest(
        contract=contract,
        start=args.start,
        end=args.end,
        use_rth=args.use_rth,
    )
    client = NautilusIbHistoricalClient(IbConnectionConfig.from_environment())
    connection = IbConnectionManager(client)
    ingestion = DataIngestionService(
        CanonicalStore(args.data_root / "market"),
        args.data_root / "quality-reports",
    )
    summary = await SegmentedHistoricalDownloader(connection, client, ingestion).download(request)
    print(
        json.dumps(
            {
                "request_id": summary.request_id,
                "contract_id": contract.contract_id,
                "rows_received": summary.rows_received,
                "segments": [
                    {
                        "start": segment.start.isoformat(),
                        "end": segment.end.isoformat(),
                        "rows_received": result.write_summary.rows_received,
                        "rows_stored": result.write_summary.rows_stored,
                        "duplicates_discarded": result.write_summary.duplicates_discarded,
                        "quality_has_errors": result.quality_report.has_errors,
                        "quality_issue_count": len(result.quality_report.issues),
                        "quality_report": str(result.report_path),
                    }
                    for segment, result in zip(
                        summary.segments, summary.ingestion_results, strict=True
                    )
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


async def _backfill(args: argparse.Namespace, registry: ContractRegistry) -> None:
    """Backfill one individual contract to the provider's oldest discoverable 1m session."""
    contract = registry.by_symbol(args.symbol)
    client = NautilusIbHistoricalClient(IbConnectionConfig.from_environment())
    connection = IbConnectionManager(client)
    ingestion = DataIngestionService(
        CanonicalStore(args.data_root / "market"),
        args.data_root / "quality-reports",
    )
    summary = await SegmentedHistoricalDownloader(connection, client, ingestion).backfill(
        contract,
        as_of=args.end or datetime.now(UTC),
        use_rth=args.use_rth,
        max_consecutive_empty_sessions=args.empty_session_limit,
    )
    print(
        json.dumps(
            {
                "contract_id": summary.contract_id,
                "session_name": summary.session_name,
                "as_of": summary.as_of.isoformat(),
                "latest_completed_trading_date": summary.latest_completed_trading_date.isoformat(),
                "earliest_available_timestamp": (
                    summary.earliest_available_timestamp.isoformat()
                    if summary.earliest_available_timestamp is not None
                    else None
                ),
                "latest_available_end": (
                    summary.latest_available_end.isoformat()
                    if summary.latest_available_end is not None
                    else None
                ),
                "available_session_count": summary.available_session_count,
                "empty_session_dates": [item.isoformat() for item in summary.empty_session_dates],
                "stop_reason": summary.stop_reason,
                "rows_received": summary.rows_received,
                "rows_stored": summary.rows_stored,
                "duplicates_discarded": summary.duplicates_discarded,
                "quality_reports": [str(path) for path in summary.quality_report_paths],
            },
            indent=2,
            sort_keys=True,
        )
    )


async def _download_daily(args: argparse.Namespace, registry: ContractRegistry) -> None:
    """Download or max-depth backfill native IB daily bars into ``data/market-daily/``.

    Range mode: supply both ``--start`` and ``--end``.
    Backfill mode: omit ``--start``; optional ``--end`` is ``as_of`` (default: now).
    """
    if args.start is not None and args.end is None:
        raise SystemExit("download-daily range mode requires both --start and --end")

    contract = registry.by_symbol(args.symbol)
    client = NautilusIbHistoricalClient(IbConnectionConfig.from_environment())
    connection = IbConnectionManager(client)
    store = CanonicalStore(args.data_root / "market-daily")
    ingestion = DailyDataIngestionService(store, args.data_root / "quality-reports-daily")
    downloader = NativeDailyDownloader(
        connection,
        client,
        ingestion,
        store,
        policy=DailyDownloadPolicy(),
    )
    session_name = "rth" if args.use_rth else "eth"

    if args.start is not None and args.end is not None:
        summary = await downloader.download(
            DailyHistoricalDownloadRequest(
                contract=contract,
                start=args.start,
                end=args.end,
                use_rth=args.use_rth,
                session_name=session_name,
            )
        )
        stored = store.read(contract.contract_id, start=args.start, end=args.end)
        print(
            json.dumps(
                {
                    "mode": "range",
                    "request_id": summary.request_id,
                    "contract_id": contract.contract_id,
                    "source": "ib_native_daily",
                    "session_name": session_name,
                    "rows_received": summary.rows_received,
                    "rows_stored": summary.rows_stored,
                    "unique_bar_count": len(stored),
                    "earliest_available_timestamp": (
                        min(bar.timestamp for bar in stored).isoformat() if stored else None
                    ),
                    "latest_available_timestamp": (
                        max(bar.timestamp for bar in stored).isoformat() if stored else None
                    ),
                    "segments": [
                        {
                            "start": segment.start.isoformat(),
                            "end": segment.end.isoformat(),
                            "rows_received": result.write_summary.rows_received,
                            "rows_stored": result.write_summary.rows_stored,
                            "duplicates_discarded": result.write_summary.duplicates_discarded,
                            "quality_has_errors": result.quality_report.has_errors,
                            "thin_daily_count": sum(
                                1
                                for issue in result.quality_report.issues
                                if issue.code == "thin_daily"
                            ),
                            "quality_report": str(result.report_path),
                        }
                        for segment, result in zip(
                            summary.segments, summary.ingestion_results, strict=True
                        )
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    backfill_summary = await downloader.backfill(
        contract,
        as_of=args.end or datetime.now(UTC),
        use_rth=args.use_rth,
        session_name=session_name,
        max_consecutive_empty_segments=args.empty_session_limit,
    )
    print(
        json.dumps(
            {
                "mode": "backfill",
                "contract_id": backfill_summary.contract_id,
                "source": "ib_native_daily",
                "session_name": backfill_summary.session_name,
                "as_of": backfill_summary.as_of.isoformat(),
                "latest_completed_trading_date": (
                    backfill_summary.latest_completed_trading_date.isoformat()
                ),
                "earliest_available_timestamp": (
                    backfill_summary.earliest_available_timestamp.isoformat()
                    if backfill_summary.earliest_available_timestamp is not None
                    else None
                ),
                "latest_available_end": (
                    backfill_summary.latest_available_end.isoformat()
                    if backfill_summary.latest_available_end is not None
                    else None
                ),
                "available_segment_count": backfill_summary.available_segment_count,
                "empty_segment_starts": [
                    item.isoformat() for item in backfill_summary.empty_segment_starts
                ],
                "stop_reason": backfill_summary.stop_reason,
                "rows_received": backfill_summary.rows_received,
                "rows_stored": backfill_summary.rows_stored,
                "duplicates_discarded": backfill_summary.duplicates_discarded,
                "unique_bar_count": backfill_summary.unique_bar_count,
                "thin_daily_count": backfill_summary.thin_daily_count,
                "quality_reports": [str(path) for path in backfill_summary.quality_report_paths],
            },
            indent=2,
            sort_keys=True,
        )
    )


def _check_daily_consistency(args: argparse.Namespace, registry: ContractRegistry) -> None:
    """Compare native daily bars to 1m-complete-session synthetic dailies (WO-003b 3b-2)."""
    contract = registry.by_symbol(args.symbol)
    session_name = "rth" if args.use_rth else "eth"
    result = run_daily_consistency_check(
        contract,
        native_store_root=args.data_root / "market-daily",
        minute_store_root=args.data_root / "market",
        reports_root=args.data_root / "quality-reports-daily",
        session_name=session_name,
        minute_start=args.start,
        minute_end=args.end,
    )
    checks = result.quality_report.checks
    print(
        json.dumps(
            {
                "contract_id": contract.contract_id,
                "session_name": session_name,
                "complete_1m_session_count": result.complete_1m_session_count,
                "native_in_expected_count": result.native_in_expected_count,
                "matched_ohlc_count": result.matched_ohlc_count,
                "matched_ohl_count": result.matched_ohl_count,
                "ohlc_mismatch_count": result.ohlc_mismatch_count,
                "missing_native_count": result.missing_native_count,
                "native_only_outside_expected_count": result.native_only_outside_expected_count,
                "compared_days": int(checks.get("compared_days", "0")),
                "field_match_open": int(checks.get("field_match_open", "0")),
                "field_match_high": int(checks.get("field_match_high", "0")),
                "field_match_low": int(checks.get("field_match_low", "0")),
                "field_match_close": int(checks.get("field_match_close", "0")),
                "close_diff_ticks_median": result.close_diff_ticks_median,
                "close_diff_ticks_p95": result.close_diff_ticks_p95,
                "close_diff_sample_size": result.close_diff_sample_size,
                "ohl_miss_count": int(checks.get("ohl_miss_count", "0")),
                "ohl_miss_dates": [
                    item for item in checks.get("ohl_miss_dates", "").split(",") if item
                ],
                "quality_has_errors": result.quality_report.has_errors,
                "issue_codes": sorted({issue.code for issue in result.quality_report.issues}),
                "quality_report": str(result.report_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _parse_strategy(args: argparse.Namespace) -> None:
    """Validate strategy.v1 and print the mapped StrategySpec summary."""
    from futures_research.strategy import StrategyValidationError, load_strategy_file

    try:
        parsed = load_strategy_file(
            args.strategy,
            contracts_config=args.contracts_config,
        )
    except StrategyValidationError as exc:
        print(exc.format_report())
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "source": str(args.strategy),
                "schema": parsed.document.schema_name,
                "name": parsed.document.meta.name,
                "based_on_sketch": parsed.document.meta.based_on_sketch,
                "based_on_sketch_origin": parsed.document.meta.based_on_sketch_origin,
                "based_on_insights": parsed.document.meta.based_on_insights,
                "spec": parsed.spec.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )


def _scorecard(args: argparse.Namespace) -> None:
    """Enrich a result.v1 main file with WO-004 core scorecard + opportunity funnel."""
    from futures_research.backtest.scorecard import enrich_result_file

    enriched = enrich_result_file(args.result, write=True)
    funnel_obj = enriched.get("funnel")
    funnel: dict[str, object] = funnel_obj if isinstance(funnel_obj, dict) else {}
    scorecard_obj = enriched.get("scorecard")
    scorecard: list[object] = scorecard_obj if isinstance(scorecard_obj, list) else []
    metrics_obj = enriched.get("metrics")
    metrics: dict[str, object] = metrics_obj if isinstance(metrics_obj, dict) else {}
    print(
        json.dumps(
            {
                "result": str(args.result),
                "trade_count": metrics.get("trade_count"),
                "net_r": metrics.get("net_r"),
                "expectancy_r": metrics.get("expectancy_r"),
                "funnel": funnel,
                "scorecard": scorecard,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _backtest(args: argparse.Namespace, registry: ContractRegistry) -> None:
    """Run the approved closed-bar full path and print the immutable artifact locations."""
    contract = registry.by_symbol(args.symbol)
    runs_database = args.runs_db or args.data_root / "backtests" / "runs.sqlite3"
    results_root = args.results_root or args.data_root / "backtests" / "results"
    artifacts = BacktestRunner(
        canonical_store=CanonicalStore(args.data_root / "market"),
        daily_canonical_store=CanonicalStore(args.data_root / "market-daily"),
        run_store=SqliteRunStore(runs_database),
        result_exporter=ResultExporter(results_root),
        quality_reports_root=args.data_root / "quality-reports",
    ).run(
        contract=contract,
        config=BacktestRunConfig(
            run_id=args.run_id,
            strategy_version=args.strategy_version,
            session_name=args.session,
            range_start=args.start,
            range_end=args.end,
            initial_capital=args.initial_capital,
            quantity=args.quantity,
            verify_nautilus_replay=not args.skip_nautilus_replay,
        ),
    )
    print(
        json.dumps(
            {
                "run_id": artifacts.manifest.run_id,
                "contract_id": artifacts.manifest.contract_id,
                "range_start": artifacts.manifest.range_start.isoformat(),
                "range_end": artifacts.manifest.range_end.isoformat(),
                "raw_bar_count": artifacts.raw_bar_count,
                "admitted_bar_count": artifacts.admitted_bar_count,
                "warmup_bar_count": artifacts.warmup_bar_count,
                "entry_bar_count": artifacts.entry_bar_count,
                "nautilus_replay_iterations": artifacts.nautilus_replay_iterations,
                "calibration": (
                    artifacts.manifest.calibration.model_dump(mode="json", by_alias=True)
                    if artifacts.manifest.calibration is not None
                    else None
                ),
                "metrics": artifacts.result.metrics.model_dump(mode="json"),
                "event_count": len(artifacts.result.event_log),
                "warnings": list(artifacts.result.warnings),
                "result_file": str(artifacts.exported.result_path),
                "trades_file": str(artifacts.exported.trades_path),
                "equity_file": str(artifacts.exported.equity_curve_path),
                "events_file": str(artifacts.exported.events_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
