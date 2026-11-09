"""Regenerate `src/app-router.d.ts` from `router_source.build_router`.

Run from the repository root (or this directory) with the project's
environment, which has the compiled `trpc_rs_py` extension installed:

    uv run python clients/ts-tests/generate-fixture.py

The output is committed. `tests/test_codegen.py` fails if the committed
fixture no longer matches `generate_types(build_router())` byte for byte,
so the fixture cannot drift from the generator.

Example:
    ```bash
    uv run python clients/ts-tests/generate-fixture.py
    ```
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from router_source import build_router  # noqa: E402

from trpc_rs_py import generate_types  # noqa: E402

_TARGET = _HERE / "src" / "app-router.d.ts"


def main() -> None:
    """Write the regenerated declaration file."""
    _TARGET.write_text(generate_types(build_router()))
    print(f"wrote {_TARGET}")


if __name__ == "__main__":
    main()
