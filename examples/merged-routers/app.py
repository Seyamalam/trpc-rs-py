"""merged-routers: one app composed from several sub-routers.

Sub-routers are plain `Router` objects defined in their own modules
(`posts.py`, `users.py`). The app router `nest`s each under a dotted prefix
(`posts.by_id`, `users.rename`, `posts.comments.for_post` — nesting
composes two levels deep) and `merge`s a meta router in at the root, exactly
like tRPC's `appRouter = router({...}).merge(...)`.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/merged-routers && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from posts import posts_router
from users import users_router

from trpc_rs_py import Router, mount


def meta_router() -> Router:
    """Root-level procedures merged in beside the nested domains."""
    router = Router()

    @router.query
    def health() -> str:
        """Liveness probe; served as `health` (merge keeps paths unprefixed)."""
        return "ok"

    return router


def build_router() -> Router:
    """Compose the app router: merge root procedures, nest the domains."""
    router = Router()
    router.merge(meta_router())
    router.nest("posts", posts_router())
    router.nest("users", users_router())
    return router


def build_app() -> FastAPI:
    """Build the FastAPI app with the merged router mounted at /trpc."""
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
