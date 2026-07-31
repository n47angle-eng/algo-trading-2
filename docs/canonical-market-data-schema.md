# Canonical market-data schema

Status: WO-001 implementation contract. Owner-editable contract metadata lives in
`config/contracts.yaml`.

Last synchronized: 2026-07-27.

## Contract catalog identity

`config/contracts.yaml` is also the canonical controlled catalog used by P2 instrument selection,
strategy validation, P4 run eligibility, and the Nautilus adapter. Every configured root/product
symbol must expose:

- `display_name`;
- `asset_class` from the controlled MVP vocabulary;
- `currency`;
- supported `sessions`;
- the existing expiry-specific `contract_id` and execution metadata.

Initial mapping: NQ and YM are `equity_index_futures`; GC is `commodity_futures`. Frontend,
strategy parser, and adapter must not maintain a second hard-coded symbol-to-class map.

Sketches and strategies identify the root/product symbol (`NQ`). Immutable run manifests identify
the exact expiry-specific contract (`NQ-202609-CME`). A current catalog value must never be used to
rewrite historical run or result identity.

## Ownership boundary

The data module owns the canonical store. It downloads historical data through NautilusTrader's
Interactive Brokers historical client, normalizes it into this schema, and writes the result.
The schema deliberately contains no NautilusTrader- or IB-specific object encoding, so another
provider can replace IB without changing downstream research data.

NautilusTrader's `ParquetDataCatalog` is a derived cache only. It must be regenerated from this
canonical store plus the matching instrument definition; it is never the source of truth.

## Time and partitioning

- Every `ts_event` and `ingested_at` value is `timestamp[ns, tz=UTC]`.
- Session display and future calendar-aware completeness checks use the contract's
  `America/Chicago` exchange timezone.
- Bars are 1-minute, start-timestamped OHLCV records.
- Files are partitioned as `data/market/contract_id=<id>/year=<YYYY>/month=<MM>/bars.parquet`.
- `start` is inclusive and `end` is exclusive in download and read requests.
- A bounded canonical read opens only UTC month partitions intersecting `[start, end)` and applies
  its `ts_event` predicate in Arrow/Parquet before Python model materialization.

## Parquet columns

| Column | Parquet type | Meaning |
|---|---|---|
| `ts_event` | `timestamp[ns, tz=UTC]` | Start of the one-minute bar |
| `open`, `high`, `low`, `close` | `float64` | Vendor-neutral prices |
| `volume` | `int64` | Reported trade volume |
| `contract_id` | `string` | Individual-contract identifier from `contracts.yaml` |
| `source` | `string` | Source name, initially `IB` |
| `source_request_id` | `string?` | Request-level ingestion audit key |
| `ingested_at` | `timestamp[ns, tz=UTC]` | UTC ingest time |

File metadata records the schema version, UTC policy, interval, and canonical owner. Duplicate
bars use `(contract_id, ts_event)` as their key; the existing persisted bar wins so a replay cannot
silently rewrite history. The ingestion result reports discarded duplicates.

## Quality-report policy

Every ingestion runs four checks and emits a JSON report:

1. **Completeness** — duplicates and gaps versus an expected-minute set. The downloader derives
   this from the configured RTH/ETH weekday template in `America/Chicago`; fixtures and callers
   may supply an explicit set. Exchange holidays and ad-hoc early closes remain visible as
   suspicious dates for Owner adjudication until calendar overrides are added.
2. **Reasonableness** — finite positive prices, valid OHLC envelope, non-negative volume.
3. **Anomalies** — true range above a configurable multiple of prior Wilder ATR.
4. **Consistency** — optional aggregate comparison of 1-minute bars with supplied native higher-TF
   bars.

Quality flags do not silently erase bars. Suspicious dates remain auditable and are for Owner
adjudication, as required by the design baseline.
