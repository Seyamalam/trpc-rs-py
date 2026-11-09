"""FastAPI fixture server for the official @trpc/client runtime tests.

Started as a child process by `src/runtime.test.ts`:

    uv run --project <repo root> python fixtures/fastapi_app.py

Reads the port from the `TRPC_TEST_PORT` environment variable (chosen by the
test runner), prints `READY` on stdout once startup is initiated, and serves
the fixture router under `/trpc`.
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI

from trpc_rs_py import mount

from router import build_fixture_router


def main() -> None:
    """Serve the fixture router with uvicorn and announce readiness."""
    import uvicorn

    app = FastAPI()
    app.mount("/trpc", mount(build_fixture_router()))
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    sys.exit(main())
