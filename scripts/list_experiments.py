from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb


def main() -> None:
    db = ROOT / "warehouse" / "metadata.duckdb"
    if not db.exists():
        raise SystemExit("No warehouse yet. Run the ingestion command first.")
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("""
        SELECT e.experiment_id, e.finished_at, h.hypothesis_text,
               max(CASE WHEN m.metric_name='signals' THEN m.metric_value END) AS signals,
               max(CASE WHEN m.metric_name='hit_rate' THEN m.metric_value END) AS hit_rate,
               e.result_uri
        FROM experiment e
        JOIN strategy_spec s USING (strategy_spec_id)
        JOIN hypothesis h USING (hypothesis_id)
        LEFT JOIN experiment_metric m USING (experiment_id)
        GROUP BY 1,2,3,6
        ORDER BY e.finished_at DESC
    """).fetchall()
    if not rows:
        print("No experiments yet.")
        return
    for exp_id, finished, text, signals, hit_rate, uri in rows:
        rate = "n/a" if hit_rate is None else f"{hit_rate*100:.2f}%"
        print(f"{exp_id} | {finished} | signals={int(signals or 0)} | hit={rate}\n  {text}\n  {uri}")


if __name__ == "__main__":
    main()
