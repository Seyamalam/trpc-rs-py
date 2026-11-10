"""Streaming helpers for subscription procedures and Vercel AI SDK endpoints.

A subscription handler (`@router.subscription`) is an `async def` generator —
each yielded value becomes one SSE data event. Yields a `Tracked` value via
`tracked(id, data)` to mark an event with an id, enabling client resume with
`lastEventId` exactly like the official `tracked()` in `@trpc/server`.

The `ai_*` helpers shape events for the Vercel AI SDK v5 UI message stream
protocol (`useChat`): `ai_text_stream` turns a token source into
`text-start`/`text-delta`/`text-end` chunks, `ai_message_stream` adds the
`start`/`finish`/`error` message envelope, `ai_data_part` builds `data-*`
chunks, and `ai_sdk_endpoint` serves the whole stream as a plain ASGI app
speaking `text/event-stream` with `x-vercel-ai-ui-message-stream: v1`. The
helpers only shape *events* — cancellation, backpressure, and `tracked`
semantics belong to the transport and are untouched.

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

import asyncio
import contextlib
import inspect
import itertools
import json
import re
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeGuard, TypeVar, cast

if TYPE_CHECKING:
    from .asgi import ASGIApp, Receive, Scope, Send

__all__ = [
    "AiChunkSource",
    "AiStreamFactory",
    "Tracked",
    "ai_data_part",
    "ai_message_stream",
    "ai_sdk_endpoint",
    "ai_text_stream",
    "tracked",
]

_T = TypeVar("_T")

# Deterministic, monotonically increasing ids for generated message/part ids.
# Process-unique only; callers needing stability across restarts pass explicit
# ids (message_id=, text_id=, part_id=).
_ID_COUNTER = itertools.count()

# Response headers of the AI SDK v5 UI message stream, mirroring
# UI_MESSAGE_STREAM_HEADERS in the `ai` package.
_AI_SDK_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-type", b"text/event-stream"),
    (b"cache-control", b"no-cache"),
    (b"connection", b"keep-alive"),
    (b"x-vercel-ai-ui-message-stream", b"v1"),
    (b"x-accel-buffering", b"no"),
)

_STREAM_OPEN_COMMENT = b": stream-open\n\n"
_KEEP_ALIVE_COMMENT = b": keep-alive\n\n"
_DONE_FRAME = b"data: [DONE]\n\n"

_DATA_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

DEFAULT_MAX_BODY_SIZE = 1024 * 1024

AiChunkSource: TypeAlias = AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]]
"""Sync or async iterable of UI message stream chunk dicts (`{"type": ...}`)."""

AiStreamFactory: TypeAlias = Callable[[Any, "Scope"], AiChunkSource | Awaitable[AiChunkSource]]
"""Factory producing the chunk iterable for one `ai_sdk_endpoint` request.

