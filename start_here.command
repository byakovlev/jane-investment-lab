#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating local Python environment..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [ ! -f warehouse/metadata.duckdb ]; then
  echo "Ingesting the included vendor sample..."
  .venv/bin/python scripts/ingest_historicaldata.py vendor_samples/historicaldata_net_sample_2022H2.zip
fi
echo "Running tests..."
.venv/bin/pytest -q
echo "Opening Hypothesis Lab..."
exec .venv/bin/jupyter lab notebooks/Hypothesis_Lab.ipynb
