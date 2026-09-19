# Investment Lab POC — V0.2 real vendor ingestion

This version replaces synthetic market data with the exact free HistoricalData.net stock sample supplied for the project. The architecture is deliberately vendor-independent above the adapter layer.

## What V0.2 proves

- Verifies the vendor delivery with its own offline `verify.py` and SHA-256 manifest.
- Treats a vendor **security lifecycle**, not a ticker, as the source identity.
- Builds deterministic internal `security_id` values.
- Stores metadata/provenance in DuckDB.
- Stores bulk daily observations as partitioned ZSTD Parquet.
- Keeps **raw daily bars** physically/semantically separate from vendor `adj_*` bars.
- Extracts dividends, splits and delistings into canonical corporate actions.
- Records source-file metadata, ingestion runs and quality checks.
- Is idempotent for a given immutable vendor snapshot.
- Is designed so the full purchased package can be passed to the same ingestion command.

## Why adjusted bars are separate

HistoricalData.net adjusts each per-security file through its latest row. Therefore `adj_close` is an excellent archive-normalized value, but not literally a value an investor observed on the historical trade date. Future actions apply a common factor to earlier rows.

Investment Lab therefore makes this hard to misuse:

- `bars_daily_current` contains **only raw market observations**.
- `vendor_adjusted_daily_current` contains the vendor-adjusted series and labels its basis `VENDOR_ARCHIVE_ADJUSTED_THROUGH_SNAPSHOT`.
- future feature/backtest code must explicitly request adjusted data when it needs ex-post total-return normalization.

For point-in-time signals such as `price < $10`, the raw bar is the appropriate input.

## Run the included sample

```bash
cd investment_lab_poc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/ingest_historicaldata.py vendor_samples/historicaldata_net_sample_2022H2.zip
python scripts/query_demo.py
pytest -q
```

The free sample has 4 securities and 464 daily rows (TSLA, KO, SPY and delisted TWTR).

## When the full package is purchased

The full package adds `symbols.csv`, which the sample omits. The same adapter automatically uses it for:

- company/security name,
- security type (`CS`, `ETF`, `PFD`, ...),
- exchange,
- active/delisted/renamed lifecycle status,
- delisting date,
- CIK,
- FIGI,
- full symbol history such as `FB:2012-05-18|META:2022-06-09`.

Then run:

```bash
python scripts/ingest_historicaldata.py /path/to/purchased/archive \
  --version-label 2026-09-snapshot \
  --source-asof 2026-09-15
```

The full data remains untouched. Canonical data is written under `warehouse/canonical/`.

## Canonical storage

```text
warehouse/
├── metadata.duckdb
└── canonical/
    └── historicaldata_net/
        └── dataset_version=<stable id>/
            ├── bars_daily_raw/
            │   └── year(trade_date)=2022/*.parquet
            └── bars_daily_vendor_adjusted/
                └── year(trade_date)=2022/*.parquet
```

The exact Hive partition folder label is produced by DuckDB and is an implementation detail; query through the registered views or data-access layer.

## Important sample limitation

The free sample does **not** include `symbols.csv`. Security metadata is therefore marked `PROVISIONAL` and is inferred only far enough to exercise ingestion. Do not treat the sample's instrument type/exchange metadata as authoritative. The purchased full package resolves this automatically.

## Next milestone

Before LLM/UI work, add the first research-safe return/feature layer:

1. trading calendar / session semantics,
2. total-return calculation from raw bars + corporate actions,
3. point-in-time universe membership,
4. feature registry,
5. one hypothesis and one reproducible backtest.

# V0.3 — how to work with the project day to day

## Easiest start on a Mac

After unzipping the project, open Terminal in the project folder and run:

```bash
./start_here.command
```

The first run creates `.venv`, installs dependencies, ingests the bundled HistoricalData.net sample, runs the tests, and opens `notebooks/Hypothesis_Lab.ipynb` in JupyterLab.

If macOS blocks direct execution, run:

```bash
bash start_here.command
```

After the first setup, these are the useful commands:

```bash
make test          # check code/data invariants
make hypothesis    # run the saved 3%-for-3-days hypothesis
make experiments   # list previous experiments
make lab           # open the Hypothesis Lab notebook
```

## Your two main places

1. **To inspect code:** open the project root in JupyterLab and use `CODE_MAP.md` as the map. Production logic is ordinary Python under `src/`, not hidden in notebooks.
2. **To run hypotheses:** use `notebooks/Hypothesis_Lab.ipynb`. Durable hypothesis definitions live under `hypotheses/`.

## First hypothesis semantics

`hypotheses/three_up_days_then_next_day_up.json` means:

- universe: KO, TSLA, TWTR in the free sample (SPY excluded because it is an ETF),
- daily return: close-to-close vendor-adjusted return,
- signal: each of the latest 3 trading-day returns is strictly greater than 3%,
- signal becomes known after the third day's close,
- outcome: close-to-close return on the next trading day is strictly greater than 1%,
- comparison: signal hit rate versus the unconditional next-day >1% hit rate in the same universe/sample.

For this test we use vendor-adjusted closes only to calculate realized returns across splits/dividends. We do not use adjusted price levels as point-in-time features.

The free 2022-H2 sample is intentionally tiny. It contains no three-day streak where each day exceeds +3%, so this exact hypothesis produces zero signals. That is a valid result: the sample is good for exercising the machinery, but not for evaluating this hypothesis statistically. The full purchased archive is where this becomes meaningful.

Each run creates:

```text
results/<experiment_id>/
├── result.json      # metrics + semantics + provenance
├── events.csv       # every matching signal and outcome
└── compiled.sql     # exact query the engine executed
```

This makes every result auditable rather than just showing a chart or a headline metric.
