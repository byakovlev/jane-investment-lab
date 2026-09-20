from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.providers.historicaldata_net import build_lifecycles, parse_day_filename, verify_vendor_delivery, classify_verifier_failures

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

def test_filename_map_resolves_original_vendor_name(tmp_path):
    root = tmp_path
    day_dir = root / "day_by_symbol"
    day_dir.mkdir()

    (day_dir / "AAPw_day_2.csv").write_text(
        "date,open,high,low,close,volume,vwap,transactions,"
        "adj_open,adj_high,adj_low,adj_close,adj_volume,adj_vwap,"
        "dividend,dividend_type,split\n"
        "2020-01-02,1,1,1,1,100,1,1,1,1,1,1,100,1,,,\n"
    )

    (root / "filename-map.json").write_text(
        """
        {
          "files": [
            {
              "original_name": "day_by_symbol/AAPw_day.csv",
              "local_name": "day_by_symbol/AAPw_day_2.csv",
              "bytes": 0,
              "sha256": "dummy",
              "active": true
            }
          ]
        }
        """
    )

    lifecycles = build_lifecycles(root)

    assert len(lifecycles) == 1
    assert lifecycles[0].local_file_name == "AAPw_day_2.csv"
    assert lifecycles[0].source_file_name == "AAPw_day.csv"
    assert lifecycles[0].terminal_symbol == "AAPw"

def test_verifier_failure_classification():
    output = """
    FAIL  [AMCR_day.csv] adjustment chain breaks at 1 boundaries (first: 2019-06-11)
    FAIL  [SPAB_day_delisted_2009-05-04.csv] adjustment factor inconsistent on 4 rows
    FAIL  [day_by_symbol/manifest.json] manifest lists AAPw_day.csv but it is missing
    FAIL  [something.csv] unexpected structural problem
    """

    adjustment, missing, other = classify_verifier_failures(output)

    assert adjustment == {
        "AMCR_day.csv",
        "SPAB_day_delisted_2009-05-04.csv",
    }
    assert missing == {"AAPw_day.csv"}
    assert other == [
        "FAIL  [something.csv] unexpected structural problem"
    ]

def test_ticker_interval_ends_use_observed_dates():
    from dataclasses import replace
    from investment_lab.providers.historicaldata_net import Lifecycle, parse_symbol_history

    lifecycle = Lifecycle(
        source_security_key="FIGI:TEST", security_id=1, terminal_symbol="NEW",
        status="delisted", delisted_at="2025-06-09", name="Test", instrument_type="ETF",
        exchange="BATS", cik=None, figi="TEST", symbol_history=None,
        metadata_completeness="FULL", local_file_name="NEW_day.csv", source_file_name="NEW_day.csv",
    )
    assert parse_symbol_history(lifecycle, "2025-01-02", "2025-06-05") == [
        ("NEW", "2025-01-02", "2025-06-05"),
    ]
    with_history = replace(lifecycle, symbol_history="OLD:2024-01-02|NEW:2025-01-02")
    assert parse_symbol_history(with_history, "2024-01-02", "2025-06-05") == [
        ("OLD", "2024-01-02", "2025-01-01"),
        ("NEW", "2025-01-02", "2025-06-05"),
    ]
    for lc in (lifecycle, with_history):
        active = replace(lc, status="active", delisted_at=None)
        assert parse_symbol_history(active, "2025-01-02", "2025-06-05")[-1] == (
            "NEW", "2025-01-02", None,
        )
