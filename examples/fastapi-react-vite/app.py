"""fastapi-react-vite: a FastAPI backend called by a Vite + React frontend.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/fastapi-react-vite && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then start the frontend (`frontend/README` commands are in README.md) — the
Vite dev server on http://localhost:5173 calls `http://127.0.0.1:8000/trpc`
through the official `@trpc/react-query` client. The mounted tRPC app is
wrapped with `cors()` so that origin is allowed.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount
from trpc_rs_py.cors import cors

# Origins the Vite dev server serves the frontend from.
_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


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
        """A simple typed query rendered by the React frontend."""
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


def build_app() -> FastAPI:
    """Build the FastAPI app with the tRPC router at /trpc, CORS-enabled."""
    app = FastAPI()
    app.mount("/trpc", cors(mount(build_router()), allow_origins=_FRONTEND_ORIGINS))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
