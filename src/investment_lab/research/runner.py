from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

import duckdb

from investment_lab.ids import stable_bigint

ENGINE_VERSION = "investment-lab-research-v0.3"
SUPPORTED_TYPE = "consecutive_return_continuation_v1"


@dataclass(frozen=True)
class ResearchResult:
    experiment_id: int
    hypothesis_name: str
    hypothesis_text: str
    dataset_version_id: int
    n_eligible_observations: int
    n_signals: int
    n_successes: int
    hit_rate: float | None
    baseline_hit_rate: float | None
    hit_rate_lift_pp: float | None
    mean_outcome_return: float | None
    median_outcome_return: float | None
    signal_return_threshold: float
    consecutive_days: int
    outcome_horizon_days: int
    outcome_return_threshold: float
    result_json: str
    events_csv: str


def load_hypothesis(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    spec = json.loads(path.read_text())
    validate_hypothesis(spec)
    return spec


def validate_hypothesis(spec: dict[str, Any]) -> None:
    if spec.get("type") != SUPPORTED_TYPE:
        raise ValueError(f"V0.3 supports type={SUPPORTED_TYPE!r}; got {spec.get('type')!r}")
    for key in ("name", "hypothesis_text", "signal", "outcome", "universe"):
        if key not in spec:
            raise ValueError(f"Missing required hypothesis field: {key}")
    signal = spec["signal"]
    outcome = spec["outcome"]
    if int(signal.get("consecutive_days", 0)) < 1:
        raise ValueError("signal.consecutive_days must be >= 1")
    if float(signal.get("daily_return_gt", -2)) <= -1:
        raise ValueError("signal.daily_return_gt must be > -1")
    if int(outcome.get("horizon_trading_days", 0)) < 1:
        raise ValueError("outcome.horizon_trading_days must be >= 1")
    if spec.get("return_basis") != "vendor_adjusted_close":
        raise ValueError("V0.3 intentionally supports only return_basis='vendor_adjusted_close'")


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _ensure_warehouse(root: Path) -> Path:
    db = root / "warehouse" / "metadata.duckdb"
    if not db.exists():
        raise FileNotFoundError(
            f"{db} does not exist. First run: python scripts/ingest_historicaldata.py "
            "vendor_samples/historicaldata_net_sample_2022H2.zip"
        )
    return db


def _effective_symbol_cte(dataset_version_id: int) -> str:
    return f"""
    SELECT
        a.*,
        coalesce(h.identifier_value, a.source_symbol) AS ticker
    FROM vendor_adjusted_daily_current a
    LEFT JOIN security_identifier_history h
      ON h.security_id = a.security_id
     AND h.source_dataset_version_id = {dataset_version_id}
     AND h.identifier_type = 'TICKER'
     AND a.trade_date >= h.valid_from
     AND (h.valid_to IS NULL OR a.trade_date <= h.valid_to)
    """


def _build_sql(spec: dict[str, Any], dataset_version_id: int) -> str:
    n = int(spec["signal"]["consecutive_days"])
    signal_threshold = float(spec["signal"]["daily_return_gt"])
    horizon = int(spec["outcome"]["horizon_trading_days"])

    lag_conditions = [f"daily_return > {signal_threshold:.17g}"]
    for k in range(1, n):
        lag_conditions.append(f"lag(daily_return, {k}) OVER w > {signal_threshold:.17g}")
    signal_expr = " AND ".join(lag_conditions)

    symbols = spec.get("universe", {}).get("symbols") or []
    symbol_filter = ""
    if symbols:
        symbol_filter = "WHERE ticker IN (" + ",".join(_sql_string(str(s)) for s in symbols) + ")"

    # Vendor-adjusted prices are used only to calculate realized returns. Future archive
    # adjustments multiply earlier adjacent closes by the same factor, while splits/dividends
    # at their boundary are normalized. We do NOT expose adjusted price levels as signals.
    return f"""
    WITH symbolized AS (
        {_effective_symbol_cte(dataset_version_id)}
    ),
    filtered AS (
        SELECT * FROM symbolized
        {symbol_filter}
    ),
    daily AS (
        SELECT
            security_id,
            ticker,
            trade_date,
            adj_close,
            adj_close / lag(adj_close) OVER (
                PARTITION BY security_id ORDER BY trade_date
            ) - 1.0 AS daily_return,
            lead(adj_close, {horizon}) OVER (
                PARTITION BY security_id ORDER BY trade_date
            ) / adj_close - 1.0 AS outcome_return
        FROM filtered
    ),
    scored AS (
        SELECT
            *,
            ({signal_expr}) AS signal
        FROM daily
        WINDOW w AS (PARTITION BY security_id ORDER BY trade_date)
    )
    SELECT
        security_id,
        ticker,
        trade_date,
        daily_return,
        outcome_return,
        signal
    FROM scored
    WHERE outcome_return IS NOT NULL
    ORDER BY ticker, trade_date
    """


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["ticker", "signal_date", "signal_day_return", "next_horizon_return", "success"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events)


def run_hypothesis(spec_or_path: dict[str, Any] | str | Path, root: str | Path | None = None) -> ResearchResult:
    if isinstance(spec_or_path, (str, Path)):
        spec = load_hypothesis(spec_or_path)
    else:
        spec = dict(spec_or_path)
        validate_hypothesis(spec)

    root_path = Path(root) if root else Path(__file__).resolve().parents[3]
    db_path = _ensure_warehouse(root_path)
    con = duckdb.connect(str(db_path))

    try:
        version_rows = con.execute("SELECT DISTINCT source_dataset_version_id FROM vendor_adjusted_daily_current").fetchall()
        if len(version_rows) != 1:
            raise RuntimeError(f"Expected one current dataset version; found {version_rows}")
        dataset_version_id = int(version_rows[0][0])

        sql = _build_sql(spec, dataset_version_id)
        rows = con.execute(sql).fetchall()
        columns = [d[0] for d in con.description]
        records = [dict(zip(columns, r)) for r in rows]

        outcome_threshold = float(spec["outcome"]["return_gt"])
        signal_records = [r for r in records if bool(r["signal"])]
        successes = sum(float(r["outcome_return"]) > outcome_threshold for r in signal_records)
        baseline_successes = sum(float(r["outcome_return"]) > outcome_threshold for r in records)

        n = len(signal_records)
        n_eligible = len(records)
        hit_rate = successes / n if n else None
        baseline_hit_rate = baseline_successes / n_eligible if n_eligible else None
        lift = (hit_rate - baseline_hit_rate) * 100 if hit_rate is not None and baseline_hit_rate is not None else None
        outcome_returns = [float(r["outcome_return"]) for r in signal_records]
        ci_low, ci_high = _wilson_interval(successes, n)

        now = datetime.now(timezone.utc)
        spec_json = _canonical_json(spec)
        hypothesis_id = stable_bigint("hypothesis", spec["name"])
        strategy_spec_id = stable_bigint("strategy_spec", spec_json)
        experiment_id = stable_bigint("experiment", f"{strategy_spec_id}|{dataset_version_id}|{now.isoformat()}")

        result_dir = root_path / "results" / str(experiment_id)
        result_dir.mkdir(parents=True, exist_ok=True)
        events_path = result_dir / "events.csv"
        result_path = result_dir / "result.json"
        sql_path = result_dir / "compiled.sql"

        events = [
            {
                "ticker": r["ticker"],
                "signal_date": str(r["trade_date"]),
                "signal_day_return": float(r["daily_return"]),
                "next_horizon_return": float(r["outcome_return"]),
                "success": bool(float(r["outcome_return"]) > outcome_threshold),
            }
            for r in signal_records
        ]
        _write_events(events_path, events)
        sql_path.write_text(sql)

        payload = {
            "experiment_id": experiment_id,
            "engine_version": ENGINE_VERSION,
            "created_at": now.isoformat(),
            "dataset_version_id": dataset_version_id,
            "hypothesis": spec,
            "semantics": {
                "daily_return": "close-to-close return from vendor adjusted close",
                "signal_known": "after the third (or Nth) signal-day close",
                "outcome": f"close-to-close return over the next {int(spec['outcome']['horizon_trading_days'])} trading day(s)",
                "adjusted_price_warning": "Adjusted price LEVELS are not point-in-time features; used here only for realized return normalization.",
            },
            "metrics": {
                "eligible_observations": n_eligible,
                "signals": n,
                "successes": successes,
                "hit_rate": hit_rate,
                "hit_rate_wilson_95_low": ci_low,
                "hit_rate_wilson_95_high": ci_high,
                "baseline_hit_rate": baseline_hit_rate,
                "hit_rate_lift_percentage_points": lift,
                "mean_outcome_return": mean(outcome_returns) if outcome_returns else None,
                "median_outcome_return": median(outcome_returns) if outcome_returns else None,
            },
            "artifacts": {
                "events_csv": str(events_path.relative_to(root_path)),
                "compiled_sql": str(sql_path.relative_to(root_path)),
            },
        }
        result_path.write_text(json.dumps(payload, indent=2, default=str))

        con.execute(
            "INSERT OR IGNORE INTO hypothesis (hypothesis_id, hypothesis_text, created_at) VALUES (?, ?, ?)",
            [hypothesis_id, spec["hypothesis_text"], now],
        )
        existing_spec = con.execute(
            "SELECT version FROM strategy_spec WHERE strategy_spec_id=?", [strategy_spec_id]
        ).fetchone()
        if existing_spec is None:
            next_version = con.execute(
                "SELECT coalesce(max(version), 0) + 1 FROM strategy_spec WHERE hypothesis_id=?",
                [hypothesis_id],
            ).fetchone()[0]
            con.execute(
                "INSERT INTO strategy_spec (strategy_spec_id, hypothesis_id, version, spec_json, created_at, confirmed_at) VALUES (?, ?, ?, ?, ?, ?)",
                [strategy_spec_id, hypothesis_id, next_version, spec_json, now, now],
            )
        con.execute(
            "INSERT INTO experiment (experiment_id, strategy_spec_id, dataset_version_id, universe_id, engine_version, started_at, finished_at, status, result_uri, manifest_json) VALUES (?, ?, ?, NULL, ?, ?, ?, 'SUCCEEDED', ?, ?)",
            [experiment_id, strategy_spec_id, dataset_version_id, ENGINE_VERSION, now, now, str(result_path.relative_to(root_path)), json.dumps({"compiled_sql": str(sql_path.relative_to(root_path))})],
        )
        metric_values = {
            "eligible_observations": float(n_eligible),
            "signals": float(n),
            "successes": float(successes),
            "hit_rate": hit_rate,
            "baseline_hit_rate": baseline_hit_rate,
            "hit_rate_lift_percentage_points": lift,
            "mean_outcome_return": mean(outcome_returns) if outcome_returns else None,
            "median_outcome_return": median(outcome_returns) if outcome_returns else None,
        }
        for metric_name, metric_value in metric_values.items():
            con.execute(
                "INSERT INTO experiment_metric (experiment_id, metric_name, metric_value, metric_json) VALUES (?, ?, ?, NULL)",
                [experiment_id, metric_name, metric_value],
            )

        return ResearchResult(
            experiment_id=experiment_id,
            hypothesis_name=spec["name"],
            hypothesis_text=spec["hypothesis_text"],
            dataset_version_id=dataset_version_id,
            n_eligible_observations=n_eligible,
            n_signals=n,
            n_successes=successes,
            hit_rate=hit_rate,
            baseline_hit_rate=baseline_hit_rate,
            hit_rate_lift_pp=lift,
            mean_outcome_return=mean(outcome_returns) if outcome_returns else None,
            median_outcome_return=median(outcome_returns) if outcome_returns else None,
            signal_return_threshold=float(spec["signal"]["daily_return_gt"]),
            consecutive_days=int(spec["signal"]["consecutive_days"]),
            outcome_horizon_days=int(spec["outcome"]["horizon_trading_days"]),
            outcome_return_threshold=outcome_threshold,
            result_json=str(result_path.relative_to(root_path)),
            events_csv=str(events_path.relative_to(root_path)),
        )
    finally:
        con.close()


def format_result(result: ResearchResult) -> str:
    if result.n_signals == 0:
        conclusion = "No matching signal occurred in this dataset, so this sample cannot test the hypothesis yet."
    else:
        conclusion = (
            f"{result.n_successes}/{result.n_signals} signals succeeded "
            f"({_pct(result.hit_rate)}), versus baseline {_pct(result.baseline_hit_rate)}."
        )
    return "\n".join([
        f"Hypothesis: {result.hypothesis_text}",
        f"Eligible stock-days: {result.n_eligible_observations}",
        f"Signals: {result.n_signals}",
        f"Successes: {result.n_successes}",
        f"Signal hit rate: {_pct(result.hit_rate)}",
        f"Baseline next-period hit rate: {_pct(result.baseline_hit_rate)}",
        f"Lift: {'n/a' if result.hit_rate_lift_pp is None else f'{result.hit_rate_lift_pp:+.2f} pp'}",
        f"Mean outcome return: {_pct(result.mean_outcome_return)}",
        "",
        conclusion,
        f"Result JSON: {result.result_json}",
        f"Signal events: {result.events_csv}",
    ])
