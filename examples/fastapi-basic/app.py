"""fastapi-basic: a minimal FastAPI app serving a tRPC router.

Run it (from the repository root, after `uv run maturin develop --release`):

    uv run --project . uvicorn app:app --port 8000   # from examples/fastapi-basic
    # or: uv run python examples/fastapi-basic/app.py

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount


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
    """Build the example FastAPI app with the tRPC router mounted at /trpc."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router()))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
