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


@pytest.mark.parametrize("retry_status", ["REJECTED", "INGESTING"])
def test_retry_reuses_legacy_version_and_preserves_provenance(quarantine_delivery, monkeypatch, retry_status):
    import json
    from dataclasses import replace
    from investment_lab import pipeline

    source, project = quarantine_delivery
    # An unrelated label for the same source must survive the retry untouched.
    other = pipeline.ingest_historicaldata_net(source, project, "other", extract_sample=True)
    lifecycles = pipeline.build_lifecycles(source)
    lifecycles[0] = replace(lifecycles[0], symbol_history="LATER:2022-08-01|EARLIER:2022-07-01")
    monkeypatch.setattr(pipeline, "build_lifecycles", lambda root: lifecycles)
    current_pipeline = pipeline.PIPELINE_VERSION
    stable_bigint = pipeline.stable_bigint
    fingerprint = pipeline.source_fingerprint(source)
    legacy_id = stable_bigint("dataset_version", f"{pipeline.DATASET_ID}|{fingerprint}|investment-lab-v0.2")
    with monkeypatch.context() as legacy:
        legacy.setattr(pipeline, "PIPELINE_VERSION", "investment-lab-v0.2")
        legacy.setattr(pipeline, "stable_bigint", lambda namespace, key: legacy_id if namespace == "dataset_version" else stable_bigint(namespace, key))
        previous = pipeline.ingest_historicaldata_net(source, project, "retry", source_asof="2022-12-31", extract_sample=True)
    db = str(project / "warehouse/metadata.duckdb")
    with duckdb.connect(db) as con:
        con.execute("UPDATE dataset_version SET status=? WHERE dataset_version_id=?", [retry_status, legacy_id])
        con.execute("UPDATE ingestion_run SET status=? WHERE ingestion_run_id=?", ["RUNNING" if retry_status == "INGESTING" else "FAILED", previous["ingestion_run_id"]])
        old_checks = con.execute("SELECT * FROM quality_check_result WHERE ingestion_run_id=? ORDER BY check_name", [previous["ingestion_run_id"]]).fetchall()
        other_version = con.execute("SELECT * FROM dataset_version WHERE dataset_version_id=?", [other["dataset_version_id"]]).fetchone()
        # A stale quarantine must disappear; real exceptions must be rebuilt for the new run.
        con.execute("INSERT INTO identity_history_quarantine VALUES (?, ?, ?, '{}')", [legacy_id, 999, previous["ingestion_run_id"]])
    stale_file = Path(previous["raw_parquet"]) / "stale.txt"
    stale_file.write_text("incomplete prior output")
    retried = pipeline.ingest_historicaldata_net(source, project, "retry", extract_sample=True)
    assert retried["dataset_version_id"] == legacy_id
    assert retried["ingestion_run_id"] != previous["ingestion_run_id"]
    assert retried["identity_quarantined_securities"] == 1
    assert not stale_file.exists()
    with duckdb.connect(db) as con:
        assert con.execute("SELECT count(*) FROM dataset_version").fetchone()[0] == 2
        assert con.execute("SELECT * FROM dataset_version WHERE dataset_version_id=?", [other["dataset_version_id"]]).fetchone() == other_version
        assert con.execute("SELECT status,pipeline_version,source_fingerprint_sha256,cast(source_asof AS VARCHAR) FROM dataset_version WHERE dataset_version_id=?", [legacy_id]).fetchone() == ("READY", current_pipeline, fingerprint, "2022-12-31")
        assert con.execute("SELECT status,pipeline_version FROM ingestion_run WHERE ingestion_run_id=?", [previous["ingestion_run_id"]]).fetchone() == ("FAILED", "investment-lab-v0.2")
        assert con.execute("SELECT * FROM quality_check_result WHERE ingestion_run_id=? ORDER BY check_name", [previous["ingestion_run_id"]]).fetchall() == old_checks
        quarantines = con.execute("SELECT security_id,ingestion_run_id,details_json FROM identity_history_quarantine WHERE dataset_version_id=?", [legacy_id]).fetchall()
        assert len(quarantines) == 1
        assert quarantines[0][:2] == (lifecycles[0].security_id, retried["ingestion_run_id"])
        assert json.loads(quarantines[0][2])["source_files"][0]["source_file_name"] == lifecycles[0].source_file_name
        assert con.execute("SELECT count(*) FROM source_file WHERE dataset_version_id=?", [legacy_id]).fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current").fetchone()[0] == 464
        assert con.execute("SELECT count(*) FROM bars_daily_current").fetchone()[0] == 337
    assert pipeline.ingest_historicaldata_net(source, project, "retry") == retried
    assert pipeline.ingest_historicaldata_net(source, project, "other") == other


