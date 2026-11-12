"""flask-nextjs: a Flask backend called by a minimal Next.js frontend.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/flask-nextjs && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then start the frontend (`pnpm --dir frontend dev`, see README.md) — the
Next.js app on http://localhost:3000 renders a query result fetched
server-side by the official `@trpc/client`, so no CORS is involved.
"""

from __future__ import annotations

import os

from flask import Flask
from pydantic import BaseModel
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount_wsgi


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

    @router.query
    def greeting(name: str) -> str:
        """A simple typed query rendered by the Next.js frontend."""
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
    """Build the Flask app with the tRPC WSGI app mounted at /trpc."""
    app = Flask(__name__)
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/trpc": mount_wsgi(build_router())})
    return app


app = build_app()

if __name__ == "__main__":
    from werkzeug.serving import run_simple

    run_simple("127.0.0.1", int(os.environ.get("PORT", "8000")), app, threaded=True)
