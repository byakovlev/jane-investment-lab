# Code map — where to look

This project deliberately separates **research definitions** from **research implementation**.

| What you want to inspect/change | File/folder |
|---|---|
| Run hypotheses interactively | `notebooks/Hypothesis_Lab.ipynb` |
| Saved hypothesis definitions | `hypotheses/*.json` |
| Hypothesis compiler/runner | `src/investment_lab/research/runner.py` |
| Vendor-specific ingestion | `src/investment_lab/providers/historicaldata_net.py` |
| Canonical ingestion pipeline | `src/investment_lab/pipeline.py` |
| Canonical metadata schema | `schema/001_core.sql` |
| Automated tests | `tests/` |
| Immutable experiment outputs | `results/<experiment_id>/` |
| Data warehouse | `warehouse/` |

## Rule of thumb

- **Change a hypothesis** by editing JSON or the notebook scratch spec.
- **Change research semantics** in `research/runner.py` and add a test.
- **Change a data source** in `providers/`, not in research code.
- **Never manually edit canonical Parquet or experiment results.** Rebuild/rerun instead.
