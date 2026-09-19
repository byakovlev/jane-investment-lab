from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.providers.historicaldata_net import build_lifecycles, parse_day_filename, verify_vendor_delivery

SAMPLE = ROOT / "vendor_samples" / "historicaldata_net_sample_2022H2.zip"


def unpack():
    d = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(SAMPLE) as z:
        z.extractall(d)
    return d


def test_filename_lifecycle_parsing():
    assert parse_day_filename(Path("TSLA_day.csv")) == ("TSLA", None)
    assert parse_day_filename(Path("TWTR_day_delisted_2022-10-31.csv")) == ("TWTR", "2022-10-31")


def test_sample_vendor_verifier_passes():
    d = unpack()
    try:
        ok, output = verify_vendor_delivery(d, extract=True)
        assert ok, output
    finally:
        shutil.rmtree(d)


def test_sample_has_four_lifecycles_and_delisted_twitter():
    d = unpack()
    try:
        lifecycles = build_lifecycles(d)
        assert len(lifecycles) == 4
        twtr = next(x for x in lifecycles if x.terminal_symbol == "TWTR")
        assert twtr.delisted_at == "2022-10-31"
        assert twtr.status == "delisted"
        assert all(x.metadata_completeness == "PROVISIONAL" for x in lifecycles)
    finally:
        shutil.rmtree(d)
