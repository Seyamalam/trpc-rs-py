"""feature-error-handling: the production error policy, side by side with dev.

The same router is mounted twice on one app so both modes are visible at
once — a real deployment picks one `production` flag from config:

- `/trpc`     — `production=True`: `TRPCError`s with a client-facing code
  (NOT_FOUND, ...) pass their message through; `TRPCError`s with code
  INTERNAL_SERVER_ERROR and any unexpected exception are answered with the
  fixed message "Internal server error" so internals never leak.
- `/trpc-dev` — `production=False` (default): every message passes through,
  including the `repr` of unexpected exceptions.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/feature-error-handling && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc` (production) or `http://127.0.0.1:8000/trpc-dev`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount

# Marker strings a production response must never contain.
_SECRET = "postgres://admin:hunter2@10.0.0.5/prod"


class Account(BaseModel):
    """An account returned by the `lookup` query."""

    id: int
    name: str


_ACCOUNTS: dict[int, Account] = {1: Account(id=1, name="Ada")}


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query
    def health() -> str:
        """Liveness probe."""
        return "ok"

    @router.query
    def lookup(account_id: int) -> Account:
        """Raise a deliberate client-facing error for unknown ids.

        NOT_FOUND is an API contract error, so its message reaches the
        client verbatim in BOTH modes.
        """
        account = _ACCOUNTS.get(account_id)
        if account is None:
            raise TRPCError(f"Account {account_id} does not exist", code=TRPCErrorCode.NOT_FOUND)
        return account

    @router.query
    def crash() -> str:
        """Raise an unexpected exception carrying internal details."""
        raise ValueError(f"could not connect to {_SECRET}")

    @router.query
    def internal() -> str:
        """Raise a `TRPCError` that still codes INTERNAL_SERVER_ERROR.

        Choosing the code does not mark the error client-safe: INTERNAL
        messages are redacted in production too — only non-INTERNAL
        `TRPCError` messages pass through.
        """
        raise TRPCError(
            f"database credentials in use: {_SECRET}",
            code=TRPCErrorCode.INTERNAL_SERVER_ERROR,
        )

    return router


def build_app() -> FastAPI:
    """Build the FastAPI app with the router mounted in both modes."""
    app = FastAPI()
    router = build_router()
    app.mount("/trpc", mount(router, production=True))
    app.mount("/trpc-dev", mount(router, path="/trpc-dev"))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
