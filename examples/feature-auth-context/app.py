"""feature-auth-context: bearer-token auth via `context_factory` + `require_auth`.

One public procedure (`greeting`) and one protected procedure (`me`) on a
single router. `context_factory` runs once per HTTP request, reads the
`Authorization: Bearer <token>` header, and stores the resolved user on
`RequestContext.user`. `router.use(require_auth(...))` is registered after
`greeting`, so the auth gate is snapshotted onto `me` only — the same
public-procedure/protected-procedure split tRPC apps write with middleware.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/feature-auth-context && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import RequestContext, Router, mount
from trpc_rs_py.middleware import require_auth


class User(BaseModel):
    """An authenticated user resolved from a bearer token."""

    id: int
    name: str


# Demo token table; a real app verifies a JWT or queries a session store.
_TOKENS: dict[str, User] = {
    "token-ada": User(id=1, name="Ada"),
    "token-grace": User(id=2, name="Grace"),
}


def context_factory(request: RequestContext) -> RequestContext:
    """Resolve `Authorization: Bearer <token>` into `request.user`.

    Called once per HTTP request by `mount(...)`; whatever it returns is the
    `ctx` object middleware and handlers see. A missing or unknown token
    leaves `user` as `None`, which `require_auth` turns into UNAUTHORIZED.
    """
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
    request.user = _TOKENS.get(token)
    return request


def build_router() -> Router:
    """Build the example router: `greeting` is public, `me` is auth-gated."""
    router = Router()

    @router.query
    def greeting() -> str:
        """Public procedure — reachable without credentials."""
        return "Hello, world!"

    # Everything registered AFTER `use` carries the auth gate; `greeting`
    # keeps the empty middleware chain it was snapshotted with.
    router.use(require_auth(lambda ctx: getattr(ctx, "user", None) is not None))

    @router.query
    def me(ctx: RequestContext) -> User:
        """Protected procedure — `require_auth` ran first, so `ctx.user` is set."""
        assert isinstance(ctx.user, User)
        return ctx.user

    return router


def build_app() -> FastAPI:
    """Build the FastAPI app, wiring the context factory into the mount."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router(), context_factory=context_factory))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
