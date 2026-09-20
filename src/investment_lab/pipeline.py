from __future__ import annotations

import csv
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from investment_lab.ids import stable_bigint
from investment_lab.providers.historicaldata_net import (
    PROVIDER,
    build_lifecycles,
    day_files,
    manifest_file_hashes,
    parse_symbol_history,
    source_fingerprint,
    verify_vendor_delivery,
    classify_verifier_failures,
    load_filename_map,
)

PIPELINE_VERSION = "investment-lab-v0.2"
DATASET_ID = stable_bigint("dataset", "historicaldata.net|US_EQUITIES|DAILY")

DAY_COLUMNS_SQL = """{
    'date':'DATE',
    'open':'DOUBLE','high':'DOUBLE','low':'DOUBLE','close':'DOUBLE',
    'volume':'DOUBLE','vwap':'DOUBLE','transactions':'BIGINT',
    'adj_open':'DOUBLE','adj_high':'DOUBLE','adj_low':'DOUBLE','adj_close':'DOUBLE',
    'adj_volume':'DOUBLE','adj_vwap':'DOUBLE',
    'dividend':'DOUBLE','dividend_type':'VARCHAR','split':'VARCHAR'
}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _write_lifecycle_map(path: Path, lifecycles) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "local_file_name",
            "source_file_name",
            "security_id",
            "source_security_key",
            "terminal_symbol",
            "delisted_at",
        ])
        for lc in lifecycles:
            w.writerow([
                lc.local_file_name,
                lc.source_file_name,
                lc.security_id,
                lc.source_security_key,
                lc.terminal_symbol,
                lc.delisted_at or "",
            ])


def _record_check(con, run_id: int, name: str, status: str, observed=None, expected=None, severity="ERROR", details=None):
    con.execute(
        """INSERT OR REPLACE INTO quality_check_result
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [run_id, name, severity, status,
         None if observed is None else str(observed),
         None if expected is None else str(expected),
         json.dumps(details) if details is not None else None],
    )


