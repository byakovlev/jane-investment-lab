from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd


DEFAULT_MAX_ROWS = 100_000


def _check_result_size(con, table, max_rows):
    count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if count > max_rows:
        raise ValueError(
            f"Research result exceeds max_rows={max_rows:,} (at least {count:,} rows). "
            "Narrow symbols/dates or explicitly increase max_rows; no rows were returned."
        )


def load_research_frame(
    root=None,
    symbols=None,
    start_date=None,
    end_date=None,
    include_labels=False,
    *,
    max_rows=DEFAULT_MAX_ROWS,
):
    """Load a bounded research dataframe, with optional future-return labels.

    Symbols match the ticker on each observation date, including historical names
    and reuse by different securities. Filters apply after full-security windows,
    preserving lookbacks and forward outcomes at the requested boundaries.

    Results exceeding max_rows (default 100,000) raise before pandas materializes
    any rows. The guard never truncates a returned result. DuckDB working memory
    is capped at 2 GB with temporary disk spill; broad requests may still scan a
    large archive. Increasing max_rows increases the caller's pandas memory risk.
    """
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    filters, parameters = [], []
    date_filters, date_parameters = [], []
    if symbols is not None:
        if isinstance(symbols, (str, bytes)):
            raise TypeError("symbols must be a collection of ticker strings")
        filters.append("ticker IN (SELECT unnest(?))")
        parameters.append(list(symbols))
    for value, operator in ((start_date, ">="), (end_date, "<=")):
        if value is not None:
            boundary = pd.Timestamp(value)
            if pd.isna(boundary) or boundary.tzinfo is not None:
                raise ValueError("Date bounds must be valid timezone-naive dates or timestamps")
            filters.append(f"trade_date {operator} ?")
            parameters.append(boundary.to_pydatetime())
            date_filters.append(f"r.trade_date {operator} ?")
            date_parameters.append(boundary.to_pydatetime())
    where = " AND ".join(filters) or "TRUE"

    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    db_path = root / "warehouse" / "metadata.duckdb"
    if not db_path.exists():
        raise FileNotFoundError(f"Warehouse not found: {db_path}")

    with TemporaryDirectory(prefix="jane_research_") as spill_dir:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            con.execute("SET memory_limit='2GB'")
            con.execute("SET threads=2")
            con.execute("SET temp_directory=?", [spill_dir])
            version_rows = con.execute(
                "SELECT DISTINCT source_dataset_version_id FROM bars_daily_current LIMIT 2"
            ).fetchall()
            # Entirely quarantined deliveries still have an archival dataset version.
            if not version_rows and con.execute(
                "SELECT count(*) FROM duckdb_views() WHERE view_name='bars_daily_raw_current'"
            ).fetchone()[0]:
                version_rows = con.execute(
                    "SELECT DISTINCT source_dataset_version_id FROM bars_daily_raw_current LIMIT 2"
                ).fetchall()
            if len(version_rows) != 1:
                raise RuntimeError(f"Expected one current dataset version; found {version_rows}")
            dataset_version_id = int(version_rows[0][0])
            history_join = f"""
                LEFT JOIN security_identifier_history h
                  ON h.security_id = r.security_id
                 AND h.source_dataset_version_id = {dataset_version_id}
                 AND h.identifier_type = 'TICKER'
                 AND r.trade_date >= h.valid_from
                 AND (h.valid_to IS NULL OR r.trade_date <= h.valid_to)
            """
            # Candidate IDs are a superset: history tickers plus raw source-symbol
            # fallback. The exact resolved-ticker predicate below removes false
            # matches when a source symbol is overridden by historical metadata.
            raw_filters = list(date_filters)
            if symbols is not None:
                con.execute(f"""
                    CREATE TEMP TABLE _candidate_securities AS
                    SELECT security_id FROM security_identifier_history
                    WHERE source_dataset_version_id = {dataset_version_id}
                      AND identifier_type = 'TICKER'
                      AND identifier_value IN (SELECT unnest(?))
                    UNION
                    SELECT security_id FROM bars_daily_current
                    WHERE source_symbol IN (SELECT unnest(?))
                """, [parameters[0], parameters[0]])
                raw_filters.append("r.security_id IN (SELECT security_id FROM _candidate_securities)")
            raw_where = " AND ".join(raw_filters) or "TRUE"
            # This preflight is before feature windows and pandas. The extra row
            # detects oversize; it is never a silently truncated return value.
            # Resolve actual row tickers (including source-symbol fallback), not
            # just terminal symbols or one security per ticker.
            con.execute(f"""
                CREATE TEMP TABLE _requested_rows AS
                WITH candidate_rows AS MATERIALIZED (
                    SELECT r.security_id, r.trade_date, r.source_symbol
                    FROM bars_daily_current r WHERE {raw_where}
                ), resolved AS (
                    SELECT r.security_id, r.trade_date,
                           coalesce(h.identifier_value, r.source_symbol) AS ticker
                    FROM candidate_rows r {history_join}
                )
                SELECT * FROM resolved WHERE {where} LIMIT {max_rows + 1}
            """, date_parameters + parameters)
            _check_result_size(con, "_requested_rows", max_rows)

            label_sql = ""
            if include_labels:
                label_sql = """,
                    lead(adj_close, 1) OVER w / adj_close - 1 AS label_return_1d,
                    lead(adj_close, 5) OVER w / adj_close - 1 AS label_return_5d,
                    lead(adj_close, 21) OVER w / adj_close - 1 AS label_return_21d
                """
            con.execute(f"""
                CREATE TEMP TABLE _research_result AS
                WITH selected_raw AS MATERIALIZED (
                    SELECT * FROM bars_daily_current
                    WHERE security_id IN (SELECT security_id FROM _requested_rows)
                ), selected_adjusted AS MATERIALIZED (
                    SELECT security_id, trade_date, source_dataset_version_id, adj_close
                    FROM vendor_adjusted_daily_current
                    WHERE security_id IN (SELECT security_id FROM _requested_rows)
                ), base AS (
                    SELECT r.security_id,
                           coalesce(h.identifier_value, r.source_symbol) AS ticker,
                           s.instrument_type, r.trade_date,
                           r.open, r.high, r.low, r.close, r.volume, r.vwap, r.transactions,
                           a.adj_close, r.availability_rule, r.source_dataset_version_id
                    FROM selected_raw r
                    LEFT JOIN selected_adjusted a
                      ON r.security_id = a.security_id
                     AND r.trade_date = a.trade_date
                     AND r.source_dataset_version_id = a.source_dataset_version_id
                    LEFT JOIN security s ON r.security_id = s.security_id
                    {history_join}
                ), features AS (
                    SELECT *,
                           close / open - 1 AS intraday_return,
                           adj_close / lag(adj_close, 1) OVER w - 1 AS return_1d,
                           adj_close / lag(adj_close, 5) OVER w - 1 AS return_5d,
                           adj_close / lag(adj_close, 21) OVER w - 1 AS return_21d,
                           volume / avg(volume) OVER (
                               PARTITION BY security_id ORDER BY trade_date
                               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                           ) - 1 AS volume_vs_20d_avg,
                           (high - low) / close AS daily_range_pct,
                           close * volume AS dollar_volume
                           {label_sql}
                    FROM base
                    WINDOW w AS (PARTITION BY security_id ORDER BY trade_date)
                )
                SELECT * EXCLUDE (adj_close) FROM features
                WHERE {where}
                ORDER BY security_id, trade_date
                LIMIT {max_rows + 1}
            """, parameters)
            # Also guard the actual joined result, even if malformed metadata or
            # adjusted data unexpectedly multiplies rows after preflight.
            _check_result_size(con, "_research_result", max_rows)
            return con.execute("SELECT * FROM _research_result ORDER BY security_id, trade_date").df()
        finally:
            con.close()