Called per HTTP request with the decoded JSON body (`None` for GET or an
empty body) and the raw ASGI scope — the AI SDK `DefaultChatTransport`
POSTs `{"messages": [...], ...}` and header-based auth reads the scope.
The returned value may be a sync or async iterable of chunk mappings, or an
awaitable resolving to one.
"""


class _BodyTooLarge(Exception):
    """Internal sentinel: the streamed request body exceeded `max_body_size`."""


class _ClientDisconnected(Exception):
    """Internal sentinel: the client disconnected mid-request."""


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


def _next_id(prefix: str) -> str:
    """Return the next generated id, e.g. `text-12`, monotonic per process."""
    return f"{prefix}-{next(_ID_COUNTER)}"


def _as_async_iterator(source: object) -> AsyncIterator[Any]:
    """Normalize a sync or async iterable into an async iterator.

    Args:
        source: The iterable to normalize. Public signatures declare
            `AsyncIterable | Iterable`; this accepts `object` so mistyped
            callers fail cleanly instead of falling through unchecked.

    Returns:
        `source.__aiter__()` for async iterables, or a generator draining a
        sync iterable lazily.

    Raises:
        TypeError: If `source` is not iterable, or is a `str`/`bytes`/`Mapping`
            (a single value, not a stream of chunks).
    """
    if isinstance(source, AsyncIterable):
        return cast("AsyncIterable[object]", source).__aiter__()
    if isinstance(source, (str, bytes)) or _is_mapping(source):
        msg = f"expected an iterable of chunks, got {type(source).__name__}"
        raise TypeError(msg)
    if isinstance(source, Iterable):
        return _sync_to_async(cast("Iterable[object]", source))
    msg = f"expected an iterable of chunks, got {type(source).__name__}"
    raise TypeError(msg)


async def _sync_to_async(iterable: Iterable[_T]) -> AsyncIterator[_T]:
    """Drain a sync iterable lazily inside an async generator."""
    for item in iterable:
        yield item


def _is_mapping(value: object) -> TypeGuard[Mapping[str, Any]]:
    """Narrow `value` to a fully-known `Mapping[str, Any]` for strict typing."""
    return isinstance(value, Mapping)


def _attribute_or_key(value: Any, name: str) -> Any:
    """Read `name` from an object attribute or a mapping key."""
    if _is_mapping(value):
        return value.get(name)
    return getattr(value, name, None)


def _delta_text(item: object) -> str | None:
    """Extract one text delta from a source item, or `None` to skip it.

    Supported shapes, checked in this order:

    - `str` — the delta itself.
    - OpenAI `ChatCompletionChunk`-shaped — `.choices[0].delta.content`
      (also dict-shaped `{"choices": [{"delta": {"content": ...}}]}`).
    - Anthropic `content_block_delta`-shaped — `.delta.text` (also
      dict-shaped `{"delta": {"text": ...}}`), or `.delta.content` for
      OpenAI-style objects without a `choices` envelope.

    Anything else (usage summaries, role chunks, ping events, unknown
    objects) returns `None` and is skipped by `ai_text_stream`.
    """
    if isinstance(item, str):
        return item
    choices = _attribute_or_key(item, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        delta = _attribute_or_key(choices[0], "delta")
        content = _attribute_or_key(delta, "content")
        return content if isinstance(content, str) else None
    delta = _attribute_or_key(item, "delta")
    if delta is not None:
        text = _attribute_or_key(delta, "text")
        if isinstance(text, str):
            return text
        content = _attribute_or_key(delta, "content")
        if isinstance(content, str):
            return content
    return None


async def ai_text_stream(
    source: AsyncIterable[object] | Iterable[object],
    *,
    text_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield `text-start`/`text-delta`/`text-end` chunks for one text part.

    Each item of `source` is mapped through `_delta_text`: a `str` is the
    delta itself; OpenAI-shaped chunks contribute `choices[0].delta.content`;
    Anthropic-shaped `content_block_delta` events contribute `delta.text`;
    anything else (usage, role, ping chunks) is skipped, as are empty deltas.

    For several text blocks in one message, concatenate multiple
    `ai_text_stream` calls — each call owns one block id, which is all the
    per-id sequencing the protocol requires.

    Args:
        source: A sync or async iterable of text chunks.
        text_id: The block `id` carried by every emitted chunk. Defaults to a
            generated `text-N` id from a process-wide counter (monotonic,
            deterministic within a run; pass explicit ids for stability
            across restarts).

    Yields:
        `{"type": "text-start", "id": ...}`, one
        `{"type": "text-delta", "id": ..., "delta": ...}` per non-empty
        delta, then `{"type": "text-end", "id": ...}`.

    Example:
        ```python
        from trpc_rs_py.streaming import ai_text_stream


        async def collect() -> list[dict]:
            return [c async for c in ai_text_stream(iter_of_words())]
        ```
    """
    block_id = text_id if text_id is not None else _next_id("text")
    yield {"type": "text-start", "id": block_id}
    async for item in _as_async_iterator(source):
        delta = _delta_text(item)
        if delta:
            yield {"type": "text-delta", "id": block_id, "delta": delta}
    yield {"type": "text-end", "id": block_id}


