"""Streaming helpers for subscription procedures.

A subscription handler (`@router.subscription`) is an `async def` generator —
each yielded value becomes one SSE data event. Yields a `Tracked` value via
`tracked(id, data)` to mark an event with an id, enabling client resume with
`lastEventId` exactly like the official `tracked()` in `@trpc/server`.

Example:
    ```python
    import asyncio
    from collections.abc import AsyncIterator

    from trpc_rs_py import Router, tracked

    router = Router()


    @router.subscription
    async def ticks() -> AsyncIterator[object]:
        for index in range(3):
            yield tracked(str(index), {"tick": index})
    ```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

__all__ = ["Tracked", "tracked"]

_T = TypeVar("_T")


@dataclass(frozen=True)
class Tracked(Generic[_T]):
    r"""A yielded subscription value carrying an SSE event id.

    Mirrors `tracked()` in `@trpc/server`: the id is emitted as the SSE `id`
    field and returned to reconnecting clients as their `lastEventId`.

    Attributes:
        id: The event id. Must not contain `\n`, `\r`, or NUL — the SSE
            encoder rejects those to prevent frame injection.
        data: The JSON-ready event payload.

    Example:
        ```python
        from trpc_rs_py import tracked

        event = tracked("msg-4", {"text": "hi"})
        assert (event.id, event.data) == ("msg-4", {"text": "hi"})
        ```
    """

    id: str
    data: _T


def tracked(event_id: str, data: _T) -> Tracked[_T]:
    r"""Mark a yielded subscription value with an SSE event id.

    Args:
        event_id: The event id sent as the SSE `id` field; becomes the
            client's `lastEventId` for resume. Must not contain `\n`, `\r`,
            or NUL characters.
        data: The JSON-ready event payload.

    Returns:
        A `Tracked` wrapper the adapter unwraps into an `id:` SSE field.

    Raises:
        ValueError: If `event_id` contains `\n`, `\r`, or NUL.

    Example:
        ```python
        from trpc_rs_py import tracked

        assert tracked("1", "a").id == "1"
        ```
    """
    if any(character in event_id for character in "\n\r\0"):
        msg = "tracked event id must not contain newline, carriage return, or NUL"
        raise ValueError(msg)
    return Tracked(event_id, data)
