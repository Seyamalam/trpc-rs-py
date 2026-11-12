"""fastapi_app.py: serve the shared router through FastAPI (`mount()`).

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/shared-router && uv run --project ../.. python fastapi_app.py
    # PORT=9000 to override the listen port (default 8001)

The tRPC endpoints live at `http://127.0.0.1:8001/trpc`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from shared_router import build_router

from trpc_rs_py import mount


def build_app() -> FastAPI:
    """Build the FastAPI app with the shared router mounted at /trpc."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router()))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8001")),
        log_level="error",
    )
