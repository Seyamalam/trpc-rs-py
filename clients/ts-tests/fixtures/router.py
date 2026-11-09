"""Test-only tRPC router shared by the runtime compatibility fixture servers.

Procedures are deliberately tiny and cover every runtime case the official
`@trpc/client` tests exercise: query, mutation, Pydantic-validated input,
deliberate `TRPCError`, an unexpected exception, and a tracked subscription.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from trpc_rs_py import RestRoute, Router, Tracked, TRPCError, TRPCErrorCode, tracked


class GreetingInput(BaseModel):
    name: str
    excited: bool = False


class SignupForm(BaseModel):
    """Constrained input for the zod-codegen parity fixture."""

    username: Annotated[
        str, StringConstraints(min_length=3, max_length=16, pattern=r"^[a-z][a-z0-9_]*$")
    ]
    age: Annotated[int, Field(ge=13, le=120)]
    nickname: str | None = None
    tags: Annotated[list[str], Field(max_length=3)] = []


class GreetingOutput(BaseModel):
    greeting: str


class CreatedPost(BaseModel):
    title: str
    created: bool


class Tick(BaseModel):
    tick: int


class PostRef(BaseModel):
    """Typed payload of `PostNotFound`: which post id was missing."""

    post_id: int


class PostNotFound(TRPCError):
    """Declared typed error raised by `post_by_id_or_fail`."""

    code = TRPCErrorCode.NOT_FOUND
    data_model = PostRef


def build_fixture_router() -> Router:
    """Build the router served by both fixture servers."""
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query(rest=RestRoute("GET", "/echo/{message}"))
    def echo(message: str) -> str:
        return f"echo: {message}"

    @router.query
    def greet(params: GreetingInput) -> GreetingOutput:
        suffix = "!" if params.excited else "."
        return GreetingOutput(greeting=f"Hello {params.name}{suffix}")

    @router.query
    def boom() -> str:
        raise TRPCError("deliberate not found", code=TRPCErrorCode.NOT_FOUND)

    @router.query
    def explode() -> str:
        raise RuntimeError("unexpected fixture failure")

    @router.query(errors=[PostNotFound])
    def post_by_id_or_fail(post_id: int) -> str:
        raise PostNotFound("no such post", data=PostRef(post_id=post_id))

    @router.mutation(rest=RestRoute("POST", "/posts"))
    def create_post(title: str) -> CreatedPost:
        return CreatedPost(title=title, created=True)

    @router.mutation
    def signup(form: SignupForm) -> str:
        return f"signed up {form.username}"

    @router.mutation
    def no_input() -> str:
        return "mutated"

    @router.subscription
    async def ticks(lastEventId: str | None = None) -> AsyncIterator[Tracked[Tick]]:
        start = int(lastEventId) + 1 if lastEventId else 0
        for index in range(start, start + 3):
            yield tracked(str(index), Tick(tick=index))

    return router
