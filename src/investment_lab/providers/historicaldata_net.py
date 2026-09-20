from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from investment_lab.ids import stable_bigint

PROVIDER = "historicaldata.net"
DAY_FILE_RE = re.compile(r"^(?P<symbol>.+)_day(?:_delisted_(?P<delisted>\d{4}-\d{2}-\d{2}))?\.csv$")


@dataclass(frozen=True)
class Lifecycle:
    source_security_key: str
    security_id: int
    terminal_symbol: str
    status: str
    delisted_at: str | None
    name: str
    instrument_type: str
    exchange: str
    cik: str | None
    figi: str | None
    symbol_history: str | None
    metadata_completeness: str
    local_file_name: str
    source_file_name: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(root: Path) -> dict | None:
    path = root / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else None

def load_filename_map(root: Path) -> dict[str, str]:
    path = root / "filename-map.json"
    if not path.exists():
        return {}

    payload = json.loads(path.read_text())
    mapping: dict[str, str] = {}

    def walk(obj) -> None:
        if isinstance(obj, dict):
            original = obj.get("original_name")
            local = obj.get("local_name")

            if original and local and obj.get("active", True):
                local_key = str(local).replace("\\", "/")
                original_value = str(original).replace("\\", "/")
                mapping[local_key] = original_value

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)
    return mapping

def verify_vendor_delivery(root: Path, extract: bool = False) -> tuple[bool, str]:
    verifier = root / "verify.py"
    if not verifier.exists():
        return False, "verify.py not found"
    cmd = [sys.executable, str(verifier), "--strict"]
    if extract:
        cmd.append("--extract")
    # Full deliveries have a collection manifest under day_by_symbol/. Validate only
    # the grain we ingest; the free sample has one root manifest covering both grains.
    daily_collection = root / "day_by_symbol"
    target = daily_collection if (daily_collection / "manifest.json").exists() else root
    cmd.append(str(target))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output

def classify_verifier_failures(
    output: str,
) -> tuple[set[str], set[str], list[str]]:
    adjustment_files: set[str] = set()
    manifest_missing_files: set[str] = set()
    other_failures: list[str] = []

    adjustment_re = re.compile(
        r"^FAIL\s+\[(?P<name>[^\]]+)\]\s+adjustment "
        r"(?:chain breaks|factor inconsistent)"
    )
    manifest_re = re.compile(
        r"^FAIL\s+\[day_by_symbol/manifest\.json\]\s+"
        r"manifest lists (?P<name>\S+) but it is missing"
    )

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if not re.match(r"^FAIL\s+\[", line):
            continue

        match = adjustment_re.match(line)
        if match:
            adjustment_files.add(match.group("name"))
            continue

        match = manifest_re.match(line)
        if match:
            manifest_missing_files.add(match.group("name"))
            continue

        other_failures.append(line)

    return adjustment_files, manifest_missing_files, other_failures

def day_files(root: Path) -> list[Path]:
    folder = root / "day_by_symbol"
    if not folder.exists():
        raise FileNotFoundError(f"Missing {folder}")
    files = sorted(folder.glob("*_day*.csv"))
    if not files:
        raise FileNotFoundError(f"No daily files found under {folder}")
    return files


def parse_day_filename(path: Path) -> tuple[str, str | None]:
    match = DAY_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Unexpected HistoricalData.net daily filename: {path.name}")
    return match.group("symbol"), match.group("delisted")


def _symbols_rows(root: Path) -> list[dict[str, str]]:
    path = root / "symbols.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="ascii") as f:
        return list(csv.DictReader(f))


def _source_key(row: dict[str, str], terminal_symbol: str, delisted_at: str | None) -> str:
    # FIGI is the strongest instrument identifier supplied by this vendor.
    # Fall back deterministically so the sample (which has no symbols.csv) still ingests.
    figi = (row.get("figi") or "").strip()
    cik = (row.get("cik") or "").strip()
    if figi:
        return f"FIGI:{figi}"
    if cik:
        return f"CIK:{cik}|SYMBOL:{terminal_symbol}|DELISTED:{delisted_at or ''}"
    return f"SYMBOL:{terminal_symbol}|DELISTED:{delisted_at or ''}"