def ingest_historicaldata_net(
    source_root: Path,
    project_root: Path,
    version_label: str,
    source_asof: str | None = None,
    extract_sample: bool = False,
) -> dict:
    source_root = source_root.resolve()
    project_root = project_root.resolve()
    warehouse = project_root / "warehouse"
    warehouse.mkdir(parents=True, exist_ok=True)
    db_path = warehouse / "metadata.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute((project_root / "schema" / "001_core.sql").read_text())

    fingerprint = source_fingerprint(source_root)
    dataset_version_id = stable_bigint("dataset_version", f"{DATASET_ID}|{fingerprint}|{PIPELINE_VERSION}")
    run_id = stable_bigint("ingestion_run", f"{dataset_version_id}|{PIPELINE_VERSION}")
    canonical_root = warehouse / "canonical" / "historicaldata_net" / f"dataset_version={dataset_version_id}"
    raw_root = canonical_root / "bars_daily_raw"
    adjusted_root = canonical_root / "bars_daily_vendor_adjusted"

    con.execute(
        """INSERT OR IGNORE INTO dataset VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [DATASET_ID, "HistoricalData.net US equities daily", PROVIDER, "EQUITY", "DAILY",
         "Vendor daily US equity archive; raw bars plus separately preserved archive-adjusted bars.", _now()],
    )

    existing = con.execute(
        "SELECT status FROM dataset_version WHERE dataset_version_id=?", [dataset_version_id]
    ).fetchone()
    if existing and existing[0] == "READY" and raw_root.exists() and adjusted_root.exists():
        con.execute("DROP VIEW IF EXISTS bars_daily_current")
        con.execute("DROP VIEW IF EXISTS vendor_adjusted_daily_current")
        con.execute(f"CREATE VIEW bars_daily_current AS SELECT * FROM read_parquet('{_sql_path(raw_root)}/**/*.parquet', hive_partitioning=true)")
        con.execute(f"CREATE VIEW vendor_adjusted_daily_current AS SELECT * FROM read_parquet('{_sql_path(adjusted_root)}/**/*.parquet', hive_partitioning=true)")
        row = con.execute(
            "SELECT summary_json FROM ingestion_run WHERE dataset_version_id=? AND status='SUCCEEDED' ORDER BY finished_at DESC LIMIT 1",
            [dataset_version_id],
        ).fetchone()
        summary = row[0] if row else None
        con.close()
        if isinstance(summary, str):
            return json.loads(summary)
        return summary or {"dataset_version_id": dataset_version_id, "status": "READY"}

    # Re-running a failed/incomplete copy is safe: derived rows for this exact dataset/pipeline
    # version are cleared, while the immutable dataset_version identity remains stable.
    con.execute("DELETE FROM quality_check_result WHERE ingestion_run_id=?", [run_id])
    con.execute("DELETE FROM ingestion_run WHERE ingestion_run_id=?", [run_id])
    con.execute("DELETE FROM corporate_action WHERE source_dataset_version_id=?", [dataset_version_id])
    con.execute("DELETE FROM source_file WHERE dataset_version_id=?", [dataset_version_id])
    con.execute("DELETE FROM security_identifier_history WHERE source_dataset_version_id=?", [dataset_version_id])
    con.execute("DELETE FROM source_security_lifecycle_snapshot WHERE source_dataset_version_id=?", [dataset_version_id])

    if canonical_root.exists():
        shutil.rmtree(canonical_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    adjusted_root.mkdir(parents=True, exist_ok=True)

    if existing:
        con.execute(
            """UPDATE dataset_version SET version_label=?, source_asof=?, retrieved_at=?, raw_uri=?,
               source_fingerprint_sha256=?, canonical_raw_uri=?, canonical_adjusted_uri=?, row_count=NULL,
               status='INGESTING', pipeline_version=?, notes=? WHERE dataset_version_id=?""",
            [version_label, source_asof, _now(), str(source_root), fingerprint, str(raw_root), str(adjusted_root),
             PIPELINE_VERSION, "HistoricalData.net delivery ingested without altering source files.", dataset_version_id],
        )
    else:
        con.execute(
            """INSERT INTO dataset_version (
                dataset_version_id,dataset_id,version_label,source_asof,retrieved_at,raw_uri,
                source_fingerprint_sha256,canonical_raw_uri,canonical_adjusted_uri,row_count,
                status,pipeline_version,parent_dataset_version_id,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,NULL,'INGESTING',?,NULL,?)""",
            [dataset_version_id, DATASET_ID, version_label, source_asof, _now(), str(source_root),
             fingerprint, str(raw_root), str(adjusted_root), PIPELINE_VERSION,
             "HistoricalData.net delivery ingested without altering source files."],
        )

    con.execute(
        "INSERT INTO ingestion_run VALUES (?, ?, ?, NULL, 'RUNNING', ?, NULL, NULL)",
        [run_id, dataset_version_id, _now(), PIPELINE_VERSION],
    )

    # Vendor-native verification. The free sample is an extract, the full package is not.
    verified, verify_output = verify_vendor_delivery(
        source_root,
        extract=extract_sample,
    )
    
    adjustment_failures: set[str] = set()
    
    if verified:
        _record_check(
            con,
            run_id,
            "vendor_verify_py",
            "PASS",
            observed="exit=0",
            expected="exit=0",
        )
    else:
        adjustment_failures, manifest_missing, other_failures = (
            classify_verifier_failures(verify_output)
        )
    
        filename_map = load_filename_map(source_root)
    
        mapped_originals = {
            Path(original).name
            for local, original in filename_map.items()
            if (source_root / local).exists()
        }
    
        unexplained_missing = manifest_missing - mapped_originals
    
        if other_failures or unexplained_missing or not (adjustment_failures or manifest_missing):
            _record_check(
                con,
                run_id,
                "vendor_verify_py",
                "FAIL",
                observed="unexplained verification failures",
                expected="only known filename mappings or adjustment failures",
                details={
                    "adjustment_failures": sorted(adjustment_failures),
                    "mapped_manifest_exceptions": sorted(
                        manifest_missing - unexplained_missing
                    ),
                    "unexplained_missing": sorted(unexplained_missing),
                    "other_failures": other_failures,
                },
            )
            con.execute(
                "UPDATE dataset_version SET status='REJECTED' WHERE dataset_version_id=?",
                [dataset_version_id],
            )
            con.execute(
                "UPDATE ingestion_run SET status='FAILED', finished_at=? WHERE ingestion_run_id=?",
                [_now(), run_id],
            )
            raise RuntimeError(
                "Vendor verification contains unexplained failures; "
                "see quality_check_result"
            )
    
        _record_check(
            con,
            run_id,
            "vendor_verify_py",
            "SKIP",
            observed=(
                f"{len(adjustment_failures)} adjustment failures; "
                f"{len(manifest_missing)} mapped filename exceptions"
            ),
            expected="exit=0 or understood exceptions only",
            severity="WARNING",
            details={
                "adjustment_failures": sorted(adjustment_failures),
                "mapped_manifest_exceptions": sorted(manifest_missing),
            },
        )
        
    lifecycles = build_lifecycles(source_root)
    if not lifecycles:
        raise RuntimeError("No security lifecycles resolved")

    # Verifier names may refer to the local file or its original vendor name.
    failed_names = {Path(name.replace("\\", "/")).name for name in adjustment_failures}
    quarantined_lifecycles = [
        lc for lc in lifecycles
        if lc.local_file_name in failed_names or lc.source_file_name in failed_names
    ]
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _adjustment_quarantine "
        "(source_file_name VARCHAR)"
    )
    if quarantined_lifecycles:
        con.executemany(
            "INSERT INTO _adjustment_quarantine VALUES (?)",
            [(lc.source_file_name,) for lc in quarantined_lifecycles],
        )

    # One pass over all daily files with the vendor's fixed schema.
    glob = _sql_path(source_root / "day_by_symbol" / "*.csv")
    con.execute("DROP VIEW IF EXISTS _vendor_daily")
    con.execute(f"""
        CREATE TEMP VIEW _vendor_daily AS
        SELECT *, regexp_extract(filename, '[^/\\\\]+$') AS local_file_name
        FROM read_csv('{glob}', header=true, columns={DAY_COLUMNS_SQL}, filename=true, nullstr='')
    """)
    stats = {row[0]: {"first": str(row[1]), "last": str(row[2]), "rows": row[3]}
             for row in con.execute("""
                 SELECT local_file_name, min(date), max(date), count(*)
                 FROM _vendor_daily GROUP BY 1
             """).fetchall()}

    temp_map = Path(tempfile.mkstemp(prefix="lifecycle_map_", suffix=".csv")[1])
    try:
        _write_lifecycle_map(temp_map, lifecycles)
        con.execute("DROP TABLE IF EXISTS _lifecycle_map")
        con.execute(f"CREATE TEMP TABLE _lifecycle_map AS SELECT * FROM read_csv_auto('{_sql_path(temp_map)}', header=true)")

        # Replace only the source lifecycle rows represented by this immutable dataset version.
        for lc in lifecycles:
            s = stats.get(lc.local_file_name)
            if not s:
                raise RuntimeError(f"No data stats for {lc.source_file_name}")
            first_date, last_date = s["first"], s["last"]
            existing_identity = con.execute(
                "SELECT security_id FROM source_security_identity WHERE provider=? AND source_security_key=?",
                [PROVIDER, lc.source_security_key],
            ).fetchone()
            if existing_identity and existing_identity[0] != lc.security_id:
                raise RuntimeError(f"Source identity remapped unexpectedly: {lc.source_security_key}")

            existing_security = con.execute("SELECT 1 FROM security WHERE security_id=?", [lc.security_id]).fetchone()
            if not existing_security:
                con.execute(
                    "INSERT INTO security VALUES (?, ?, ?, 'USD', ?, ?, FALSE, ?)",
                    [lc.security_id, lc.name, lc.instrument_type, first_date, last_date, lc.metadata_completeness],
                )
            else:
                # Canonical security row tracks our latest best metadata; versioned vendor snapshots remain immutable below.
                con.execute(
                    """UPDATE security SET security_name=?, instrument_type=?,
                       first_observed_date=least(first_observed_date, ?),
                       last_observed_date=greatest(last_observed_date, ?),
                       metadata_completeness=? WHERE security_id=?""",
                    [lc.name, lc.instrument_type, first_date, last_date, lc.metadata_completeness, lc.security_id],
                )

            con.execute(
                "INSERT OR IGNORE INTO source_security_identity VALUES (?,?,?,?)",
                [PROVIDER, lc.source_security_key, lc.security_id, dataset_version_id],
            )
            con.execute(
                """INSERT OR REPLACE INTO source_security_lifecycle_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [PROVIDER, lc.source_security_key, dataset_version_id, lc.security_id, lc.terminal_symbol,
                 lc.name, lc.instrument_type, lc.exchange, lc.status, lc.delisted_at, lc.cik, lc.figi,
                 json.dumps({"symbol_history": lc.symbol_history, "source_file_name": lc.source_file_name})],
            )
            for symbol, start, end in parse_symbol_history(lc, first_date, last_date):
                con.execute(
                    "INSERT INTO security_identifier_history VALUES (?, 'TICKER', ?, 'US_EQUITY', ?, ?, ?)",
                    [lc.security_id, symbol, start, end, dataset_version_id],
                )

        # Source file metadata from vendor manifests + actual daily scan.
        manifest_hashes = manifest_file_hashes(source_root)
        lifecycle_by_local = {lc.local_file_name: lc for lc in lifecycles}

        for path in day_files(source_root):
            lc = lifecycle_by_local[path.name]
            rel = (Path("day_by_symbol") / lc.source_file_name).as_posix()
            s = stats[path.name]
            mbytes, msha = manifest_hashes.get(rel, (path.stat().st_size, None))
            con.execute(
                """INSERT OR REPLACE INTO source_file VALUES (?, ?, ?, ?, ?, ?, ?, 'DAILY_BARS', 'PASS')""",
                [dataset_version_id, rel, mbytes or path.stat().st_size, msha,
                 s["rows"], s["first"], s["last"]],
            )

        # Raw bars are the point-in-time market observations. Keep vendor event columns out
        # of this table so signal code cannot accidentally use future-adjusted fields.
        con.execute("DROP TABLE IF EXISTS _raw_bars")
        con.execute(f"""
            CREATE TEMP VIEW _raw_bars AS
            SELECT
                m.security_id,
                m.source_file_name,
                m.terminal_symbol AS source_symbol,
                v.date AS trade_date,
                year(v.date)::INTEGER AS trade_year,
                v.open, v.high, v.low, v.close,
                v.volume, v.vwap, v.transactions,
                'AFTER_SESSION_CLOSE'::VARCHAR AS availability_rule,
                {dataset_version_id}::BIGINT AS source_dataset_version_id,
                md5(concat_ws('|',
                    m.source_file_name, cast(v.date as varchar),
                    coalesce(cast(v.open as varchar),'<NULL>'), coalesce(cast(v.high as varchar),'<NULL>'),
                    coalesce(cast(v.low as varchar),'<NULL>'), coalesce(cast(v.close as varchar),'<NULL>'),
                    coalesce(cast(v.volume as varchar),'<NULL>'), coalesce(cast(v.vwap as varchar),'<NULL>'),
                    coalesce(cast(v.transactions as varchar),'<NULL>')
                )) AS source_row_hash
            FROM _vendor_daily v
            JOIN _lifecycle_map m USING (local_file_name)
        """)

        # Vendor adjusted bars are useful for ex-post total-return math/validation, but their
        # factor is computed through the latest row in the archive. Keep them physically separate.
        con.execute("DROP TABLE IF EXISTS _adjusted_bars")
        con.execute(f"""
            CREATE TEMP VIEW _adjusted_bars AS
            SELECT
                m.security_id,
                m.source_file_name,
                m.terminal_symbol AS source_symbol,
                v.date AS trade_date,
                year(v.date)::INTEGER AS trade_year,
                v.adj_open, v.adj_high, v.adj_low, v.adj_close, v.adj_volume, v.adj_vwap,
                'VENDOR_ARCHIVE_ADJUSTED_THROUGH_SNAPSHOT'::VARCHAR AS adjustment_basis,
                {dataset_version_id}::BIGINT AS source_dataset_version_id
            FROM _vendor_daily v
            JOIN _lifecycle_map m USING (local_file_name)
            WHERE NOT EXISTS (
                SELECT 1 FROM _adjustment_quarantine q
                WHERE q.source_file_name = m.source_file_name
            )
        """)

        # Stream canonical views straight to partitioned Parquet. We avoid materializing the
        # whole archive inside metadata.duckdb; the database stays a catalogue, not a warehouse.
        con.execute(f"COPY (SELECT * FROM _raw_bars) TO '{_sql_path(raw_root)}' (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (trade_year), OVERWRITE_OR_IGNORE)")
        con.execute(f"COPY (SELECT * FROM _adjusted_bars) TO '{_sql_path(adjusted_root)}' (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (trade_year), OVERWRITE_OR_IGNORE)")
        # Partitioned COPY emits no files when every adjusted history is quarantined.
        # Preserve a readable, schema-carrying dataset for current views and reruns.
        if not any(adjusted_root.rglob("*.parquet")):
            con.execute(f"COPY (SELECT * FROM _adjusted_bars LIMIT 0) TO '{_sql_path(adjusted_root / 'empty.parquet')}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        con.execute("DROP VIEW IF EXISTS bars_daily_current")
        con.execute("DROP VIEW IF EXISTS vendor_adjusted_daily_current")
        con.execute(f"CREATE VIEW bars_daily_current AS SELECT * FROM read_parquet('{_sql_path(raw_root)}/**/*.parquet', hive_partitioning=true)")
        con.execute(f"CREATE VIEW vendor_adjusted_daily_current AS SELECT * FROM read_parquet('{_sql_path(adjusted_root)}/**/*.parquet', hive_partitioning=true)")

        vendor_count = sum(x["rows"] for x in stats.values())
        raw_count = con.execute("SELECT count(*) FROM bars_daily_current").fetchone()[0]
        adjusted_count = con.execute("SELECT count(*) FROM vendor_adjusted_daily_current").fetchone()[0]
        quarantined_file_count = len(quarantined_lifecycles)
        quarantined_row_count = sum(stats[lc.local_file_name]["rows"] for lc in quarantined_lifecycles)
        for name, count in (
            ("adjusted_quarantined_files", quarantined_file_count),
            ("adjusted_quarantined_rows", quarantined_row_count),
        ):
            _record_check(
                con, run_id, name, "SKIP" if count else "PASS", count, 0,
                severity="WARNING" if count else "INFO",
            )
        unresolved = vendor_count - raw_count
        duplicate_keys = con.execute("""
            SELECT count(*) FROM (
                SELECT security_id, trade_date, count(*) n FROM bars_daily_current
                GROUP BY 1,2 HAVING n > 1
            )
        """).fetchone()[0]
        invalid_ohlc = con.execute("""
            SELECT count(*) FROM bars_daily_current
            WHERE open IS NOT NULL AND (
                low > open OR open > high OR low > close OR close > high OR
                open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
            )
        """).fetchone()[0]
        negative_volume = con.execute("SELECT count(*) FROM bars_daily_current WHERE volume < 0").fetchone()[0]

        checks = [
            ("canonical_row_count_matches_vendor", raw_count == vendor_count, raw_count, vendor_count),
            ("adjusted_row_count_matches_vendor", adjusted_count == vendor_count - quarantined_row_count,
             adjusted_count, vendor_count - quarantined_row_count),
            ("all_files_resolved_to_security", unresolved == 0, unresolved, 0),
            ("no_duplicate_security_dates", duplicate_keys == 0, duplicate_keys, 0),
            ("ohlc_invariants", invalid_ohlc == 0, invalid_ohlc, 0),
            ("nonnegative_volume", negative_volume == 0, negative_volume, 0),
        ]
        for name, ok, observed, expected in checks:
            _record_check(con, run_id, name, "PASS" if ok else "FAIL", observed, expected)
        if not all(ok for _, ok, _, _ in checks):
            raise RuntimeError("Canonical validation failed")

        # Extract explicit corporate actions from the daily source. Safe-to-use dates are
        # intentionally conservative: this product does not publish declaration timestamps.
        # Thus a dividend/split may be used only from its ex/effective date onward.
        con.execute("DELETE FROM corporate_action WHERE source_dataset_version_id=?", [dataset_version_id])
        action_rows = con.execute("""
            SELECT m.security_id, m.source_file_name, v.date, v.dividend, v.dividend_type, v.split
            FROM _vendor_daily v JOIN _lifecycle_map m USING (local_file_name)
            WHERE v.dividend IS NOT NULL OR v.split IS NOT NULL
            ORDER BY 1,3
        """).fetchall()
        action_count = 0
        for security_id, source_file_name, dt, dividend, dividend_type, split_text in action_rows:
            if dividend is not None:
                currency = "USD"
                classification = dividend_type
                if dividend_type and "+CUR:" in dividend_type:
                    classification, currency = dividend_type.split("+CUR:", 1)
                source_record = f"{source_file_name}|{dt}|DIVIDEND"
                action_id = stable_bigint("corporate_action", f"{dataset_version_id}|{security_id}|{source_record}")
                con.execute(
                    """INSERT INTO corporate_action VALUES (
                        ?,?,'DIVIDEND',?,?,NULL,?,'EX_DATE_ONLY',NULL,NULL,?,?,?,?,?,?
                    )""",
                    [action_id, security_id, dt, dt, dt, dividend, currency, classification,
                     dataset_version_id, source_record,
                     json.dumps({"dividend": dividend, "dividend_type": dividend_type})],
                )
                action_count += 1
            if split_text:
                for idx, token in enumerate(str(split_text).split("+")):
                    from_qty, to_qty = token.split(":", 1)
                    source_record = f"{source_file_name}|{dt}|SPLIT|{idx}"
                    action_id = stable_bigint("corporate_action", f"{dataset_version_id}|{security_id}|{source_record}")
                    con.execute(
                        """INSERT INTO corporate_action VALUES (
                            ?,?,'SPLIT',?,?,NULL,?,'EX_DATE_ONLY',?,?,NULL,'USD',NULL,?,?,?
                        )""",
                        [action_id, security_id, dt, dt, dt, float(from_qty), float(to_qty),
                         dataset_version_id, source_record,
                         json.dumps({"split": split_text, "component": token})],
                    )
                    action_count += 1

        # Add delisting as a lifecycle event when the vendor provides it.
        for lc in lifecycles:
            if lc.delisted_at:
                source_record = f"{lc.source_file_name}|{lc.delisted_at}|DELISTING"
                action_id = stable_bigint("corporate_action", f"{dataset_version_id}|{lc.security_id}|{source_record}")
                con.execute(
                    """INSERT INTO corporate_action VALUES (
                        ?,?,'DELISTING',NULL,?,NULL,?,'VENDOR_LIFECYCLE_DATE',NULL,NULL,NULL,'USD',NULL,?,?,?
                    )""",
                    [action_id, lc.security_id, lc.delisted_at, lc.delisted_at,
                     dataset_version_id, source_record,
                     json.dumps({"delisted_at": lc.delisted_at})],
                )
                action_count += 1

        _record_check(con, run_id, "corporate_actions_extracted", "PASS", action_count, ">=0", severity="INFO")
        provisional = sum(1 for lc in lifecycles if lc.metadata_completeness == "PROVISIONAL")
        _record_check(con, run_id, "security_master_metadata", "PASS" if provisional == 0 else "SKIP",
                      observed=f"{provisional} provisional", expected="0 provisional",
                      severity="WARNING" if provisional else "INFO",
                      details={"reason": "Free sample does not include symbols.csv; full package does."} if provisional else None)

        summary = {
            "dataset_version_id": dataset_version_id,
            "ingestion_run_id": run_id,
            "source_fingerprint_sha256": fingerprint,
            "daily_files": len(lifecycles),
            "daily_rows": raw_count,
            "adjusted_rows": adjusted_count,
            "adjusted_quarantined_files": quarantined_file_count,
            "adjusted_quarantined_rows": quarantined_row_count,
            "securities": len(lifecycles),
            "provisional_security_metadata": provisional,
            "corporate_actions": action_count,
            "raw_parquet": str(raw_root),
            "adjusted_parquet": str(adjusted_root),
        }
        con.execute(
            """UPDATE dataset_version SET status='READY', row_count=? WHERE dataset_version_id=?""",
            [raw_count, dataset_version_id],
        )
        con.execute(
            """UPDATE ingestion_run SET status='SUCCEEDED', finished_at=?, summary_json=? WHERE ingestion_run_id=?""",
            [_now(), json.dumps(summary), run_id],
        )
        return summary
    except Exception:
        con.execute("UPDATE dataset_version SET status='REJECTED' WHERE dataset_version_id=?", [dataset_version_id])
        con.execute("UPDATE ingestion_run SET status='FAILED', finished_at=? WHERE ingestion_run_id=?", [_now(), run_id])
        raise
    finally:
        try:
            temp_map.unlink(missing_ok=True)
        except Exception:
            pass
        con.close()
