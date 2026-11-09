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

from trpc_rs_py import mount, mount_rest

from router import build_fixture_router


def main() -> None:
    """Serve the fixture router with uvicorn and announce readiness."""
    import uvicorn

    router = build_fixture_router()
    app = FastAPI()
    app.mount("/trpc", mount(router))
    app.mount("/api", mount_rest(router, title="Fixture API", version="0.1.0"))
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    sys.exit(main())
