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


import pytest


@pytest.mark.parametrize("symbol,active_figi,renamed_figi", [
    ("XPER", "BBG019FGSSM1", "BBG00RBFBL50"),
    ("BAM", "BBG01BPHNXZ3", "BBG000C9KL89"),
    ("CR", "BBG016G0L0Q5", "BBG017BXPZ85"),
    ("MSGE", "BBG019980TD4", "BBG00L9HLWV8"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_active_lifecycle_wins_over_renamed_holder(tmp_path, symbol, active_figi, renamed_figi, reverse):
    import csv
    import json

    (tmp_path / "day_by_symbol").mkdir()
    # Selection must use the mapped source filename, not the local spelling.
    (tmp_path / "day_by_symbol" / "LOCAL_day.csv").touch()
    (tmp_path / "filename-map.json").write_text(json.dumps([
        {"local_name": "day_by_symbol/LOCAL_day.csv", "original_name": f"day_by_symbol/{symbol}_day.csv"}
    ]))
    rows = [
        {"symbol": symbol, "status": "active", "delisted_at": "", "figi": active_figi},
        {"symbol": symbol, "status": "renamed", "delisted_at": "", "figi": renamed_figi},
        {"symbol": symbol, "status": "delisted", "delisted_at": "2020-01-02", "figi": "OLDER"},
    ]
    with (tmp_path / "symbols.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(list(reversed(rows)) if reverse else rows)
    lc, = build_lifecycles(tmp_path)
    assert lc.source_security_key == f"FIGI:{active_figi}"
    assert lc.status == "active"
    assert lc.source_file_name == f"{symbol}_day.csv"
    assert lc.local_file_name == "LOCAL_day.csv"


@pytest.mark.parametrize("delisted", [None, "2020-01-02"])
def test_equally_matching_lifecycle_rows_are_explicitly_ambiguous(tmp_path, delisted):
    import csv

    name = "TEST_day" + (f"_delisted_{delisted}" if delisted else "") + ".csv"
    (tmp_path / "day_by_symbol").mkdir()
    (tmp_path / "day_by_symbol" / name).touch()
    rows = [{"symbol": "TEST", "delisted_at": delisted or "", "status": "delisted" if delisted else "active", "figi": figi}
            for figi in ("FIRST", "SECOND")]
    with (tmp_path / "symbols.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="Ambiguous symbols.csv") as exc:
        build_lifecycles(tmp_path)
    assert name in str(exc.value)
    assert "FIRST" in str(exc.value) and "SECOND" in str(exc.value)
    assert '"symbols_csv_line": 2' in str(exc.value)
    assert '"symbols_csv_line": 3' in str(exc.value)


def test_delisted_lifecycle_uses_exact_date_and_status(tmp_path):
    (tmp_path / "day_by_symbol").mkdir()
    (tmp_path / "day_by_symbol" / "TEST_day_delisted_2020-01-02.csv").touch()
    (tmp_path / "symbols.csv").write_text(
        "symbol,delisted_at,status,figi\n"
        "TEST,,active,CURRENT\n"
        "TEST,2020-01-02,delisted,CORRECT\n"
        "TEST,2020-01-02,renamed,WRONG_STATUS\n"
        "TEST,2019-01-02,delisted,WRONG_DATE\n"
    )
    lc, = build_lifecycles(tmp_path)
    assert lc.source_security_key == "FIGI:CORRECT"
