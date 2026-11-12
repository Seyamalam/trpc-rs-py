"""Regenerate this example's `generated_client.py` from `app.build_router`.

Run from the repository root:

    uv run python examples/python-client/generate_client.py

The output is committed verbatim — do not edit it by hand;
`examples/python-client/smoke.sh` regenerates it and fails on drift, so the
committed stub always matches the example router.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from app import build_router  # noqa: E402

from trpc_rs_py.pyclient import generate_python_client  # noqa: E402

_TARGET = _HERE / "generated_client.py"


def main() -> None:
    """Write the regenerated client stub module."""
    _TARGET.write_text(generate_python_client(build_router()))
    print(f"wrote {_TARGET}")


if __name__ == "__main__":
    main()
