"""Regenerate `fixtures/zod-schemas.ts` from `router.build_fixture_router`.

Run from the repository root (or this directory) with the project's
environment, which has the compiled `trpc_rs_py` extension installed:

    uv run python clients/ts-tests/fixtures/generate_zod_schemas.py

The output is committed and drift-checked by `scripts/ts-tests.sh`.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from router import build_fixture_router  # noqa: E402

from trpc_rs_py import generate_zod  # noqa: E402

_TARGET = _HERE / "zod-schemas.ts"


def main() -> None:
    """Write the regenerated zod schema module."""
    _TARGET.write_text(generate_zod(build_fixture_router()))
    print(f"wrote {_TARGET}")


if __name__ == "__main__":
    main()
