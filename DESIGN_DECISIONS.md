# Design decisions — V0.2

## 1. Vendor archives are inputs, not our database schema

Every provider gets an adapter. Above the adapter, the system speaks canonical securities, bars, actions, features and experiments.

## 2. Ticker is not identity

HistoricalData.net explicitly documents reassigned symbols and renamed companies. We model one vendor lifecycle and map it to a stable internal `security_id`. In the full package, FIGI is the preferred source key when present; CIK/lifecycle and finally symbol+delisting-date are fallbacks.

## 3. Raw observations and archive-adjusted values have different semantics

The vendor's `adj_*` columns are recomputed through the latest row of a per-security archive. They are preserved because they are valuable for validation and ex-post return calculations, but they are stored separately from point-in-time raw bars.

This is a safety boundary, not merely organization.

## 4. Missing is not zero

The vendor explicitly guarantees blanks mean unavailable, never zero. Canonical ingestion preserves SQL NULLs. This matters for `vwap`, `transactions`, adjusted fields and event columns.

## 5. Daily volume is DOUBLE

The vendor documents fractional consolidated-tape volume beginning 2026-02-23. Storing volume as BIGINT would make the schema wrong as soon as the full current archive is ingested.

## 6. Corporate-action availability is conservative

The daily archive contains ex/effective dates but not declaration timestamps. Consequently canonical actions from this source have `availability_basis = EX_DATE_ONLY` and `safe_to_use_from = ex_date`. We will not use them as pre-event predictive information.

## 7. Full provenance is cheap, so keep it

Dataset version → source files → SHA-256 → canonical rows → features → experiments should remain traceable. The vendor's manifests make this unusually easy.

## 8. Canonical bulk storage is Parquet

DuckDB is the local query/catalogue engine. Parquet is the durable bulk format. This lets local SSD become object storage later without changing the logical model.

## 9. Pipelines rebuild derived state instead of hand-repairing it

Raw vendor data is immutable. If normalization logic changes, rebuild canonical Parquet from source and issue a new pipeline/dataset version.

## 10. We deliberately ignore minute bars in V0

The investment-hypothesis product currently needs daily resolution. The vendor sample also documents important daily/minute volume and adjustment differences. Minute data stays available as a future independent grain rather than being mixed into daily truth.
