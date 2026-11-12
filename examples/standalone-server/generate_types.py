"""Regenerate this example's `app-router.d.ts` from `app.build_router`.

Run from the repository root:

    uv run python examples/standalone-server/generate_types.py

The output is committed; `examples/standalone-server/smoke.sh` regenerates it
and fails on drift, so the committed types always match the example app.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from app import build_router  # noqa: E402

from trpc_rs_py import generate_types  # noqa: E402

_TARGET = _HERE / "app-router.d.ts"


def main() -> None:
    """Write the regenerated declaration file."""
    _TARGET.write_text(generate_types(build_router()))
    print(f"wrote {_TARGET}")


if __name__ == "__main__":
    main()
