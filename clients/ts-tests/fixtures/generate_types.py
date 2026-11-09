"""Regenerate `fixtures/app-router.d.ts` from `fixtures/router.build_fixture_router`.

Run from the repository root with the project's environment (the compiled
`trpc_rs_py` extension must be installed):

    uv run python clients/ts-tests/fixtures/generate_types.py

The output is committed. `scripts/ts-tests.sh` regenerates it and fails if the
committed file drifts, so the runtime tests always type against declarations
that match the served router exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from router import build_fixture_router  # noqa: E402

from trpc_rs_py import generate_types  # noqa: E402

_TARGET = _HERE / "app-router.d.ts"


def main() -> None:
    """Write the regenerated declaration file."""
    _TARGET.write_text(generate_types(build_fixture_router()))
    print(f"wrote {_TARGET}")


if __name__ == "__main__":
    main()
