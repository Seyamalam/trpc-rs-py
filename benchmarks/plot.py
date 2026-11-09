#!/usr/bin/env python3
"""Generate SVG charts from benchmark results into docs/benchmarks/.

Reads benchmarks/results/<version>/*.json produced by benchmarks/run.py
(never hand-edited) and writes dependency-free SVG bar charts. The latest
version's charts are also copied to docs/benchmarks/latest-*.svg so docs
can embed stable paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
OUT = Path(__file__).parent.parent / "docs" / "benchmarks"


def bar_chart(path: Path, title: str, items: list[tuple[str, float]], unit: str) -> None:
    row_h = 30
    left = 300
    width = 980
    height = 70 + row_h * len(items) + 20
    plot_w = width - left - 140
    max_value = max((value for _, value in items), default=0.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="16" y="34" font-size="20" font-weight="bold" fill="#111">{title}</text>',
    ]
    for index, (label, value) in enumerate(items):
        y = 60 + index * row_h
        bar_w = max(1.0, value / max_value * plot_w)
        parts += [
            f'<text x="{left - 10}" y="{y + 15}" font-size="13" text-anchor="end" '
            f'fill="#333">{label}</text>',
            f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="20" fill="#2563eb"/>',
            f'<text x="{left + bar_w + 8:.1f}" y="{y + 15}" font-size="13" fill="#111">'
            f"{value:,.1f} {unit}</text>",
        ]
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n")


def latest_version() -> str | None:
    versions = sorted(d.name for d in RESULTS.iterdir() if d.is_dir()) if RESULTS.exists() else []
    return versions[-1] if versions else None


def chart_rust(version_dir: Path) -> None:
    data = json.loads((version_dir / "rust-micro.json").read_text())
    items = [(name, stats["mean_ns"]) for name, stats in sorted(data.items())]
    bar_chart(OUT / "latest-rust-micro.svg", f"Rust core hot paths — mean ns ({version_dir.name})", items, "ns")


def chart_python(version_dir: Path) -> None:
    data = json.loads((version_dir / "python-micro.json").read_text())
    items = [
        (bench["name"].replace("test_", ""), bench["stats"]["mean"] * 1e6)
        for bench in data["benchmarks"]
    ]
    bar_chart(OUT / "latest-python-micro.svg", f"Python adapter & codegen — mean µs ({version_dir.name})", items, "µs")


def chart_http(version_dir: Path) -> None:
    data = json.loads((version_dir / "http-load.json").read_text())
    rps: list[tuple[str, float]] = []
    p99: list[tuple[str, float]] = []
    for server, workloads in sorted(data.items()):
        for workload, raw in sorted(workloads.items()):
            label = f"{workload} / {server}"
            rps.append((label, float(raw["summary"]["requestsPerSec"])))
            p99.append((label, float(raw["latencyPercentiles"]["p99"]) * 1000.0))
    bar_chart(OUT / "latest-http-rps.svg", f"HTTP load — requests/sec ({version_dir.name})", rps, "req/s")
    bar_chart(OUT / "latest-http-p99.svg", f"HTTP load — p99 latency ({version_dir.name})", p99, "ms")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    version = latest_version()
    if version is None:
        print("plot: no results yet; nothing to chart")
        return 0
    version_dir = RESULTS / version
    for path in sorted(version_dir.glob("*.json")):
        json.loads(path.read_text())  # validate shape early
    chart_rust(version_dir)
    chart_python(version_dir)
    chart_http(version_dir)
    print(f"plot: wrote 4 SVG charts for {version} into {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())