@pytest.mark.parametrize("status", ["REJECTED", "INGESTING", "READY"])
def test_label_fingerprint_mismatch_preserves_existing_version(quarantine_delivery, monkeypatch, status):
    from investment_lab import pipeline

    source, project = quarantine_delivery
    summary = pipeline.ingest_historicaldata_net(source, project, "fixed-label", extract_sample=True)
    db = str(project / "warehouse/metadata.duckdb")
    with duckdb.connect(db) as con:
        con.execute("UPDATE dataset_version SET status=?", [status])
        before = con.execute("SELECT * FROM dataset_version").fetchall()
        runs = con.execute("SELECT * FROM ingestion_run").fetchall()
    monkeypatch.setattr(pipeline, "source_fingerprint", lambda root: "different-fingerprint")
    with pytest.raises(RuntimeError, match="different source fingerprint"):
        pipeline.ingest_historicaldata_net(source, project, "fixed-label")
    with duckdb.connect(db) as con:
        assert con.execute("SELECT * FROM dataset_version").fetchall() == before
        assert con.execute("SELECT * FROM ingestion_run").fetchall() == runs
    assert any(Path(summary["raw_parquet"]).rglob("*.parquet"))


@pytest.mark.parametrize("change", ["pipeline", "missing_files"])
def test_ready_label_never_rebuilt(quarantine_delivery, monkeypatch, change):
    from investment_lab import pipeline

    source, project = quarantine_delivery
    summary = pipeline.ingest_historicaldata_net(source, project, "ready", extract_sample=True)
    if change == "pipeline":
        monkeypatch.setattr(pipeline, "PIPELINE_VERSION", "future-pipeline")
    else:
        for path in Path(summary["raw_parquet"]).rglob("*.parquet"):
            path.unlink()
    db = str(project / "warehouse/metadata.duckdb")
    with duckdb.connect(db) as con:
        before = con.execute("SELECT * FROM dataset_version").fetchall()
        runs = con.execute("SELECT * FROM ingestion_run").fetchall()
    with pytest.raises(RuntimeError, match="already READY"):
        pipeline.ingest_historicaldata_net(source, project, "ready")
    with duckdb.connect(db) as con:
        assert con.execute("SELECT * FROM dataset_version").fetchall() == before
        assert con.execute("SELECT * FROM ingestion_run").fetchall() == runs


