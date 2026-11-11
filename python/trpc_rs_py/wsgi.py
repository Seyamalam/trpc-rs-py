"""WSGI adapter: serve a `Router` from Flask or any WSGI application.

`mount_wsgi(router)` returns a standard WSGI callable. Every request is
translated to an ASGI scope and served by the same dispatch path as the ASGI
adapters (ADR 0010) — one protocol implementation, including SSE
subscriptions, which stream through the WSGI response iterable.

WSGI streaming limitations (also in `docs/adapters.md`):

* One open SSE stream holds a worker thread for its lifetime.
* Client disconnects surface only on write; the iterable's `close()` — which
  WSGI servers call when the connection drops — cancels the stream. Use
  `sse_ping_interval` so dead connections are noticed promptly.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from trpc_rs_py._bridge import WsgiResponse, serve_asgi_sync

if TYPE_CHECKING:
    from trpc_rs_py.router import Router

__all__ = ["attach_flask", "mount_wsgi", "wsgi_from_asgi"]

_HEADER_ENVIRON = re.compile(r"^HTTP_")
"""WSGI `HTTP_*` environ keys become request headers."""


def wsgi_from_asgi(
    asgi_app: Callable[..., Any],
) -> Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]:
    """Wrap any ASGI `http` application as a WSGI callable.

    The generic form of `mount_wsgi`: useful for mounting the REST/OpenAPI
    app (`mount_rest`) or any other ASGI app inside a WSGI server. Same
    per-request event loop and streaming semantics as `mount_wsgi`.

    Args:
        asgi_app: The ASGI application to wrap.

    Returns:
        A WSGI application `app(environ, start_response) -> iterable`.

    Example:
        ```python
        from trpc_rs_py import Router, mount_rest, wsgi_from_asgi

        router = Router()
        rest_app = wsgi_from_asgi(mount_rest(router))
        # mount as {"/api": rest_app} in a DispatcherMiddleware
        ```
    """

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        """Serve one WSGI request through the ASGI dispatch path."""
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        try:
            response = serve_asgi_sync(asgi_app, _scope_from_environ(environ), body)
        except Exception as error:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [str(error).encode("utf-8", errors="replace")]
        try:
            reason = HTTPStatus(response.status).phrase
        except ValueError:
            reason = ""
        status_text = f"{response.status} {reason}".rstrip()
        start_response(status_text, _header_list(response))
        return response.body

    return app


def _scope_from_environ(environ: dict[str, Any]) -> dict[str, Any]:
    """Translate a WSGI `environ` into an ASGI `http` scope.

    Header names are recovered from `HTTP_*` keys with `CONTENT_TYPE` and
    `CONTENT_LENGTH` handled specially; `PATH_INFO` is decoded back from
    WSGI's latin-1 convention to the Unicode form ASGI expects.

    Args:
        environ: The WSGI environ mapping.

    Returns:
        An ASGI `http` scope dict.
    """
    headers: list[tuple[bytes, bytes]] = [
        (
            key[5:].replace("_", "-").lower().encode("latin-1"),
            value.encode("latin-1"),
        )
        for key, value in environ.items()
        if _HEADER_ENVIRON.match(key)
    ]
    if environ.get("CONTENT_TYPE"):
        headers.append((b"content-type", environ["CONTENT_TYPE"].encode("latin-1")))
    if environ.get("CONTENT_LENGTH"):
        headers.append((b"content-length", environ["CONTENT_LENGTH"].encode("latin-1")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").rsplit("/", 1)[-1],
        "method": environ["REQUEST_METHOD"],
        "scheme": environ.get("wsgi.url_scheme", "http"),
        "path": environ.get("PATH_INFO", "").encode("latin-1").decode("utf-8"),
        "raw_path": environ.get("PATH_INFO", "").encode("latin-1"),
        "query_string": environ.get("QUERY_STRING", "").encode("latin-1"),
        "root_path": environ.get("SCRIPT_NAME", ""),
        "headers": headers,
        "client": (environ.get("REMOTE_ADDR", ""), 0),
        "server": (
            environ.get("SERVER_NAME", ""),
            int(environ.get("SERVER_PORT") or 0),
        ),
        # Framework-native request for RequestContext.request (see
        # trpc_rs_py.context): under WSGI the environ is the object users
        # expect, not the translated scope.
        "trpc.request": environ,
    }


def mount_wsgi(
    router: Router,
    *,
    path: str = "/trpc",
    production: bool = False,
    limits: Any = None,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int | None = None,
    context_factory: Any = None,
    middleware: Any = (),
    panel: bool | None = None,
) -> Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]:
    """Return a WSGI app serving `router` as tRPC v11 endpoints.

    Protocol behavior — batching, error policy, SSE — is identical to
    `mount()` (the same dispatch code runs on a per-request event loop, per
    ADR 0010). Mount it with `werkzeug.middleware.dispatcher.
    DispatcherMiddleware`, or serve it directly under any WSGI server.

    Args:
        router: The router whose procedures are served.
        path: Mount path prefix — the procedure path is taken from the URL
            suffix after it, matching `mount()`'s behavior. Defaults to
            `/trpc`.
        production: When `True`, internal errors never leak details; see
            `mount()` for the exact policy.
        limits: Request limits (body size, batch size, JSON depth, procedure
            timeout); protocol defaults when `None`.
        sse_ping_interval: Seconds between SSE `ping` frames on idle streams,
            or `None` (default). Recommended under WSGI so dropped
            connections are noticed on the next write.
        max_sse_event_size: Largest single encoded SSE frame in bytes;
            defaults to the adapter default.
        context_factory: Optional factory called once per request with the
            `RequestContext`; the returned object is injected into handlers
            whose first parameter is named `ctx`. Defaults to the
            `RequestContext` itself.
        middleware: Mount-level middleware applied outermost-first ahead of
            each procedure's router-level chain; forwarded to `mount()`
            (see `trpc_rs_py.middleware`).
        panel: Serve the built-in playground at `{path}/panel`; forwarded to
            `mount()` (enabled in development only, unless explicit).

    Returns:
        A WSGI application `app(environ, start_response) -> iterable`.

    Example:
        ```python
        from trpc_rs_py import Router, mount_wsgi

        router = Router()

        @router.query
        def health() -> bool:
            return True

        app = mount_wsgi(router)  # serve under any WSGI server at /trpc/*
        ```
    """
    from trpc_rs_py.asgi import DEFAULT_MAX_SSE_EVENT_SIZE, mount

    asgi_app = mount(
        router,
        path=path,
        production=production,
        limits=limits,
        sse_ping_interval=sse_ping_interval,
        max_sse_event_size=(
            DEFAULT_MAX_SSE_EVENT_SIZE if max_sse_event_size is None else max_sse_event_size
        ),
        context_factory=context_factory,
        middleware=middleware,
        panel=panel,
    )
    return wsgi_from_asgi(asgi_app)


def _header_list(response: WsgiResponse) -> list[tuple[str, str]]:
    """Convert ASGI byte headers into the str pairs WSGI expects."""
    return [(name.decode("latin-1"), value.decode("latin-1")) for name, value in response.headers]


def attach_flask(
    app: Any,
    router: Router,
    *,
    path: str = "/trpc",
    production: bool = False,
    limits: Any = None,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int | None = None,
    context_factory: Any = None,
    middleware: Any = (),
    panel: bool | None = None,
) -> Any:
    """Mount `router` under `path` on a Flask (or Werkzeug) application.

    Uses `werkzeug.middleware.dispatcher.DispatcherMiddleware` so the tRPC
    app gets its own mount point without touching the Flask app's routes.
    Requires `werkzeug` (installed with Flask).

    Args:
        app: The Flask (or plain Werkzeug) application object.
        router: The router whose procedures are served.
        path: Mount path prefix; defaults to `/trpc`.
        production: Forwarded to `mount_wsgi`.
        limits: Forwarded to `mount_wsgi`.
        sse_ping_interval: Forwarded to `mount_wsgi`.
        max_sse_event_size: Forwarded to `mount_wsgi`.
        context_factory: Forwarded to `mount_wsgi`.
        middleware: Mount-level middleware; forwarded to `mount_wsgi`.
        panel: Serve the built-in playground at `{path}/panel`; forwarded to
            `mount_wsgi`.

    Returns:
        The same `app`, with its `wsgi_app` wrapped by the dispatcher.

    Example:
        ```python
        from flask import Flask

        from trpc_rs_py import Router, attach_flask

        router = Router()

        @router.query
        def health() -> bool:
            return True

        app = Flask(__name__)
        attach_flask(app, router)
        # GET /trpc/health now serves tRPC; existing routes unchanged.
        ```
    """
    try:
        from werkzeug.middleware.dispatcher import DispatcherMiddleware
    except ImportError as error:  # pragma: no cover - werkzeug ships with Flask
        msg = "attach_flask requires werkzeug (installed with Flask)"
        raise RuntimeError(msg) from error

    trpc_app = mount_wsgi(
        router,
        path=path,
        production=production,
        limits=limits,
        sse_ping_interval=sse_ping_interval,
        max_sse_event_size=max_sse_event_size,
        context_factory=context_factory,
        middleware=middleware,
        panel=panel,
    )
    mounts = {path: trpc_app}
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, mounts)
    return app
