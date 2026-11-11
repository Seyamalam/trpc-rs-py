"""Standalone server: serve a `Router` or ASGI app on the Rust HTTP engine.

`serve()` is the backend for `trpc-py serve`: it mounts the router exactly
like `mount()` and drives it with the `hyper`-based server embedded in
`trpc_rs_py._core` — a Rust ASGI server in-process (ADR 0013). Any ASGI app
can be served via `app=`, including `cors(mount(...))` wrappers.

Threading model: the calling thread blocks inside the Rust server (with the
GIL released) while a dedicated daemon thread runs an `asyncio` loop; every
request's ASGI coroutine is scheduled onto that loop, exactly like uvicorn
schedules onto its own. Shutdown is Ctrl-C, or the loop stopping — either
returns `serve()` cleanly.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from typing import TYPE_CHECKING, Any

from ._core import Limits
from ._core import serve as _rust_serve
from .asgi import mount

if TYPE_CHECKING:
    from .asgi import ASGIApp, Message
    from .router import Router

__all__ = ["serve"]


def _schedule(  # pyright: ignore[reportUnusedFunction]  # called from Rust
    app: ASGIApp,
    scope: Any,
    body: bytes,
    bridge: Any,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Schedule one ASGI request on `loop`, streaming through `bridge`.

    Called by the Rust server on a tokio worker thread for every request.
    `bridge` is a `_core._AsgiBridge`: `start` captures the response head,
    `offer` pushes one body chunk (returning `False` on a full channel),
    `finish` ends the body, and `done` reports app completion.

    Backpressure: when `offer` reports a full channel, `send` awaits the
    `drained` event; Rust re-arms it via `loop.call_soon_threadsafe` as soon
    as the socket pulls a chunk, so the `asyncio` loop never blocks on I/O.
    Disconnect: Rust sets `disconnected` when the body stream dies, waking
    `receive()` into `http.disconnect` and `send()` into `ConnectionError`.
    """
    disconnected = asyncio.Event()
    drained = asyncio.Event()
    bridge.arm(disconnected.set, drained.set)
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            await disconnected.wait()
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        message_type = message["type"]
        if message_type == "http.response.start":
            bridge.start(message["status"], message.get("headers", []))
            return
        if message_type != "http.response.body":
            return
        chunk = bytes(message.get("body", b""))
        while chunk:
            # Clear before each offer: a Rust-side drain between clear() and
            # offer() still leaves capacity, and a drain after a failed
            # offer() re-arms the event — neither ordering can lose a wakeup.
            drained.clear()
            if bridge.offer(chunk):
                break
            await drained.wait()
        if not message.get("more_body", False):
            bridge.finish()

    async def invoke() -> None:
        await app(scope, receive, send)

    future = asyncio.run_coroutine_threadsafe(invoke(), loop)

    def _done(fut: concurrent.futures.Future[None]) -> None:
        try:
            error: BaseException | None = fut.exception()
        except concurrent.futures.CancelledError as cancelled:
            error = cancelled
        bridge.done(error)

    future.add_done_callback(_done)


def serve(
    router: Router | None = None,
    *,
    app: ASGIApp | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    production: bool = False,
    **mount_kwargs: Any,
) -> int:
    """Serve `router` (or a ready ASGI `app`) on the standalone Rust server.

    Blocks until Ctrl-C (SIGINT), then shuts down gracefully and returns 0.
    The router is mounted with `mount(router, production=production,
    **mount_kwargs)` — every mount option (`path`, `limits`,
    `sse_ping_interval`, `context_factory`, `middleware`, `panel`, ...)
    applies unchanged. Pass `app=` instead to serve any ASGI app directly —
    for example `cors(mount(router))` — in which case router and mount
    options must be absent.

    Args:
        router: The router whose procedures are served, or `None` when
            `app` is given.
        app: A ready-made ASGI application to serve instead of a router.
        host: Bind address; defaults to `"127.0.0.1"`.
        port: Bind port; defaults to `8000`.
        production: Forwarded to `mount()` — redacts internal errors and
            disables the panel by default. Ignored when `app` is given.
        **mount_kwargs: Additional `mount()` keyword arguments.

    Returns:
        `0` after a graceful shutdown.

    Raises:
        ValueError: Neither or both of `router`/`app` were given, or mount
            options were mixed with `app`.
        OSError: The socket could not be bound.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.serve import serve

        router = Router()

        @router.query
        def greeting(name: str) -> str:
            return f"Hello {name}!"

        serve(router, port=8000)  # GET /trpc/greeting?input={"name":"Ada"}
        ```
    """
    if app is not None and (router is not None or production or mount_kwargs):
        msg = "serve() takes either a router (+mount options) or app=, not both"
        raise ValueError(msg)
    if app is None:
        if router is None:
            msg = "serve() needs a Router or an ASGI app="
            raise ValueError(msg)
        app = mount(router, production=production, **mount_kwargs)
    limits = mount_kwargs.get("limits")
    max_body_size = limits.max_body_size if limits is not None else Limits().max_body_size

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, name="trpc-rs-py-serve", daemon=True)
    loop_thread.start()
    try:
        _rust_serve(app, loop, host, port, max_body_size=max_body_size)
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()
    return 0
