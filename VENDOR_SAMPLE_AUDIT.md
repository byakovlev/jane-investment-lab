# HistoricalData.net sample audit

Source: user-supplied free HistoricalData.net sample, 2022 H2.

## Delivery verification

The vendor's included verifier passes in strict extract mode:

- 8/8 data files validated
- root manifest validated
- SHA-256 checks match
- vendor row invariants pass
- split/dividend adjustment chains are independently re-derived by `verify.py`

## Daily subset used by Investment Lab V0.2

| Lifecycle | Rows | First bar | Last bar | Dividends | Splits | Note |
|---|---:|---|---|---:|---:|---|
| KO | 127 | 2022-07-01 | 2022-12-30 | 2 | 0 | cash dividends |
| SPY | 127 | 2022-07-01 | 2022-12-30 | 2 | 0 | ETF distributions |
| TSLA | 127 | 2022-07-01 | 2022-12-30 | 0 | 1 | 3-for-1 split |
| TWTR | 83 | 2022-07-01 | 2022-10-27 | 0 | 0 | delisted 2022-10-31 |
| **Total** | **464** | | | **4** | **1** | plus one delisting lifecycle event |

Minute files are deliberately not ingested in V0.

## Data-model consequences confirmed by the sample/README

1. **Lifecycle != ticker.** A ticker can be reassigned; delisted lives are separate files. The full package's `symbols.csv` supplies CIK, FIGI and symbol history.
2. **Raw != adjusted semantics.** Per-security `adj_*` values are adjusted through the latest archive row. We store them separately from raw bars.
3. **Blank != zero.** The canonical parser preserves blanks as NULL.
4. **Volume cannot be BIGINT forever.** The vendor documents fractional tape volume beginning 2026-02-23, so canonical volume is DOUBLE.
5. **Delisting date != last trading date.** TWTR demonstrates this directly: last bar 2022-10-27, delisting 2022-10-31.
6. **Corporate-action announcement dates are not present.** Dividend/split events from this source are conservatively usable only from the ex/effective date onward for predictive research.
7. **Vendor manifests are useful durable provenance.** The adapter fingerprints only the daily collection plus `symbols.csv`, i.e. exactly the inputs consumed by this dataset.

## Remaining check after purchase

The free sample deliberately omits `symbols.csv`, so names, instrument types, exchanges and stable external identifiers cannot yet be tested end-to-end. The full-purchase ingestion should be treated as successful only when:

- zero lifecycle rows are `PROVISIONAL`,
- all daily files map to exactly one security lifecycle,
- CIK/FIGI/symbol histories parse without collisions,
- active/delisted/renamed cases pass dedicated tests.