@pytest.fixture
def overlapping_figi_delivery(tmp_path, monkeypatch):
    import csv
    from investment_lab import pipeline

    source, project = tmp_path / "source", tmp_path / "project"
    (source / "day_by_symbol").mkdir(parents=True)
    (project / "schema").mkdir(parents=True)
    shutil.copy2(ROOT / "schema/001_core.sql", project / "schema/001_core.sql")
    fields = ["date", "open", "high", "low", "close", "volume", "vwap", "transactions",
              "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume", "adj_vwap",
              "dividend", "dividend_type", "split"]
    for symbol, dates in {"A": ["2024-01-02", "2024-01-03"], "B": ["2024-01-03", "2024-01-04"],
                          "C": ["2024-01-05"], "SAFE": ["2024-01-02"]}.items():
        with (source / "day_by_symbol" / f"{symbol}_day.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for date in dates:
                writer.writerow(dict(date=date, open=10, high=12, low=9, close=10, volume=100,
                                     vwap=10, transactions=5, adj_open=10, adj_high=12,
                                     adj_low=9, adj_close=10, adj_volume=100, adj_vwap=10))
    with (source / "symbols.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "status", "figi", "symbol_history"])
        writer.writeheader()
        for symbol in ("A", "B", "C", "SAFE"):
            writer.writerow(dict(symbol=symbol, status="active", figi="SAFE" if symbol == "SAFE" else "SHARED",
                                 symbol_history="SAFE:2024-01-01" if symbol == "SAFE" else "SHARED:2024-01-01"))
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (True, ""))
    return source, project


@pytest.mark.parametrize("field,value", [("close", "11"), ("volume", "200"), ("vwap", ""), ("transactions", "6")])
@pytest.mark.parametrize("ticker_conflict", [False, True])
def test_cross_file_market_conflict_quarantines_whole_security(
    overlapping_figi_delivery, monkeypatch, field, value, ticker_conflict,
):
    import csv
    import json
    from investment_lab import pipeline
    from investment_lab.research.frame import load_research_frame

    source, project = overlapping_figi_delivery
    path = source / "day_by_symbol/B_day.csv"
    with path.open() as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    rows[0][field] = value
    with path.open("w") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if ticker_conflict:
        path = source / "symbols.csv"
        path.write_text(path.read_text().replace("A,active,SHARED,SHARED:", "A,active,SHARED,DIFFERENT:"))
    # Adjustment quarantine overlaps price quarantine and must not double-count exclusions.
    monkeypatch.setattr(pipeline, "verify_vendor_delivery", lambda *a, **kw: (
        False, "FAIL [A_day.csv] adjustment factor inconsistent on 1 rows",
    ))
    summary = pipeline.ingest_historicaldata_net(source, project, "overlap")
    assert summary["daily_rows"] == 6
    assert summary["research_rows"] == summary["adjusted_rows"] == 1
    assert summary["identity_quarantined_securities"] == 1
    assert summary["identity_quarantined_files"] == 3
    assert summary["identity_quarantined_rows"] == 5
    assert summary["raw_conflict_quarantined_securities"] == 1
    assert summary["raw_conflicting_date_pairs"] == 1
    assert summary["raw_rows_on_conflicting_dates"] == 2
    assert summary["raw_conflict_quarantined_rows"] == 5
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        security_id, details = con.execute("SELECT security_id,details_json FROM identity_history_quarantine").fetchone()
        q = json.loads(details)
        assert set(q["reasons"]) == ({"ticker_history_conflict", "conflicting_raw_observations"}
                                     if ticker_conflict else {"conflicting_raw_observations"})
        assert q["file_count"] == 3 and q["row_count"] == 5
        assert q["raw_observation_conflict"]["source_files"] == ["A_day.csv", "B_day.csv"]
        assert q["raw_observation_conflict"]["conflicting_date_count"] == 1
        assert q["raw_observation_conflict"]["rows_on_conflicting_dates"] == 2
        assert {f["source_file_name"] for f in q["source_files"]} == {"A_day.csv", "B_day.csv", "C_day.csv"}
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current").fetchone()[0] == 6
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current WHERE security_id=?", [security_id]).fetchone()[0] == 5
        assert con.execute("SELECT count(*) FROM source_file").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM security_identifier_history WHERE security_id=?", [security_id]).fetchone()[0] == 0
        assert con.execute("SELECT status,observed_value FROM quality_check_result WHERE check_name='no_duplicate_security_dates'").fetchone() == ("PASS", "0")
    for labels in (False, True):
        frame = load_research_frame(project, include_labels=labels)
        assert len(frame) == 1
        assert security_id not in set(frame.security_id)
    assert pipeline.ingest_historicaldata_net(source, project, "overlap") == summary
    assert security_id not in set(load_research_frame(project).security_id)


def test_identical_cross_file_rows_are_not_price_conflicts(overlapping_figi_delivery):
    from investment_lab import pipeline

    source, project = overlapping_figi_delivery
    # This change grants no permission to choose/deduplicate identical source rows.
    # The existing research duplicate-key validation still rejects them.
    with pytest.raises(RuntimeError, match="Canonical validation failed"):
        pipeline.ingest_historicaldata_net(source, project, "identical")
    with duckdb.connect(str(project / "warehouse/metadata.duckdb")) as con:
        assert con.execute("SELECT count(*) FROM identity_history_quarantine").fetchone()[0] == 0
        assert con.execute("SELECT observed_value FROM quality_check_result WHERE check_name='raw_conflicting_date_pairs'").fetchone()[0] == "0"
        assert con.execute("SELECT count(*) FROM bars_daily_raw_current").fetchone()[0] == 6
        assert con.execute("SELECT status,observed_value FROM quality_check_result WHERE check_name='no_duplicate_security_dates'").fetchone() == ("FAIL", "1")
