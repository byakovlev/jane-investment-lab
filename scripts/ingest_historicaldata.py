from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.pipeline import ingest_historicaldata_net


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest HistoricalData.net daily archive into Investment Lab V0.2")
    p.add_argument("source", type=Path, help="Unpacked vendor directory or the free sample zip")
    p.add_argument("--version-label", default="sample-2022H2")
    p.add_argument("--source-asof", default="2022-12-31")
    p.add_argument("--project-root", type=Path, default=ROOT)
    args = p.parse_args()
    project_root = args.project_root.resolve()

    source = args.source.resolve()
    if zipfile.is_zipfile(source):
        h = hashlib.sha256(source.read_bytes()).hexdigest()
        raw_dir = project_root / "warehouse" / "raw" / "historicaldata_net" / f"{source.stem}-{h[:12]}"
        if not raw_dir.exists():
            raw_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as z:
                z.extractall(raw_dir)
        result = ingest_historicaldata_net(raw_dir, project_root, args.version_label, args.source_asof, extract_sample=True)
    else:
        result = ingest_historicaldata_net(source, project_root, args.version_label, args.source_asof, extract_sample=False)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
