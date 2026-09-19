PY=.venv/bin/python
PIP=.venv/bin/pip
PYTEST=.venv/bin/pytest
JUPYTER=.venv/bin/jupyter
SAMPLE=vendor_samples/historicaldata_net_sample_2022H2.zip
HYPOTHESIS=hypotheses/three_up_days_then_next_day_up.json

.PHONY: setup ingest test hypothesis experiments lab all clean

setup:
	python3 -m venv .venv
	$(PIP) install -r requirements.txt

ingest:
	$(PY) scripts/ingest_historicaldata.py $(SAMPLE)

test:
	$(PYTEST) -q

hypothesis:
	$(PY) scripts/run_hypothesis.py $(HYPOTHESIS)

experiments:
	$(PY) scripts/list_experiments.py

lab:
	$(JUPYTER) lab notebooks/Hypothesis_Lab.ipynb

all: ingest test hypothesis

clean:
	rm -rf warehouse results/* .pytest_cache
