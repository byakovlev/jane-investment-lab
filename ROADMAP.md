# Jane Investment Lab — Roadmap

## Goal

Build a system for discovering, testing, and accumulating statistically sound
investment features and strategies.

It should eventually be easy enough for non-technical users to use through a web UI.

## Core principles

- Point-in-time data only
- Survivorship-safe universes
- No look-ahead leakage
- Versioned feature definitions
- Reproducible experiments
- Out-of-sample / walk-forward validation
- Track failed ideas as well as successful ones
- Control for repeated hypothesis testing
- Research engine independent of notebook, UI, and user
- Purchased data remains local/server-side and outside GitHub
- Preserve raw data; quarantine ambiguous research data

---

# Current priorities

## 1. Finish full historical equity ingestion
Status: COMPLETE — ingestion and bounded research access

Completed:

- verified the full vendor archive (~49.65M daily rows)
- canonical Parquet + DuckDB for active + delisted securities
- raw data preserved; invalid adjusted histories quarantined
- lifecycle matching, ticker-history validation, and safe retries
- ambiguous identities excluded from research

- shared-FIGI histories with conflicting overlapping prices quarantined
- full ingestion READY / SUCCEEDED with zero FAIL quality checks
- bounded `load_research_frame()` access: SQL filtering and labels, security-ID
  narrowing before feature joins, and a configurable 100,000-row default guard
- ticker changes/reuse and date-boundary lookbacks/labels covered by regression tests
- existing-warehouse smoke: AAPL in 2025, labels enabled, 250 rows in 0.549 seconds
  (~279 MiB process peak RSS); no re-ingestion

Remaining limits:

- Full-archive pandas materialization is intentionally guarded, not validated.
- Broad requests may still scan large amounts of data and spill to disk; the
  2 GB DuckDB working-memory cap does not bound total process memory.
- Keep requests bounded on the 16 GB Mac. See STATUS.md for validation details.

## 2. First real investment hypothesis
Status: NEXT

Initial idea:

> Buy large S&P 500 companies trading below their 200-week moving average
> when the latest earnings are strong.

First test this as an event study rather than immediately imposing a trading strategy.

Candidate signal:

- point-in-time universe: largest 200 S&P 500 constituents
- price below 200-week moving average
- latest earnings classified as strong
- all information must have been publicly available at signal time

Measure forward excess returns at:

- 4 weeks
- 13 weeks
- 26 weeks
- 52 weeks

Compare against:

- S&P 500
- unconditional returns of the same universe
- similar stocks not meeting the signal

Then define entry/exit rules only if the signal appears robust.

Data still required:

- historical point-in-time S&P 500 membership / market-cap ranking
- earnings announcement dates
- reported EPS / revenue
- prior-year comparisons
- analyst expectations if earnings beats are used

## 3. Feature registry
Status: PLANNED

Every feature becomes a versioned object with:

- name
- definition
- rationale
- formula
- source data
- lookback
- availability rule
- implementation
- tests

Examples:

- returns / momentum
- moving-average distance
- volume anomalies
- volatility
- drawdowns
- earnings growth
- earnings surprise
- valuation features

## 4. Experiment framework

Every experiment records:

- hypothesis
- exact feature versions
- universe
- signal definition
- dataset version
- time period
- outcome
- train / validation / test split
- metrics
- event-level results

## 5. Statistical validation

Add:

- confidence intervals
- minimum sample sizes
- temporal holdouts
- walk-forward validation
- baseline comparisons
- transaction costs
- multiple-testing / false-discovery controls

---

# Medium term

- strategy construction from multiple features
- feature interaction testing
- research knowledge base
- retain negative results
- natural-language → formal StrategySpec
- portfolio construction and risk controls

# Long term

## Human-friendly web app

Initial implementation likely Streamlit.

Users can:

1. describe an idea in plain English
2. build a signal visually
3. inspect exactly how it was interpreted
4. run the historical test
5. understand results in plain language
6. save and revisit research

## Multi-user support

Separate:

- shared market data
- shared research engine
- individual hypotheses
- experiments
- saved strategies
- notes

Later add an API so notebooks and web clients use the same engine.

## Broader data

Potential sources:

- fundamentals
- earnings
- historical index membership
- insider activity
- macro
- sectors / industries
- analyst estimates
- alternative data

---

# Session start

At the start of every Jane session:

1. Read AGENTS.md, STATUS.md, and this roadmap.
2. Check Git branch and `git status`; preserve unrelated pre-existing changes.
3. Review the most recently merged work.
4. Identify the current roadmap item and the task agreed with Boris.
5. Implement only that task, run relevant validation, and inspect the diff.
6. Report changes, tests, and limitations; stop for Boris's review. Follow AGENTS.md:
   no commit without explicit instruction; Boris performs GitHub pushes himself.
7. Update this roadmap when priorities change.

# Immediate next step

The bounded research-loader task is completed and accepted. No re-ingestion is needed.
The first real hypothesis remains the next research priority, subject to a separate
agreed task; do not start it automatically.