def ai_data_part(
    name: str,
    data: Any,
    *,
    part_id: str | None = None,
    transient: bool = False,
) -> dict[str, Any]:
    """Build a `data-*` custom part chunk for the UI message stream.

    Custom parts carry arbitrary structured data to the frontend; the client
    handles them by the `data-<name>` type. An `id` marks the part as
    reconcilable (later chunks with the same id update it); `transient`
    marks the part as ephemeral — delivered to `onData` but never appended
    to the message parts.

    Args:
        name: The part name appended to `data-` — letters, digits, `-` and
            `_` only (the AI SDK convention is kebab-case, e.g. `weather`).
        data: The JSON-ready payload.
        part_id: Optional part id emitted as `id`. `None` (the default)
            omits the field; ids are not auto-generated because the AI SDK
            treats `id`-carrying data parts as updatable state.
        transient: Emit `"transient": true` so the part is delivered to
            `onData` handlers without joining the message.

    Returns:
        The chunk dict, ready to yield from `ai_message_stream`'s parts
        source or an `ai_sdk_endpoint` factory.

    Raises:
        ValueError: If `name` is empty or contains other characters.

    Example:
        ```python
        from trpc_rs_py.streaming import ai_data_part

        part = ai_data_part("weather", {"location": "SF", "temperature": 21})
        assert part["type"] == "data-weather"
        ```
    """
    if not _DATA_NAME_PATTERN.fullmatch(name):
        msg = f"data part name {name!r} must be non-empty and use only A-Z, a-z, 0-9, - and _"
        raise ValueError(msg)
    part: dict[str, Any] = {"type": f"data-{name}", "data": data}
    if part_id is not None:
        part["id"] = part_id
    if transient:
        part["transient"] = True
    return part


