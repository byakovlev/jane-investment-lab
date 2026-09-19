from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import duckdb
except ImportError as exc:
    raise SystemExit("Run: pip install -r requirements.txt") from exc


def show(con, title: str, sql: str) -> None:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print(f"\n{title}")
    print(" | ".join(cols))
    print("-+-".join("-" * len(c) for c in cols))
    for row in rows:
        print(" | ".join("" if x is None else str(x) for x in row))


con = duckdb.connect(str(ROOT / "warehouse" / "metadata.duckdb"), read_only=True)
show(con, "Dataset versions", """
SELECT dataset_version_id, version_label, status, row_count, source_fingerprint_sha256
FROM dataset_version ORDER BY retrieved_at DESC
""")
show(con, "Securities", """
SELECT s.security_id, l.terminal_symbol, s.security_name, s.instrument_type,
       s.first_observed_date, s.last_observed_date, s.metadata_completeness
FROM security s JOIN source_security_lifecycle_snapshot l USING (security_id)
QUALIFY row_number() OVER (PARTITION BY s.security_id ORDER BY l.source_dataset_version_id DESC)=1
ORDER BY l.terminal_symbol
""")
show(con, "Daily raw bars", """
SELECT source_symbol, min(trade_date) first_date, max(trade_date) last_date,
       count(*) rows, round(min(close),2) min_close, round(max(close),2) max_close
FROM bars_daily_current GROUP BY 1 ORDER BY 1
""")
show(con, "Corporate actions", """
SELECT l.terminal_symbol, c.action_type, c.effective_date, c.cash_amount,
       c.ratio_numerator, c.ratio_denominator, c.classification, c.availability_basis
FROM corporate_action c
JOIN source_security_lifecycle_snapshot l
  ON c.security_id=l.security_id AND c.source_dataset_version_id=l.source_dataset_version_id
ORDER BY effective_date, terminal_symbol
""")
show(con, "Quality checks", """
SELECT check_name, severity, status, observed_value, expected_value
FROM quality_check_result ORDER BY check_name
""")
