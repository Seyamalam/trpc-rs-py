"""shared_router.py: one router module consumed by two different web apps.

Both `fastapi_app.py` (`mount()`, ASGI) and `flask_app.py` (`mount_wsgi()`,
WSGI) import `build_router` from here — the router is framework-agnostic, so
the same procedures, validation and error shapes serve through either
adapter.
"""

from __future__ import annotations

from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode


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
    """Build the shared tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query
    def greeting(name: str) -> str:
        """A trivial query both adapters serve identically."""
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
