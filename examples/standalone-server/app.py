"""standalone-server: serve a tRPC router on the embedded Rust HTTP server.

`trpc_rs_py.serve.serve` mounts the router exactly like `mount()` and drives
it with the hyper-based server inside `trpc_rs_py._core` — no Python web
framework in the loop. Production mode is on here, so internal errors are
redacted and the playground is off.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/standalone-server && uv run --project ../.. python app.py
    # or pick a port: uv run --project ../.. python app.py 9000 (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`.
"""

from __future__ import annotations

import sys

from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode
from trpc_rs_py.serve import serve


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

    @router.query
    def boom() -> str:
        """Always fails; production mode redacts the internal message."""
        raise RuntimeError("internal-secret-details")

    return router


def main() -> int:
    """Serve the router on the standalone Rust server until Ctrl-C."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    return serve(build_router(), production=True, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
