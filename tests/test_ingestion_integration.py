from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "vendor_samples" / "historicaldata_net_sample_2022H2.zip"


def test_full_sample_ingestion():
    shutil.rmtree(ROOT / "warehouse", ignore_errors=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "ingest_historicaldata.py"), str(SAMPLE)], check=True)
    con = duckdb.connect(str(ROOT / "warehouse" / "metadata.duckdb"), read_only=True)
    assert con.execute("select count(*) from bars_daily_current").fetchone()[0] == 464
    assert con.execute("select count(*) from security").fetchone()[0] == 4
    assert con.execute("select count(*) from corporate_action where action_type='DIVIDEND'").fetchone()[0] == 4
    assert con.execute("select count(*) from corporate_action where action_type='SPLIT'").fetchone()[0] == 1
    assert con.execute("select count(*) from corporate_action where action_type='DELISTING'").fetchone()[0] == 1
    assert con.execute("select count(*) from quality_check_result where status='FAIL'").fetchone()[0] == 0
    # Vendor-adjusted data is deliberately separate from point-in-time raw bars.
    raw_cols = {r[0] for r in con.execute("describe bars_daily_current").fetchall()}
    assert "adj_close" not in raw_cols
    adj_cols = {r[0] for r in con.execute("describe vendor_adjusted_daily_current").fetchall()}
    assert "adj_close" in adj_cols
