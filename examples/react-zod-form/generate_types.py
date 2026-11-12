"""Regenerate this example's `frontend/app-router.d.ts` and `frontend/app-schemas.ts`.

Run from the repository root:

    uv run python examples/react-zod-form/generate_types.py

Both outputs are committed inside `frontend/` (where the Vite project's
`node_modules` lets the files' `zod` / `@trpc/server` imports resolve);
`examples/react-zod-form/smoke.sh` regenerates them and fails on drift, so
the committed types and Zod schemas always match the example app. The
frontend uses the `.d.ts` for the tRPC client types and `app-schemas.ts` to
validate the form before submitting.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from app import build_router  # noqa: E402

from trpc_rs_py import generate_types, generate_zod  # noqa: E402

_TYPES_TARGET = _HERE / "frontend" / "app-router.d.ts"
_SCHEMAS_TARGET = _HERE / "frontend" / "app-schemas.ts"


def main() -> None:
    """Write the regenerated declaration and Zod-schema files."""
    _TYPES_TARGET.write_text(generate_types(build_router()))
    print(f"wrote {_TYPES_TARGET}")
    _SCHEMAS_TARGET.write_text(generate_zod(build_router()))
    print(f"wrote {_SCHEMAS_TARGET}")


if __name__ == "__main__":
    main()
