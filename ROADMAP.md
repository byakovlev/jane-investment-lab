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

---

# Current priorities

## 1. Ingest full historical equity dataset
Status: NEXT

- inspect purchased vendor package
- validate files
- ingest active + delisted securities
- validate ticker histories and corporate actions
- run quality checks
- test `load_research_frame()` at full scale

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

1. Read this roadmap.
2. Check Git branch and `git status`.
3. Review the most recently merged work.
4. Identify the current roadmap item.
5. Choose one small coherent change.
6. Work locally → test → inspect diff → commit → push → PR.
7. Update this roadmap when priorities change.

# Immediate next step

Inspect and ingest the full historical equity dataset.

Then obtain the point-in-time data required for the first real investment hypothesis.
