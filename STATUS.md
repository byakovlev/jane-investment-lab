# Jane — Current State

## Goal
Build a local-first system for discovering, testing, and accumulating statistically sound investment features and strategies.

## Roadmap
Priority 1 — full historical equity ingestion and bounded research access: complete.
Priority 2 — first real investment hypothesis: pending a separately agreed task.

## Current dataset
Version: 2026-09-18-full
dataset_version_id: 9135704803770303588
Dataset status: READY
Latest ingestion: SUCCEEDED, with zero FAIL quality checks
Source files: 39,029
Raw rows: 49,649,367
Research rows: 49,209,448
Research securities: 37,829
Coverage: 2003-10-01 → 2026-09-18
Duplicate (security_id, trade_date) pairs in research: 0

## Identity / quarantine
Raw vendor data is always preserved.

Identity quarantine:
- 134 securities
- 297 files
- 439,919 raw rows excluded from research

Reasons:
- ticker-history / lifecycle ambiguity
- same FIGI assigned to files with conflicting overlapping price histories

Adjusted-history quarantine:
- 31 files
- 78,711 rows

Later goal: adjudicate quarantined identities using versioned overrides and external reference evidence.

## Important identity semantics
Jane's canonical identity is `security_id`.
External identity preference:
FIGI → CIK/symbol/lifecycle fallback.
Ticker is not identity.

`source_security_lifecycle_snapshot` contains FIGI / symbol / security_id mappings.
`security_identifier_history` contains historical ticker intervals.

## Security counts
`security` table is a global registry and currently has 38,238 rows.
Current dataset snapshot has ~37,963 unique securities before quarantine.
Research layer has 37,829 after quarantine.

Do not interpret ingestion summary `"securities": 39029` as unique securities; it currently reflects source files/lifecycles and should eventually be renamed/fixed.

## Full-ingestion work completed
- vendor archive checksum verified
- filename-map handling for case-insensitive macOS filenames
- invalid vendor-adjusted histories quarantined
- explicit lifecycle metadata matching
- ticker-history validation
- safe dataset-version retry semantics
- raw conflicting-price identity quarantine
- canonical Parquet + DuckDB warehouse
- full-scale duplicate/date validation
- `bars_daily_raw_current` preserves archival rows
- research-facing views exclude quarantined identities

Latest test count before final ingestion: 56 passed.

## Latest task — bounded research loader

`load_research_frame()` memory-usage fix completed and accepted by Boris.
The previous unrestricted full-dataframe validation was interrupted after excessive
swapping. The loader now filters and computes optional forward labels in SQL,
selects candidate security IDs before ticker resolution and feature joins, and
preserves complete selected-security histories for lookbacks and future outcomes.

The configurable `max_rows` guard defaults to 100,000. Oversized requests fail
before pandas materialization; returned results are never silently truncated.
DuckDB working memory is capped at 2 GB, with two threads and temporary disk spill.
Only the final requested columns and rows reach pandas.

Verified against the existing warehouse without re-ingestion:

- Dataset remains READY; latest ingestion SUCCEEDED with zero FAIL quality checks.
- Bounded smoke request: AAPL, 2025-01-01 through 2025-12-31, labels enabled,
  `max_rows=1000` returned 250 rows in 0.549 seconds.
- Result dataframe: 83,132 bytes; process peak RSS: 292,323,328 bytes (~279 MiB).
- The final 2025-12-31 observation retains its 21-observation forward outcome.
- Validation: 70 relevant tests passed (`test_research_frame.py`,
  `test_research_integration.py`, and `test_ingestion_integration.py`);
  `py_compile` and `git diff --check` passed.
- Regression coverage compares filtered results with small unfiltered fixture
  slices, with and without labels, across ticker changes/reuse, date boundaries,
  missing adjusted values, empty results, and guard rejection before pandas.

Remaining limits:

- Unrestricted full-archive pandas loading is intentionally guarded, not validated.
- Broad requests can still scan substantial Parquet data, compute complete
  selected-security histories, and require temporary disk space. The 2 GB setting
  limits DuckDB working memory, not total process RSS or pandas allocations.
- Increasing `max_rows` increases pandas memory requirements. This Mac has 16 GB RAM;
  continue to use bounded requests and SQL summaries for large-data validation.
- The smoke timing is one local measurement, not a cold-cache performance guarantee.

No re-ingestion is needed. Await a separately agreed task; do not begin research automatically.

## Next research task
First real hypothesis:

Buy large S&P 500 companies below their 200-week moving average when latest earnings are strong.

Start as an event study:
- 4 / 13 / 26 / 52 week forward returns
- compare with S&P 500
- compare with unconditional returns of same universe
- compare with similar stocks not meeting signal

Missing data to acquire later:
- point-in-time S&P 500 membership / market-cap ranking
- earnings announcement dates and reported results
- analyst expectations if using earnings surprise

## Session workflow

Follow the collaboration workflow in [AGENTS.md](AGENTS.md).

At start, read AGENTS.md, ROADMAP.md, and STATUS.md; check the branch and working
tree. Implement only the agreed task, validate it, report the results and
limitations, and stop for Boris's review. The next task above is recorded for
planning; it is not authorization to start it automatically.
