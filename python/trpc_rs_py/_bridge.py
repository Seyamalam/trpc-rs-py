"""WSGI↔ASGI bridging internals for the WSGI and sync-Django adapters.

A sync context drives an ASGI app by giving it a private `asyncio` event
loop in a daemon thread (ADR 0010). `send()` messages travel a bounded
`queue.Queue` — consumers pull them one at a time, which preserves the
adapter's pull-based backpressure for SSE streams — and closing the
returned iterable cancels the ASGI coroutine so subscription generators
clean up on disconnect.

The public entry point is `serve_asgi_sync`: one request in, a
`(status, headers, body_iterable)` triple out.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from queue import Full, Queue
from typing import Any

__all__ = ["WsgiResponse", "serve_asgi_sync", "strip_hop_by_hop"]

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def strip_hop_by_hop(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Drop RFC 7230 hop-by-hop headers a WSGI bridge must not forward.

    ASGI apps may legitimately emit `connection: keep-alive` (SSE responses
    do); WSGI forbids applications from sending hop-by-hop headers and
    wsgiref asserts on them. `connection` also names the headers it applies
    to — those are stripped too.

    Args:
        headers: ASGI `(name, value)` byte pairs.

    Returns:
        The header list without hop-by-hop entries.
    """
    items = list(headers)
    extra = {
        token.strip().lower()
        for name, value in items
        if name.lower() == b"connection"
        for token in value.decode("latin-1").split(",")
    }
    drop = _HOP_BY_HOP | extra
    return [(n, v) for n, v in items if n.decode("latin-1").lower() not in drop]


@dataclass(frozen=True)
class WsgiResponse:
    """A produced WSGI/ASGI response: status, headers, and a body iterable.

    The iterable yields encoded body chunks; `close()` it (explicitly or via
    the WSGI server) to cancel an in-flight ASGI coroutine — this is what
    stops a subscription stream when the client goes away.
    """

    status: int
    headers: list[tuple[bytes, bytes]]
    body: Iterable[bytes]


End = object()
"""Queue sentinel marking the end of an ASGI response."""


def make_receive(body: bytes) -> Callable[[], Any]:
    """Build an ASGI `receive` that serves one body then blocks forever.

    Disconnects cannot be observed from the sync side, so after the single
    `http.request` message the callable waits on an event that is never set;
    the subscription machinery's disconnect racer simply never wins.

    Args:
        body: The request body bytes to deliver once.

    Returns:
        An ASGI `receive` callable.
    """
    sent = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            await never.wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _pump(
    app: Callable[..., Any],
    scope: dict[str, Any],
    receive: Callable[[], Any],
    queue: Queue[Any],
    cancel: threading.Event,
    started: threading.Event,
) -> None:
    """Run the ASGI app on a fresh event loop until done or cancelled.

    Thread body: owns the loop, forwards `send()` messages into `queue` —
    `put_nowait` retried from a cancellable poll so a full queue applies real
    backpressure without non-daemon executor threads (a `to_thread` worker
    blocked on a full queue can never exit, which would hang the process),
    and posts `End` when the coroutine finishes — with or without error —
    so consumers always terminate.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def send(message: dict[str, Any]) -> None:
        while True:
            try:
                queue.put_nowait(message)
                return
            except Full:
                await asyncio.sleep(0.001)

    async def run() -> None:
        started.set()
        app_task = asyncio.ensure_future(app(scope, receive, send))
        while not cancel.is_set() and not app_task.done():
            await asyncio.wait({app_task}, timeout=0.01)
        if not app_task.done():
            app_task.cancel()
            await asyncio.wait({app_task})
        app_task.result()  # surface app exceptions into the thread

    try:
        loop.run_until_complete(run())
    except BaseException as error:  # surfaced to the consumer as a close
        queue.put(error)
    finally:
        started.set()  # never leave the caller waiting on a dead thread
        loop.close()
        queue.put(End)


def serve_asgi_sync(
    app: Callable[..., Any],
    scope: dict[str, Any],
    body: bytes = b"",
) -> WsgiResponse:
    """Serve one request through an ASGI app from sync code.

    Spawns a daemon thread running the app on a fresh event loop, waits for
    `http.response.start`, and returns the status, headers, and a body
    iterable whose chunks are the `http.response.body` payloads. The
    iterable ends when the ASGI coroutine finishes; closing it early cancels
    the coroutine.

    Args:
        app: The ASGI application callable.
        scope: ASGI `http` scope dict.
        body: Request body bytes delivered to `receive` once.

    Returns:
        The produced `WsgiResponse`.

    Example:
        ```python
        from trpc_rs_py import Router, mount
        from trpc_rs_py._bridge import serve_asgi_sync

        router = Router()

        @router.query
        def health() -> bool:
            return True

        resp = serve_asgi_sync(mount(router), {
            "type": "http", "method": "GET", "path": "/trpc/health",
            "query_string": b"", "headers": [], "scheme": "http",
            "server": ("test", 80), "client": ("test", 1),
        })
        assert b"".join(resp.body) == b'{"result":{"data":true}}'
        ```
    """
    queue: Queue[Any] = Queue(maxsize=1)
    cancel = threading.Event()
    started = threading.Event()
    thread = threading.Thread(
        target=_pump,
        args=(app, scope, make_receive(body), queue, cancel, started),
        daemon=True,
    )
    thread.start()
    started.wait()

    def bodies() -> Iterator[bytes]:
        try:
            while True:
                message = queue.get()
                if message is End:
                    return
                if isinstance(message, BaseException):
                    raise message
                if message["type"] != "http.response.body":
                    continue
                chunk: bytes = message.get("body", b"")
                if not message.get("more_body"):
                    yield chunk
                    return
                yield chunk
        finally:
            cancel.set()
            thread.join(timeout=5)

    # The first send must be http.response.start; block until it arrives.
    first = queue.get()
    if isinstance(first, BaseException):
        cancel.set()
        thread.join(timeout=5)
        raise first
    if first is End or first.get("type") != "http.response.start":
        cancel.set()
        thread.join(timeout=5)
        msg = "ASGI app ended without a response"
        raise RuntimeError(msg)
    return WsgiResponse(
        status=int(first["status"]),
        headers=strip_hop_by_hop(first.get("headers", [])),
        body=bodies(),
    )
