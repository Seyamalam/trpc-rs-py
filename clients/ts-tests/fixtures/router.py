"""Test-only tRPC router shared by the runtime compatibility fixture servers.

Procedures are deliberately tiny and cover every runtime case the official
`@trpc/client` tests exercise: query, mutation, Pydantic-validated input,
deliberate `TRPCError`, an unexpected exception, and a tracked subscription.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, Tracked, tracked


class GreetingInput(BaseModel):
    name: str
    excited: bool = False


class GreetingOutput(BaseModel):
    greeting: str


class CreatedPost(BaseModel):
    title: str
    created: bool


class Tick(BaseModel):
    tick: int


def build_fixture_router() -> Router:
    """Build the router served by both fixture servers."""
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query
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

    @router.mutation
    def create_post(title: str) -> CreatedPost:
        return CreatedPost(title=title, created=True)

    @router.mutation
    def no_input() -> str:
        return "mutated"

    @router.subscription
    async def ticks(lastEventId: str | None = None) -> AsyncIterator[Tracked[Tick]]:
        start = int(lastEventId) + 1 if lastEventId else 0
        for index in range(start, start + 3):
            yield tracked(str(index), Tick(tick=index))

    return router
