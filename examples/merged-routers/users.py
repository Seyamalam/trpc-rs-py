"""users.py: the users sub-router for the merged-routers example.

`users_router()` returns a `Router` owning the `users.*` procedures; the app
nests it so `by_id` is served as `users.by_id`.
"""

from __future__ import annotations

from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode


class User(BaseModel):
    """A user returned by the users procedures."""

    id: int
    name: str


_USERS: dict[int, User] = {1: User(id=1, name="Ada"), 2: User(id=2, name="Grace")}


def users_router() -> Router:
    """Build the users router; the app nests it as `users.*`."""
    router = Router()

    @router.query
    def by_id(user_id: int) -> User:
        """Fetch a user; raises NOT_FOUND for unknown ids."""
        user = _USERS.get(user_id)
        if user is None:
            raise TRPCError(f"User {user_id} not found", code=TRPCErrorCode.NOT_FOUND)
        return user

    @router.mutation
    def rename(user_id: int, name: str) -> User:
        """Rename a user; raises NOT_FOUND for unknown ids."""
        user = _USERS.get(user_id)
        if user is None:
            raise TRPCError(f"User {user_id} not found", code=TRPCErrorCode.NOT_FOUND)
        user.name = name
        return user

    return router
