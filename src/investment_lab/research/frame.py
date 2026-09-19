from pathlib import Path

import duckdb
import pandas as pd


def load_research_frame(
    root=None,
    symbols=None,
    start_date=None,
    end_date=None,
    include_labels=False,
):
    """
    Canonical dataframe for feature research and backtesting.

    Default columns contain only information available at or before
    the end of each trading day.

    Future returns are included only when include_labels=True.
    """

    root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    db_path = root / "warehouse" / "metadata.duckdb"

    if not db_path.exists():
        raise FileNotFoundError(f"Warehouse not found: {db_path}")

    con = duckdb.connect(str(db_path), read_only=True)

    try:
        version_rows = con.execute(
            "SELECT DISTINCT source_dataset_version_id FROM bars_daily_current"
        ).fetchall()

        if len(version_rows) != 1:
            raise RuntimeError(
                f"Expected one current dataset version; found {version_rows}"
            )

        dataset_version_id = int(version_rows[0][0])

        sql = f"""
        WITH base AS (
            SELECT
                r.security_id,
                coalesce(h.identifier_value, r.source_symbol) AS ticker,
                s.security_name,
                s.instrument_type,
                r.trade_date,
                r.open,
                r.high,
                r.low,
                r.close,
                r.volume,
                r.vwap,
                r.transactions,
                a.adj_close,
                r.availability_rule,
                r.source_dataset_version_id
            FROM bars_daily_current r
            LEFT JOIN vendor_adjusted_daily_current a
              ON r.security_id = a.security_id
             AND r.trade_date = a.trade_date
             AND r.source_dataset_version_id = a.source_dataset_version_id
            LEFT JOIN security s
              ON r.security_id = s.security_id
            LEFT JOIN security_identifier_history h
              ON h.security_id = r.security_id
             AND h.source_dataset_version_id = {dataset_version_id}
             AND h.identifier_type = 'TICKER'
             AND r.trade_date >= h.valid_from
             AND (h.valid_to IS NULL OR r.trade_date <= h.valid_to)
        ),

        features AS (
            SELECT
                *,
                close / open - 1 AS intraday_return,

                adj_close /
                    lag(adj_close, 1) OVER (
                        PARTITION BY security_id ORDER BY trade_date
                    ) - 1 AS return_1d,

                adj_close /
                    lag(adj_close, 5) OVER (
                        PARTITION BY security_id ORDER BY trade_date
                    ) - 1 AS return_5d,

                adj_close /
                    lag(adj_close, 21) OVER (
                        PARTITION BY security_id ORDER BY trade_date
                    ) - 1 AS return_21d,

                volume /
                    avg(volume) OVER (
                        PARTITION BY security_id
                        ORDER BY trade_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) - 1 AS volume_vs_20d_avg,

                (high - low) / close AS daily_range_pct,
                close * volume AS dollar_volume
            FROM base
        )

        SELECT *
        FROM features
        """

        df = con.execute(sql).df()

    finally:
        con.close()

    # Labels must be calculated before date filtering so future outcomes
    # are still available at the edge of a requested research period.
    if include_labels:
        df = df.sort_values(["security_id", "trade_date"])

        group = df.groupby("security_id")["adj_close"]

        df["label_return_1d"] = group.shift(-1) / df["adj_close"] - 1
        df["label_return_5d"] = group.shift(-5) / df["adj_close"] - 1
        df["label_return_21d"] = group.shift(-21) / df["adj_close"] - 1

    if symbols is not None:
        df = df[df["ticker"].isin(symbols)]

    if start_date is not None:
        df = df[df["trade_date"] >= pd.Timestamp(start_date).date()]

    if end_date is not None:
        df = df[df["trade_date"] <= pd.Timestamp(end_date).date()]

    # Adjusted price levels are never prediction features.
    df = df.drop(columns=["adj_close"])

    return df.reset_index(drop=True)
