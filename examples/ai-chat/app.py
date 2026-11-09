"""AI chat example: streamed LLM-style tokens over a tRPC subscription.

`chat` yields one `tracked` event per token — the official `@trpc/client`
receives `{id, data}` items over SSE and can resume from `lastEventId` after
a reconnect. Disconnecting the client cancels the generator, so no "LLM
tokens" are produced after the caller leaves. A companion `models` query
shows the same router serving REST + OpenAPI at /api.

Run:  uvicorn app:app --reload
Demo: curl -N "http://localhost:8000/trpc/chat?input=%7B%22prompt%22%3A%22hi%22%7D"
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import RestRoute, Router, Tracked, mount, mount_rest, tracked


class ChatChunk(BaseModel):
    """One streamed token; `done` marks the final chunk."""

    token: str
    done: bool = False


class ModelInfo(BaseModel):
    """A (fake) selectable model."""

    id: str
    kind: str


router = Router()

_REPLY = "This is a simulated streaming reply from the Python backend."


@router.query(rest=RestRoute("GET", "/models"))
def models() -> list[ModelInfo]:
    """List available (fake) models — also a REST GET /api/models."""
    return [ModelInfo(id="sim-1", kind="chat")]


@router.subscription
async def chat(prompt: str) -> AsyncIterator[Tracked[ChatChunk]]:
    """Stream reply tokens for `prompt`, one tracked SSE event per token."""
    tokens = f"You said: {prompt} {_REPLY}".split(" ")
    for index, word in enumerate(tokens):
        yield tracked(str(index), ChatChunk(token=word + " "))
        await asyncio.sleep(0.05)  # simulate token latency
    yield tracked(str(len(tokens)), ChatChunk(token="", done=True))


def build_router() -> Router:
    """Return the example router (used by generate_types.py and smoke.sh)."""
    return router


app = FastAPI(title="ai-chat")
app.mount("/trpc", mount(router))
app.mount("/api", mount_rest(router, title="ai-chat", version="0.1.0"))
