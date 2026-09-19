from __future__ import annotations

import csv
import re
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "vendor_samples" / "historicaldata_net_sample_2022H2.zip"


def _symbol(filename: str) -> str:
    return re.match(r"(.+?)_day", Path(filename).name).group(1)


def test_reference_calculation_for_first_hypothesis():
    """Independent stdlib reference calculation, separate from DuckDB/runner SQL."""
    eligible = 0
    baseline_successes = 0
    signals = 0
    signal_successes = 0

    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(SAMPLE) as z:
            z.extractall(td)
        for path in sorted((Path(td) / "day_by_symbol").glob("*_day*.csv")):
            symbol = _symbol(path.name)
            if symbol not in {"KO", "TSLA", "TWTR"}:
                continue
            with path.open(newline="") as f:
                rows = list(csv.DictReader(f))
            closes = [float(r["adj_close"]) for r in rows]
            returns = [None] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
            for i in range(len(rows) - 1):
                next_return = closes[i + 1] / closes[i] - 1
                eligible += 1
                baseline_successes += next_return > 0.01
                if i >= 3 and all(returns[j] is not None and returns[j] > 0.03 for j in (i, i - 1, i - 2)):
                    signals += 1
                    signal_successes += next_return > 0.01

    assert eligible == 334
    assert baseline_successes == 85
    assert signals == 0
    assert signal_successes == 0
