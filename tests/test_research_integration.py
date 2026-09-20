from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "vendor_samples" / "historicaldata_net_sample_2022H2.zip"
HYPOTHESIS = ROOT / "hypotheses" / "three_up_days_then_next_day_up.json"
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.research.runner import run_hypothesis


def test_three_up_days_hypothesis_on_vendor_sample(tmp_path):
    project_root = tmp_path / "project"
    (project_root / "schema").mkdir(parents=True)
    shutil.copy2(
        ROOT / "schema" / "001_core.sql",
        project_root / "schema" / "001_core.sql",
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ingest_historicaldata.py"),
            str(SAMPLE),
            "--project-root",
            str(project_root),
        ],
        check=True,
    )

    result = run_hypothesis(HYPOTHESIS, root=project_root)

    # KO + TSLA + TWTR have 334 rows with a known next-trading-day outcome.
    assert result.n_eligible_observations == 334
    # In the small 2022-H2 sample none has three consecutive >3% close-to-close returns.
    assert result.n_signals == 0
    assert result.n_successes == 0
    assert result.hit_rate is None
    # 85 / 334 eligible next-day observations exceed +1%.
    assert result.baseline_hit_rate == pytest.approx(85 / 334)
    assert (project_root / result.result_json).exists()
    assert (project_root / result.events_csv).exists()
