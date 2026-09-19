from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investment_lab.research.runner import format_result, run_hypothesis


def main() -> None:
    p = argparse.ArgumentParser(description="Run one saved Investment Lab hypothesis")
    p.add_argument(
        "hypothesis",
        nargs="?",
        type=Path,
        default=ROOT / "hypotheses" / "three_up_days_then_next_day_up.json",
    )
    args = p.parse_args()
    result = run_hypothesis(args.hypothesis, root=ROOT)
    print(format_result(result))


if __name__ == "__main__":
    main()
