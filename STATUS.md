# Jane — Current State

## Goal
Build a local-first system for discovering, testing, and accumulating statistically sound investment features and strategies.

## Roadmap
Priority 1 — full historical equity ingestion: essentially complete.
Priority 2 — first real investment hypothesis: next.

## Current dataset
Version: 2026-09-18-full
dataset_version_id: 9135704803770303588
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

## Immediate checks
Confirm:
1. dataset version status = READY
2. latest ingestion has zero FAIL quality checks
3. `load_research_frame()` works successfully at full scale

Then mark Roadmap Priority 1 COMPLETE.

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
At start:
1. Read ROADMAP.md and STATUS.md.
2. Check branch + git status.
3. Continue from Immediate checks / Next research task.