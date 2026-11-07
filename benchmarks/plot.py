#!/usr/bin/env python3
"""Generate SVG charts from benchmark results into docs/benchmarks/.

Reads benchmarks/results/<version>/*.json (produced by the harness, never
hand-edited). No-op with a notice while no results exist (0.1.0).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
OUT = Path(__file__).parent.parent / "docs" / "benchmarks"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    versions = sorted(d.name for d in RESULTS.iterdir() if d.is_dir())
    if not versions:
        print("plot: no results yet; nothing to chart")
        return 0
    # Chart generation lands with the first real benchmark run (0.2.0).
    for version in versions:
        for path in sorted((RESULTS / version).glob("*.json")):
            json.loads(path.read_text())  # validate shape early
    print("plot: chart generation implemented with the 0.2.0 benchmark suite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
