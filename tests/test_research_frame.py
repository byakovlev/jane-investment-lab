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


@pytest.fixture
def history_warehouse(tmp_path):
    from datetime import date, timedelta

    build_test_warehouse(tmp_path)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)
             if (date(2024, 1, 1) + timedelta(days=i)).weekday() < 5]
    with duckdb.connect(str(tmp_path / "warehouse/metadata.duckdb")) as con:
        con.execute("DELETE FROM bars_daily_current")
        con.execute("DELETE FROM vendor_adjusted_daily_current")
        con.execute("DELETE FROM security_identifier_history")
        con.execute("INSERT INTO security VALUES (2, 'Reused ticker', 'CS'), (3, 'Fallback', 'ETF'), (4, 'Overridden source ticker', 'CS')")
        con.execute("""INSERT INTO security_identifier_history VALUES
            (1, 'TICKER', 'OLD', 'US_EQUITY', '2024-01-01', '2024-01-31', 10),
            (1, 'TICKER', 'NEW', 'US_EQUITY', '2024-02-01', NULL, 10),
            (2, 'TICKER', 'PRE', 'US_EQUITY', '2024-01-01', '2024-02-14', 10),
            (2, 'TICKER', 'OLD', 'US_EQUITY', '2024-02-15', NULL, 10),
            (4, 'TICKER', 'OTHER', 'US_EQUITY', '2024-01-01', NULL, 10)
        """)
        rows = []
        for sid, source_symbol in [(1, 'NEW'), (2, 'OLD'), (3, 'FALLBACK'), (4, 'NEW')]:
            for i, dt in enumerate(dates):
                price = 100 * sid + i
                rows.append((sid, source_symbol, dt, price, price + 2, price - 2,
                             price + 1, 1000 + i * 10, price, 10, 'AFTER_SESSION_CLOSE', 10))
        con.executemany("INSERT INTO bars_daily_current VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("""INSERT INTO vendor_adjusted_daily_current
            SELECT security_id,source_symbol,trade_date,
                   CASE WHEN security_id=3 THEN NULL ELSE close END,
                   source_dataset_version_id FROM bars_daily_current""")
    return tmp_path


@pytest.mark.parametrize("labels", [False, True])
@pytest.mark.parametrize("symbols,start,end", [
    (["OLD"], None, None),
    (["NEW"], "2024-02-01", "2024-02-05"),
    (["OLD"], "2024-01-24", "2024-02-26"),
    (["OLD", "NEW"], "2024-01-31", "2024-02-01"),
    (["FALLBACK"], "2024-02-15", "2024-02-16"),
    (None, "2024-02-15", "2024-02-15"),
    ([], None, None),
    (["UNKNOWN"], None, None),
    (["OLD' OR 1=1 --"], None, None),
    (None, "2025-01-01", None),
    (None, "2024-03-01", "2024-02-01"),
    (["OLD"], "2024-01-31 12:00:00", "2024-02-15"),
])
def test_sql_filtered_results_match_full_fixture_slices(history_warehouse, labels, symbols, start, end):
    import pandas as pd

    full = load_research_frame(history_warehouse, include_labels=labels)
    mask = pd.Series(True, index=full.index)
    if symbols is not None:
        mask &= full.ticker.isin(symbols)
    if start is not None:
        mask &= full.trade_date >= pd.Timestamp(start)
    if end is not None:
        mask &= full.trade_date <= pd.Timestamp(end)
    expected = full.loc[mask].reset_index(drop=True)
    actual = load_research_frame(history_warehouse, symbols=symbols, start_date=start,
                                 end_date=end, include_labels=labels)
    pd.testing.assert_frame_equal(actual, expected)
    if symbols == ["OLD"] and start is None:
        assert set(actual.security_id) == {1, 2}
    if symbols == ["NEW"]:
        assert set(actual.security_id) == {1}  # Source symbol alone must not match security 4.


def test_lookbacks_and_labels_cross_symbol_and_date_boundaries(history_warehouse):
    import pandas as pd

    # Calculate independent references from raw fixture prices, including data
    # before the requested ticker change and after the final requested date.
    with duckdb.connect(str(history_warehouse / "warehouse/metadata.duckdb"), read_only=True) as con:
        raw = con.execute("SELECT trade_date,close,volume FROM bars_daily_current WHERE security_id=1 ORDER BY trade_date").df()
    dt = pd.Timestamp("2024-02-01")
    idx = raw.index[raw.trade_date == dt][0]
    row = load_research_frame(history_warehouse, symbols=["NEW"], start_date=dt,
                              end_date=dt, include_labels=True).iloc[0]
    for horizon in (1, 5, 21):
        assert row[f"return_{horizon}d"] == pytest.approx(raw.close.iloc[idx] / raw.close.iloc[idx - horizon] - 1)
        assert row[f"label_return_{horizon}d"] == pytest.approx(raw.close.iloc[idx + horizon] / raw.close.iloc[idx] - 1)
    assert row.volume_vs_20d_avg == pytest.approx(raw.volume.iloc[idx] / raw.volume.iloc[idx - 19:idx + 1].mean() - 1)
    # The OLD ticker's final row must still see the next NEW ticker observation.
    old = load_research_frame(history_warehouse, symbols=["OLD"], start_date="2024-01-31",
                              end_date="2024-01-31", include_labels=True).iloc[0]
    assert old.label_return_1d == pytest.approx(raw.close.iloc[idx] / raw.close.iloc[idx - 1] - 1)


def test_size_guard_rejects_before_pandas(history_warehouse, monkeypatch):
    from investment_lab.research import frame

    real_connect = frame.duckdb.connect

    class NoPandasConnection:
        def __init__(self, con):
            self.con = con

        def execute(self, *args, **kwargs):
            self.con.execute(*args, **kwargs)
            return self

        def df(self):
            pytest.fail("An oversized result must never reach pandas")

        def __getattr__(self, name):
            return getattr(self.con, name)

    monkeypatch.setattr(frame.duckdb, "connect", lambda *a, **kw: NoPandasConnection(real_connect(*a, **kw)))
    with pytest.raises(ValueError, match="exceeds max_rows=2"):
        load_research_frame(history_warehouse, symbols=["OLD"], max_rows=2)


def test_guard_counts_filtered_results_and_allows_exact_limit(history_warehouse):
    frame = load_research_frame(history_warehouse, symbols=["NEW"], start_date="2024-02-01",
                                end_date="2024-02-01", max_rows=1, include_labels=True)
    assert len(frame) == 1
    assert frame.label_return_21d.notna().all()
    assert load_research_frame(history_warehouse, symbols=[], max_rows=1).empty
    with pytest.raises(ValueError, match="exceeds max_rows=1"):
        load_research_frame(history_warehouse, symbols=["NEW"], max_rows=1)
    assert len(load_research_frame(history_warehouse, max_rows=1000)) > 1


@pytest.mark.parametrize("max_rows", [0, -1, None, True, 1.5])
def test_guard_requires_positive_integer(tmp_path, max_rows):
    with pytest.raises(ValueError, match="positive integer"):
        load_research_frame(tmp_path, max_rows=max_rows)
