from pathlib import Path
import sys

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.research.frame import load_research_frame


def build_test_warehouse(root: Path):
    warehouse = root / "warehouse"
    warehouse.mkdir()

    con = duckdb.connect(str(warehouse / "metadata.duckdb"))

    con.execute("""
        CREATE TABLE security (
            security_id BIGINT,
            security_name VARCHAR,
            instrument_type VARCHAR
        )
    """)
    con.execute("INSERT INTO security VALUES (1, 'Example Corp', 'CS')")

    con.execute("""
        CREATE TABLE security_identifier_history (
            security_id BIGINT,
            identifier_type VARCHAR,
            identifier_value VARCHAR,
            namespace VARCHAR,
            valid_from DATE,
            valid_to DATE,
            source_dataset_version_id BIGINT
        )
    """)

    con.execute("""
        INSERT INTO security_identifier_history VALUES
        (1, 'TICKER', 'OLD', 'US_EQUITY',
         DATE '2024-01-01', DATE '2024-01-02', 10),
        (1, 'TICKER', 'NEW', 'US_EQUITY',
         DATE '2024-01-03', NULL, 10)
    """)

    con.execute("""
        CREATE TABLE bars_daily_current (
            security_id BIGINT,
            source_symbol VARCHAR,
            trade_date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            vwap DOUBLE,
            transactions BIGINT,
            availability_rule VARCHAR,
            source_dataset_version_id BIGINT
        )
    """)

    con.execute("""
        INSERT INTO bars_daily_current VALUES
        (1, 'NEW', DATE '2024-01-02',
         100, 102, 99, 101, 1000, 100.5, 100,
         'AFTER_SESSION_CLOSE', 10),
        (1, 'NEW', DATE '2024-01-03',
         101, 104, 100, 103, 1100, 102.0, 110,
         'AFTER_SESSION_CLOSE', 10),
        (1, 'NEW', DATE '2024-01-04',
         103, 106, 102, 105, 1200, 104.0, 120,
         'AFTER_SESSION_CLOSE', 10)
    """)

    con.execute("""
        CREATE TABLE vendor_adjusted_daily_current AS
        SELECT
            security_id,
            source_symbol,
            trade_date,
            close AS adj_close,
            source_dataset_version_id
        FROM bars_daily_current
    """)

    con.close()


def test_future_labels_are_not_in_default_frame(tmp_path):
    build_test_warehouse(tmp_path)
    df = load_research_frame(root=tmp_path)
    assert not any(c.startswith("label_") for c in df.columns)
    assert "adj_close" not in df.columns
    assert "security_name" not in df.columns


def test_labels_are_added_only_when_requested(tmp_path):
    build_test_warehouse(tmp_path)
    df = load_research_frame(root=tmp_path, include_labels=True)
    assert "label_return_1d" in df.columns
    assert df.loc[0, "label_return_1d"] == pytest.approx(103 / 101 - 1)


def test_historical_ticker_is_used(tmp_path):
    build_test_warehouse(tmp_path)
    df = load_research_frame(root=tmp_path)
    assert df["ticker"].tolist() == ["OLD", "NEW", "NEW"]


def test_end_date_does_not_destroy_future_label(tmp_path):
    build_test_warehouse(tmp_path)

    df = load_research_frame(
        root=tmp_path,
        end_date="2024-01-03",
        include_labels=True,
    )

    assert len(df) == 2
    assert df.iloc[-1]["label_return_1d"] == pytest.approx(105 / 103 - 1)
