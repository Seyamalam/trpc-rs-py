"""flask-basic: a minimal Flask app serving a tRPC router.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/flask-basic && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`; REST + OpenAPI live under `/api`.
"""

from __future__ import annotations

import os

from flask import Flask
from pydantic import BaseModel
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from trpc_rs_py import (
    RestRoute,
    Router,
    TRPCError,
    TRPCErrorCode,
    mount_rest,
    mount_wsgi,
    wsgi_from_asgi,
)


class CreateUserInput(BaseModel):
    """Input for the `create_user` mutation."""

    name: str
    age: int


class User(BaseModel):
    """Output of both user procedures."""

    id: int
    name: str
    age: int


_USERS: dict[int, User] = {}
_NEXT_ID = 1


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query(rest=RestRoute("GET", "/greeting/{name}"))
    def greeting(name: str) -> str:
        """A query reachable via tRPC and REST at /api/greeting/<name>."""
        return f"Hello, {name}!"

    @router.query
    def user_by_id(user_id: int) -> User:
        """Fetch a user; raises NOT_FOUND for unknown ids."""
        user = _USERS.get(user_id)
        if user is None:
            raise TRPCError(f"User {user_id} not found", code=TRPCErrorCode.NOT_FOUND)
        return user

    @router.mutation
    def create_user(user: CreateUserInput) -> User:
        """Create a user; Pydantic validates and coerces the input."""
        global _NEXT_ID
        user = User(id=_NEXT_ID, name=user.name, age=user.age)
        _NEXT_ID += 1
        _USERS[user.id] = user
        return user

    return router


def build_app() -> Flask:
    """Build the Flask app with tRPC under /trpc and REST under /api."""
    router = build_router()
    app = Flask(__name__)
    # One dispatcher mounts both the tRPC WSGI app and the REST/OpenAPI ASGI
    # app (wrapped by wsgi_from_asgi). For a tRPC-only mount, `attach_flask`
    # is a one-call shortcut.
    app.wsgi_app = DispatcherMiddleware(
        app.wsgi_app,
        {
            "/trpc": mount_wsgi(router),
            "/api": wsgi_from_asgi(mount_rest(router, title="flask-basic", version="0.1.0")),
        },
    )
    return app


app = build_app()

if __name__ == "__main__":
    from werkzeug.serving import run_simple

    run_simple("127.0.0.1", int(os.environ.get("PORT", "8000")), app, threaded=True)