async def ai_message_stream(
    parts: AsyncIterable[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    message_id: str | None = None,
    error_text: Callable[[BaseException], str] = str,
) -> AsyncIterator[dict[str, Any]]:
    """Wrap a chunk iterable in the UI message stream envelope.

    Emits `{"type": "start", "messageId": ...}` first, passes every part
    through unchanged, then `{"type": "finish"}` on a clean end. If `parts`
    raises mid-stream, an `{"type": "error", "errorText": ...}` chunk is
    emitted instead of `finish` — the AI SDK protocol ends a failed message
    with the error part. Cancellation (`asyncio.CancelledError`,
    `GeneratorExit`) re-raises so disconnect semantics stay intact.

    Args:
        parts: A sync or async iterable of chunk mappings — typically
            `ai_text_stream(...)`, `ai_data_part(...)` results, or
            hand-written chunk dicts (`reasoning-*`, `tool-*`, ...).
        message_id: The `messageId` on the `start` chunk. Defaults to a
            generated `msg-N` id; the AI SDK marks it optional but `useChat`
            keys the assistant message by it, so one is always emitted.
        error_text: Maps an exception to the `errorText` field of the
            `error` chunk; defaults to `str`. Pass
            `lambda error: "internal error"` to avoid leaking internals.

    Yields:
        The `start` chunk, each part as a plain dict, then `finish` — or
        `error` on failure.

    Example:
        ```python
        from trpc_rs_py.streaming import (
            ai_data_part,
            ai_message_stream,
            ai_text_stream,
        )


        async def reply() -> AsyncIterator[dict]:
            async def parts() -> AsyncIterator[dict]:
                async for chunk in ai_text_stream(llm_tokens()):
                    yield chunk
                yield ai_data_part("usage", {"tokens": 42})

            async for chunk in ai_message_stream(parts(), message_id="m-1"):
                yield chunk
        ```
    """
    yield {"type": "start", "messageId": message_id or _next_id("msg")}
    try:
        async for part in _as_async_iterator(parts):
            yield dict(part)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception as error:
        yield {"type": "error", "errorText": error_text(error)}
        return
    yield {"type": "finish"}


def _encode_chunk(chunk: Mapping[str, Any]) -> bytes:
    r"""Encode one chunk as an SSE `data:` frame.

    Matches `JSON.stringify` output: compact separators, non-ASCII kept as
    UTF-8 (`ensure_ascii=False`). JSON string escaping guarantees no raw
    newlines, so the frame is always exactly one `data:` line.

    Args:
        chunk: The chunk mapping to serialize.

    Returns:
        The `data: {json}\\n\\n` frame bytes.
    """
    payload = json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"data: " + payload + b"\n\n"


def _error_chunk(error_text: str) -> dict[str, Any]:
    """Build the `{"type": "error", "errorText": ...}` chunk."""
    return {"type": "error", "errorText": error_text}


async def _send_frame(send: Send, payload: bytes) -> bool:
    """Send one SSE chunk, reporting whether the socket accepted it.

    Empty `payload` ends the response body. Returns `False` when the
    transport rejected the send — treated as client disconnect.
    """
    try:
        await send({"type": "http.response.body", "body": payload, "more_body": bool(payload)})
    except (ConnectionError, OSError, RuntimeError):
        return False
    return True


async def _wait_for_disconnect(receive: Receive) -> None:
    """Block until the ASGI server reports `http.disconnect`.

    Other receive events (for example a stray `http.request` body frame on
    a GET request) are ignored while streaming.
    """
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def _close_iterator(iterator: Any) -> None:
    """Best-effort `aclose()` of an async iterator after client disconnect.

    Closing throws `GeneratorExit` into a suspended generator so in-flight
    work (for example an LLM streaming call) stops promptly instead of
    running to completion unseen. Iterators without `aclose` are skipped;
    close failures are deliberately swallowed — the client is already gone.
    """
    aclose = getattr(iterator, "aclose", None)
    if aclose is None:
        return
    try:
        await asyncio.wait_for(aclose(), timeout=5.0)
    except Exception:  # client is gone; close failures are irrelevant
        return


async def _read_request_body(receive: Receive, limit: int) -> bytes:
    """Read the full request body, aborting once it exceeds `limit`.

    Raises:
        _BodyTooLarge: The body exceeded `limit`.
        _ClientDisconnected: The client disconnected mid-body.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        if message["type"] != "http.request":  # pragma: no cover - defensive
            raise _ClientDisconnected
        chunk: bytes = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _send_json_response(send: Send, status: int, payload: Mapping[str, Any]) -> None:
    """Send a small JSON error envelope and end the response."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _serve_ai_stream(
    receive: Receive,
    send: Send,
    iterator: AsyncIterator[Any],
    keep_alive_interval: float | None,
    error_text: Callable[[BaseException], str],
) -> None:
    """Pump chunk dicts into SSE frames until end, error, or disconnect.

    Mirrors the subscription transport's semantics: pull-based backpressure
    (one item is awaited, encoded, and sent before the next is pulled),
    `http.disconnect` cancels the pending `__anext__` and `aclose()`s the
    iterator, `data: [DONE]` terminates the stream whenever it ends while
    the client is still attached — including after an `error` chunk, like
    the official `JsonToSseTransformStream` flush.

    `Tracked` yields are unwrapped to their `data` (the protocol has no
    event-id concept), so a subscription-style generator that yields
    `tracked(id, chunk)` still streams its payloads here.
    """
    disconnect_waiter = asyncio.ensure_future(_wait_for_disconnect(receive))
    anext_task: asyncio.Task[Any] = asyncio.ensure_future(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait(
                {anext_task, disconnect_waiter},
                timeout=keep_alive_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_waiter in done:
                anext_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await anext_task
                await _close_iterator(iterator)
                return
            if anext_task not in done:
                # Idle interval elapsed: keep-alive comment; never pull ahead.
                if not await _send_frame(send, _KEEP_ALIVE_COMMENT):
                    return
                continue
            try:
                chunk = anext_task.result()
            except StopAsyncIteration:
                await _send_frame(send, _DONE_FRAME)
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if await _send_frame(send, _encode_chunk(_error_chunk(error_text(error)))):
                    await _send_frame(send, _DONE_FRAME)
                return
            if isinstance(chunk, Tracked):
                chunk = cast("Tracked[object]", chunk).data
            if not _is_mapping(chunk):
                message = f"AI SDK stream yielded a non-object chunk: {type(chunk).__name__}"
                if await _send_frame(send, _encode_chunk(_error_chunk(message))):
                    await _send_frame(send, _DONE_FRAME)
                return
            try:
                frame = _encode_chunk(chunk)
            except Exception as error:
                if await _send_frame(send, _encode_chunk(_error_chunk(error_text(error)))):
                    await _send_frame(send, _DONE_FRAME)
                return
            if not await _send_frame(send, frame):
                return
            anext_task = asyncio.ensure_future(iterator.__anext__())
    finally:
        if not anext_task.done():
            anext_task.cancel()
        disconnect_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await disconnect_waiter
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            await send({"type": "http.response.body", "body": b""})


def ai_sdk_endpoint(
    factory: AiStreamFactory,
    *,
    max_body_size: int = DEFAULT_MAX_BODY_SIZE,
    keep_alive_interval: float | None = None,
    error_text: Callable[[BaseException], str] = str,
) -> ASGIApp:
    """Create a plain ASGI app serving the AI SDK v5 UI message stream.

    The returned app answers `useChat`'s `DefaultChatTransport` contract
    directly — mount it under any path (e.g.
    `app.mount("/api/chat", ai_sdk_endpoint(chat))`) and point the transport
    at it. Responses are `text/event-stream` with the required
    `x-vercel-ai-ui-message-stream: v1` header; each yielded chunk becomes a
    `data: {json}` SSE frame and the stream ends with `data: [DONE]`,
    matching the official `pipeUIMessageStreamToResponse` byte-for-byte.

    `factory` is called per request with the decoded JSON request body —
    `{"messages": [...], ...}` for `useChat` POSTs, `None` for GET or an
    empty body — and the raw ASGI `scope` (for header-based auth). It
    returns a sync or async iterable of chunk mappings (or an awaitable of
    one); pair it with `ai_message_stream`/`ai_text_stream` or yield chunk
    dicts directly. A factory that raises before producing the iterable
    answers `500 {"error": ...}` instead of opening the stream.

    Disconnecting the client cancels the pending `__anext__` and `aclose()`s
    the iterable, so in-flight LLM calls stop. Backpressure is pull-based:
    the next chunk is awaited only after the previous frame was accepted by
    `send`, so a slow client never grows a buffer.

    Args:
        factory: Per-request `AiStreamFactory`.
        max_body_size: Largest accepted request body in bytes; larger POST
            bodies answer 413. Defaults to 1 MiB.
        keep_alive_interval: Seconds between `: keep-alive` SSE comments
            while the iterable is idle, or `None` (the default) to disable.
            When enabled a `: stream-open` comment leads the stream,
            mirroring the official keep-alive wrapper.
        error_text: Maps an exception raised by the factory or the iterable
            to the `errorText` of the `error` chunk (or the `500` body);
            defaults to `str`.

    Returns:
        An `ASGIApp` usable under `app.mount(...)`, `Route(...)`, or any
        ASGI server.

    Example:
        ```python
        from fastapi import FastAPI

        from trpc_rs_py.streaming import (
            ai_message_stream,
            ai_sdk_endpoint,
            ai_text_stream,
        )


        async def chat(body, scope):
            messages = body["messages"] if body else []
            return ai_message_stream(ai_text_stream(llm_reply(messages)))


        app = FastAPI()
        app.mount("/api/chat", ai_sdk_endpoint(chat))
        ```
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        elif scope["type"] == "http":
            await _serve_http(scope, receive, send)

    async def _serve_http(scope: Scope, receive: Receive, send: Send) -> None:
        method: str = scope["method"]
        if method.upper() not in ("GET", "POST"):
            await _send_json_response(
                send, 405, {"error": f"Method {method} is not supported; use GET or POST"}
            )
            return
        body_bytes = b""
        if method.upper() == "POST":
            try:
                body_bytes = await _read_request_body(receive, max_body_size)
            except _BodyTooLarge:
                await _send_json_response(send, 413, {"error": "Request body too large"})
                return
            except _ClientDisconnected:
                return
        body_value: Any = None
        if body_bytes:
            try:
                body_value = json.loads(body_bytes)
            except ValueError:
                await _send_json_response(send, 400, {"error": "Request body must be JSON"})
                return
        try:
            produced = factory(body_value, scope)
            if inspect.isawaitable(produced):
                produced = await produced
            iterator = _as_async_iterator(produced)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as error:
            await _send_json_response(send, 500, {"error": error_text(error)})
            return
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": list(_AI_SDK_HEADERS),
                }
            )
        except (ConnectionError, OSError, RuntimeError):
            return
        if keep_alive_interval is not None and not await _send_frame(send, _STREAM_OPEN_COMMENT):
            return
        await _serve_ai_stream(receive, send, iterator, keep_alive_interval, error_text)

    return app
