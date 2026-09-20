from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "vendor_samples" / "historicaldata_net_sample_2022H2.zip"


def test_full_sample_ingestion(tmp_path):
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
    con = duckdb.connect(str(project_root / "warehouse" / "metadata.duckdb"), read_only=True)
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


@pytest.fixture
def quarantine_delivery(tmp_path):
    import zipfile

    source = tmp_path / "source"
    with zipfile.ZipFile(SAMPLE) as archive:
        archive.extractall(source)
    project = tmp_path / "project"
    (project / "schema").mkdir(parents=True)
    shutil.copy2(ROOT / "schema" / "001_core.sql", project / "schema" / "001_core.sql")
    return source, project


@pytest.mark.parametrize("failure_name", ["KO_day.csv", "RENAMED_day.csv"])
def test_adjustment_quarantine_preserves_raw_and_filename_map(quarantine_delivery, monkeypatch, failure_name):
    import json
    from investment_lab import pipeline

    source, project = quarantine_delivery
    original = source / "day_by_symbol" / "KO_day.csv"
    quarantined_rows = len(original.read_text().splitlines()) - 1
    original.rename(original.with_name("RENAMED_day.csv"))
    (source / "filename-map.json").write_text(json.dumps([
        {"local_name": "day_by_symbol/RENAMED_day.csv", "original_name": "day_by_symbol/KO_day.csv"}
    ]))
    output = (
        f"FAIL [{failure_name}] adjustment chain breaks at 1 boundaries\n"
        "FAIL [day_by_symbol/manifest.json] manifest lists KO_day.csv but it is missing\n"
    )
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (False, output))
    summary = pipeline.ingest_historicaldata_net(source, project, "test")
    assert summary["daily_rows"] == 464
    assert summary["adjusted_rows"] == 464 - quarantined_rows
    assert summary["adjusted_quarantined_files"] == 1
    assert summary["adjusted_quarantined_rows"] == quarantined_rows
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT count(*) FROM bars_daily_current WHERE source_file_name='KO_day.csv'").fetchone()[0] == quarantined_rows
        assert con.execute("SELECT count(*) FROM vendor_adjusted_daily_current WHERE source_file_name='KO_day.csv'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM quality_check_result WHERE status='FAIL'").fetchone()[0] == 0
        checks = dict(con.execute("SELECT check_name, observed_value FROM quality_check_result WHERE check_name LIKE 'adjusted_quarantined_%'").fetchall())
        assert checks == {"adjusted_quarantined_files": "1", "adjusted_quarantined_rows": str(quarantined_rows)}
    assert pipeline.ingest_historicaldata_net(source, project, "test") == summary


def test_all_adjusted_histories_quarantined(quarantine_delivery, monkeypatch):
    from investment_lab import pipeline

    source, project = quarantine_delivery
    output = "\n".join(f"FAIL [{p.name}] adjustment factor inconsistent on 1 rows" for p in (source / "day_by_symbol").glob("*.csv"))
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (False, output))
    summary = pipeline.ingest_historicaldata_net(source, project, "test")
    assert summary["daily_rows"] == summary["adjusted_quarantined_rows"] == 464
    assert summary["adjusted_quarantined_files"] == 4
    assert summary["adjusted_rows"] == 0
    assert pipeline.ingest_historicaldata_net(source, project, "test") == summary


@pytest.mark.parametrize("output", [
    "verifier crashed",
    "FAIL [KO_day.csv] adjustment chain breaks at 1 boundaries\nFAIL [SPY_day.csv] hash mismatch",
    "FAIL [day_by_symbol/manifest.json] manifest lists MISSING_day.csv but it is missing",
])
def test_unexplained_verifier_failures_rejected(quarantine_delivery, monkeypatch, output):
    from investment_lab import pipeline

    source, project = quarantine_delivery
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (False, output))
    with pytest.raises(RuntimeError, match="unexplained failures"):
        pipeline.ingest_historicaldata_net(source, project, "test")
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT status FROM dataset_version").fetchone()[0] == "REJECTED"
        assert con.execute("SELECT status FROM ingestion_run").fetchone()[0] == "FAILED"
