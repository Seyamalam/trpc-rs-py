"""Starlette fixture server for the official @trpc/client runtime tests.

Same contract as `fastapi_app.py`: reads `TRPC_TEST_PORT`, prints `READY`,
serves the fixture router under `/trpc` from a plain Starlette app.
"""

from __future__ import annotations

import os
import sys

from starlette.applications import Starlette
from starlette.routing import Mount

from trpc_rs_py import mount

from router import build_fixture_router


def main() -> None:
    """Serve the fixture router with uvicorn and announce readiness."""
    import uvicorn

    app = Starlette(routes=[Mount("/trpc", app=mount(build_fixture_router()))])
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    sys.exit(main())
