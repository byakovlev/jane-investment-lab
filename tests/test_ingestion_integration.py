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


@pytest.mark.parametrize("second_history", [
    "OTHER:2022-07-01",
    "KO:2022-07-01|NEXT:2022-08-01",
])
def test_conflicting_ticker_history_quarantined(
    quarantine_delivery, monkeypatch, second_history,
):
    import json
    from dataclasses import replace
    from investment_lab import pipeline

    source, project = quarantine_delivery
    lifecycles = pipeline.build_lifecycles(source)
    first, second = lifecycles[:2]
    lifecycles[0] = replace(first, symbol_history="KO:2022-07-01")
    lifecycles[1] = replace(
        second, security_id=first.security_id,
        source_security_key=first.source_security_key, symbol_history=second_history,
    )
    monkeypatch.setattr(pipeline, "build_lifecycles", lambda root: lifecycles)
    # The same file also has an adjustment failure: exclusion counts must not double-count it.
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (
        False, f"FAIL [{first.source_file_name}] adjustment chain breaks at 1 boundaries",
    ))
    summary = pipeline.ingest_historicaldata_net(source, project, "test", extract_sample=True)
    assert summary["daily_rows"] == 464
    assert summary["identity_quarantined_securities"] == 1
    assert summary["identity_quarantined_files"] == 2
    assert summary["identity_quarantined_rows"] == 254
    assert summary["research_rows"] == summary["adjusted_rows"] == 210
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT status FROM dataset_version").fetchone()[0] == "READY"
        assert con.execute("SELECT status FROM ingestion_run").fetchone()[0] == "SUCCEEDED"
        assert con.execute("SELECT count(*) FROM security_identifier_history WHERE security_id=?", [first.security_id]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM source_file").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current WHERE security_id=?", [first.security_id]).fetchone()[0] == 254
        assert con.execute("SELECT count(*) FROM bars_daily_current WHERE security_id=?", [first.security_id]).fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM vendor_adjusted_daily_current WHERE security_id=?", [first.security_id]).fetchone()[0] == 0
        status, details = con.execute("SELECT status, details_json FROM quality_check_result WHERE check_name='ticker_history_consistency'").fetchone()
        assert status == "SKIP"
        conflict = json.loads(details)["conflicts"][0]
        assert conflict["security_id"] == first.security_id
        assert conflict["valid_from"] == "2022-07-01"
        assert {i["source_file_name"] for i in conflict["intervals"]} == {first.source_file_name, second.source_file_name}
        quarantine = json.loads(con.execute("SELECT details_json FROM identity_history_quarantine").fetchone()[0])
        assert quarantine["security_id"] == first.security_id
        assert {f["source_file_name"] for f in quarantine["source_files"]} == {first.source_file_name, second.source_file_name}
        assert quarantine["conflicts"] == [conflict]
    from investment_lab.research.frame import load_research_frame
    for labels in (False, True):
        frame = load_research_frame(project, include_labels=labels)
        assert first.security_id not in set(frame.security_id)
        assert len(frame) == 210
    assert pipeline.ingest_historicaldata_net(source, project, "test") == summary
    assert first.security_id not in set(load_research_frame(project).security_id)


def test_exact_ticker_intervals_deduplicated(quarantine_delivery, monkeypatch):
    import json
    from investment_lab import pipeline

    source, project = quarantine_delivery
    parse = pipeline.parse_symbol_history
    monkeypatch.setattr(pipeline, "parse_symbol_history", lambda *args: parse(*args) * 2)
    pipeline.ingest_historicaldata_net(source, project, "test", extract_sample=True)
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT count(*) FROM security_identifier_history").fetchone()[0] == 4
        status, details = con.execute("SELECT status, details_json FROM quality_check_result WHERE check_name='ticker_history_consistency'").fetchone()
        assert status == "PASS"
        assert json.loads(details) == {"exact_duplicates_deduplicated": 4, "conflicts": [], "quarantined_securities": []}