def build_lifecycles(root: Path) -> list[Lifecycle]:
    files = day_files(root)
    symbol_rows = _symbols_rows(root)
    filename_map = load_filename_map(root)

    rows_by_key: dict[tuple[str, str | None], dict[str, str]] = {}
    for row in symbol_rows:
        symbol = (row.get("symbol") or "").strip()
        delisted = (row.get("delisted_at") or "").strip() or None
        rows_by_key[(symbol, delisted)] = row

    result: list[Lifecycle] = []
    for path in files:
        local_rel = path.relative_to(root).as_posix()
        original_rel = filename_map.get(local_rel, local_rel)
        original_name = Path(original_rel).name

        symbol, delisted = parse_day_filename(Path(original_name))
        row = rows_by_key.get((symbol, delisted), {})
        status = (row.get("status") or ("delisted" if delisted else "active")).strip()
        source_key = _source_key(row, symbol, delisted)
        result.append(
            Lifecycle(
                source_security_key=source_key,
                security_id=stable_bigint(PROVIDER, source_key),
                terminal_symbol=symbol,
                status=status,
                delisted_at=delisted,
                name=(row.get("name") or symbol).strip(),
                instrument_type=(row.get("type") or "UNKNOWN").strip(),
                exchange=(row.get("exchange") or "UNKNOWN").strip(),
                cik=(row.get("cik") or "").strip() or None,
                figi=(row.get("figi") or "").strip() or None,
                symbol_history=(row.get("symbol_history") or "").strip() or None,
                metadata_completeness="FULL" if row else "PROVISIONAL",
                local_file_name=path.name,
                source_file_name=original_name,
            )
        )
    return result


def parse_symbol_history(lifecycle: Lifecycle, first_date: str, last_date: str) -> list[tuple[str, str, str | None]]:
    """Return (symbol, valid_from, valid_to) intervals.

    symbols.csv uses SYMBOL:from_date|SYMBOL2:from_date. The free sample does
    not ship symbols.csv, so its terminal symbol is provisional over the file range.
    Delisted ticker intervals end on the last observed trading date; delisted_at
    is an administrative lifecycle date, not an inclusive trading boundary.
    """
    if not lifecycle.symbol_history:
        end = last_date if lifecycle.delisted_at else None
        return [(lifecycle.terminal_symbol, first_date, end)]

    items: list[tuple[str, str]] = []
    for token in lifecycle.symbol_history.split("|"):
        symbol, from_date = token.rsplit(":", 1)
        items.append((symbol, from_date))
    intervals: list[tuple[str, str, str | None]] = []
    from datetime import date, timedelta
    for i, (symbol, start) in enumerate(items):
        if i + 1 < len(items):
            next_start = date.fromisoformat(items[i + 1][1])
            end = (next_start - timedelta(days=1)).isoformat()
        else:
            end = last_date if lifecycle.delisted_at else None
        intervals.append((symbol, start, end))
    return intervals


def source_fingerprint(root: Path) -> str:
    """Fingerprint exactly the daily inputs consumed by this adapter.

    Full deliveries have day_by_symbol/manifest.json. The free sample has a root
    manifest that also lists minute files, so we hash only its day_by_symbol entries.
    symbols.csv is included when present because it affects identity metadata.
    """
    h = hashlib.sha256()
    daily_manifest = root / "day_by_symbol" / "manifest.json"
    if daily_manifest.exists():
        h.update(b"day_by_symbol/manifest.json\0")
        h.update(daily_manifest.read_bytes())
    else:
        root_manifest = root / "manifest.json"
        if root_manifest.exists():
            payload = json.loads(root_manifest.read_text())
            daily_items = [x for x in payload.get("files", []) if str(x.get("name", "")).startswith("day_by_symbol/")]
            for item in sorted(daily_items, key=lambda x: x["name"]):
                h.update(item["name"].encode("utf-8"))
                h.update(b"\0")
                h.update(str(item.get("bytes", "")).encode("ascii"))
                h.update(b"\0")
                h.update(str(item.get("sha256", "")).encode("ascii"))
                h.update(b"\0")
        else:
            for path in day_files(root):
                h.update(path.name.encode("utf-8"))
                h.update(b"\0")
                h.update(bytes.fromhex(sha256_file(path)))

    symbols = root / "symbols.csv"
    if symbols.exists():
        h.update(b"symbols.csv\0")
        h.update(bytes.fromhex(sha256_file(symbols)))
    return h.hexdigest()


def manifest_file_hashes(root: Path) -> dict[str, tuple[int | None, str | None]]:
    """Return relative path -> (bytes, sha256) from all manifests."""
    out: dict[str, tuple[int | None, str | None]] = {}
    for manifest_path in root.rglob("manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text())
        except Exception:
            continue
        base = manifest_path.parent
        for item in payload.get("files", []):
            name = item.get("name")
            if not name:
                continue
            candidate = (base / name)
            try:
                rel = candidate.relative_to(root).as_posix()
            except ValueError:
                rel = name
            out[rel] = (item.get("bytes"), item.get("sha256"))
            # The free sample's root manifest already includes directory names.
            if (root / name).exists():
                out[name] = (item.get("bytes"), item.get("sha256"))
    return out
