"""feature-batching: several procedures answered by one `?batch=1` request.

tRPC v11 request batching lets a client send multiple calls of the same kind
in a single HTTP request — queries batch over one GET, mutations over one
POST — and get back an ordered list of per-call envelopes, where a failing
call produces its own error entry without affecting the others.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/feature-batching && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then run the client demo in another shell:

    cd examples/feature-batching && uv run --project ../.. python client.py
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @router.query
    def multiply(a: int, b: int) -> int:
        """Multiply two integers."""
        return a * b

    @router.query
    def divide(a: float, b: float) -> float:
        """Divide `a` by `b`; rejects a zero divisor with BAD_REQUEST."""
        if b == 0:
            raise TRPCError("Division by zero", code=TRPCErrorCode.BAD_REQUEST)
        return a / b

    return router


def build_app() -> FastAPI:
    """Build the example FastAPI app with the tRPC router mounted at /trpc."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router()))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