@pytest.mark.parametrize("older_history", [None, "ABHY:2024-12-13"])
def test_real_abhy_abxb_history_normalized(quarantine_delivery, older_history):
    import json
    from dataclasses import replace
    from investment_lab import pipeline

    source, _ = quarantine_delivery
    base = pipeline.build_lifecycles(source)[0]
    abhy = replace(
        base, security_id=2999054635932704702, source_security_key="FIGI:BBG00YC5ZT62",
        terminal_symbol="ABHY", delisted_at="2025-06-06", symbol_history=older_history,
        local_file_name="ABHY_day_delisted_2025-06-06.csv",
        source_file_name="ABHY_day_delisted_2025-06-06.csv",
    )
    abxb = replace(
        abhy, terminal_symbol="ABXB", delisted_at=None,
        symbol_history="DFHY:2020-12-08|ABHY:2024-12-13|ABXB:2025-06-06",
        local_file_name="ABXB_day.csv", source_file_name="ABXB_day.csv",
    )
    stats = {
        abhy.local_file_name: {"first": "2024-12-13", "last": "2025-06-05", "rows": 118},
        abxb.local_file_name: {"first": "2025-06-06", "last": "2026-09-18", "rows": 323},
    }
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE TABLE quality_check_result (run BIGINT, name VARCHAR, severity VARCHAR, status VARCHAR, observed VARCHAR, expected VARCHAR, details JSON, PRIMARY KEY (run, name))")
        rows, quarantines = pipeline._validated_ticker_history(con, 1, [abhy, abxb], stats)
        assert quarantines == []
        details = json.loads(con.execute("SELECT details FROM quality_check_result").fetchone()[0])
        assert details == {"exact_duplicates_deduplicated": 1, "conflicts": [], "quarantined_securities": []}
        assert set(rows) == {
            (abhy.security_id, "DFHY", "2020-12-08", "2024-12-12"),
            (abhy.security_id, "ABHY", "2024-12-13", "2025-06-05"),
            (abhy.security_id, "ABXB", "2025-06-06", None),
        }
        # A genuinely different end date quarantines the entire security.
        stats[abhy.local_file_name]["last"] = "2025-06-04"
        rows, quarantines = pipeline._validated_ticker_history(con, 1, [abhy, abxb], stats)
        assert rows == []
        assert [q["security_id"] for q in quarantines] == [abhy.security_id]


@pytest.mark.parametrize("all_securities", [False, True])
def test_reversed_intervals_quarantined(quarantine_delivery, monkeypatch, all_securities):
    from dataclasses import replace
    from investment_lab import pipeline
    from investment_lab.research.frame import load_research_frame

    source, project = quarantine_delivery
    lifecycles = pipeline.build_lifecycles(source)
    targets = lifecycles if all_securities else lifecycles[:1]
    ids = {lc.security_id for lc in targets}
    lifecycles = [replace(lc, symbol_history="LATER:2022-08-01|EARLIER:2022-07-01")
                  if lc.security_id in ids else lc for lc in lifecycles]
    monkeypatch.setattr(pipeline, "build_lifecycles", lambda root: lifecycles)
    summary = pipeline.ingest_historicaldata_net(source, project, "test", extract_sample=True)
    assert summary["daily_rows"] == 464
    assert summary["identity_quarantined_securities"] == len(ids)
    assert summary["identity_quarantined_rows"] == (464 if all_securities else 127)
    for labels in (False, True):
        frame = load_research_frame(project, include_labels=labels)
        assert not set(frame.security_id) & ids
        assert len(frame) == summary["research_rows"]
    assert pipeline.ingest_historicaldata_net(source, project, "test") == summary
    assert not set(load_research_frame(project).security_id) & ids
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current").fetchone()[0] == 464
        assert con.execute("SELECT count(*) FROM vendor_adjusted_daily_current").fetchone()[0] == summary["research_rows"]


@pytest.mark.parametrize("failure", ["malformed_history", "negative_volume", "duplicate_source_date"])
def test_structural_failures_not_identity_quarantined(quarantine_delivery, monkeypatch, failure):
    from dataclasses import replace
    from investment_lab import pipeline

    source, project = quarantine_delivery
    lifecycles = pipeline.build_lifecycles(source)
    lc = lifecycles[0]
    lifecycles[0] = replace(lc, symbol_history=("MALFORMED" if failure == "malformed_history"
                                             else "LATER:2022-08-01|EARLIER:2022-07-01"))
    monkeypatch.setattr(pipeline, "build_lifecycles", lambda root: lifecycles)
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (True, ""))
    if failure != "malformed_history":
        import csv
        path = source / "day_by_symbol" / lc.local_file_name
        with path.open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        if failure == "negative_volume":
            rows[0]["volume"] = "-1"
        else:
            rows.append(rows[0])
        with path.open("w") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    with pytest.raises((ValueError, RuntimeError)):
        pipeline.ingest_historicaldata_net(source, project, "test")
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT status FROM dataset_version").fetchone()[0] == "REJECTED"
        assert con.execute("SELECT status FROM ingestion_run").fetchone()[0] == "FAILED"
