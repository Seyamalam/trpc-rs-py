#!/usr/bin/env python3
"""Validate docs/feature-matrix.md drift.

MISSION.md section 14 requires every cell to carry a support level and a
link to the test that proves it. The cells are annotated by hand (the
annotations carry context a generator cannot produce), so this script is
the drift guard: it fails if a linked test file disappears, a cell uses
an unknown level, or a column is empty.

Run: uv run python scripts/check_matrix.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "docs" / "feature-matrix.md"
LEVELS = {
    "supported", "partial", "planned", "not supported", "same", "n/a",
    "not needed",
}


def main() -> int:
    errors: list[str] = []
    rows = 0
    links = 0
    for lineno, line in enumerate(MATRIX.read_text().split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("|") or "Feature |" in stripped or "---" in stripped:
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            errors.append(f"line {lineno}: fewer than 3 cells: {stripped!r}")
            continue
        rows += 1
        for col, cell in enumerate(cells[1:], 1):
            if not cell:
                errors.append(f"line {lineno} col {col}: empty cell")
                continue
            # Cells are `[level](link)` markdown links or bare levels with
            # optional parenthesized annotations.
            link_level = re.match(r"\[([^\]]+)\]", cell)
            if link_level:
                level = link_level.group(1).strip().lower()
            else:
                level = cell.split("(")[0].strip().lower()
            if not any(level.startswith(lvl) for lvl in LEVELS):
                errors.append(f"line {lineno} col {col}: unknown level {level!r}")
            for match in re.finditer(r"https://github\.com/Seyamalam/trpc-rs-py/blob/\S+?/(\S+?)\)", cell):
                rel = match.group(1)
                links += 1
                if not (ROOT / rel).exists():
                    errors.append(f"line {lineno} col {col}: missing file {rel}")
    if errors:
        print(f"feature-matrix: {len(errors)} problem(s):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"feature-matrix: ok ({rows} rows, {links} test links verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
