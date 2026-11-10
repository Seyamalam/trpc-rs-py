# pyright: reportMissingTypeStubs=false
"""Django adapter: sync and async views plus a URL include helper.

Import as `from trpc_rs_py.django import trpc_view, urlpatterns` — Django is
an optional dependency, imported lazily inside each helper.

Both views run the same ASGI dispatch path as `mount()` (ADR 0010): the
sync view pumps the ASGI app on a private event loop and returns a
`StreamingHttpResponse` (SSE streams through it, one worker thread per open
stream — the documented WSGI limitation); the async view drives it directly
inside Django's ASGI request context.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from typing import TYPE_CHECKING, Any

from trpc_rs_py._bridge import make_receive, serve_asgi_sync, strip_hop_by_hop

if TYPE_CHECKING:
    from trpc_rs_py.router import Router

__all__ = ["asgi_view", "asgi_view_async", "trpc_view", "trpc_view_async", "urlpatterns"]


def _mount_app(router: Router, kwargs: dict[str, Any]) -> Callable[..., Any]:
    """Build the configured ASGI app shared by both views."""
    from trpc_rs_py.asgi import DEFAULT_MAX_SSE_EVENT_SIZE, mount

    if kwargs.get("max_sse_event_size") is None:
        kwargs["max_sse_event_size"] = DEFAULT_MAX_SSE_EVENT_SIZE
    return mount(router, path="", **kwargs)


def _request_body(request: Any) -> bytes:
    """Read the request body; `request.body` raises on some Django setups."""
    try:
        body: bytes = request.body
        return body
    except Exception:
        return b""


def _to_django_response(response: Any) -> Any:
    """Turn a `WsgiResponse` into a Django response, streaming for SSE."""
    from django.http import HttpResponse, StreamingHttpResponse

    headers = _text_headers(response.headers)
    if headers.get("content-type", "").startswith("text/event-stream"):
        return StreamingHttpResponse(response.body, status=response.status, headers=headers)
    return HttpResponse(b"".join(response.body), status=response.status, headers=headers)


def _scope_from_request(request: Any, trpc_path: str) -> dict[str, Any]:
    """Translate a Django `HttpRequest` into an ASGI `http` scope.

    The dotted procedure path comes from the `trpc_path` URL parameter
    (batches keep their comma separator); headers pass through from
    `request.headers` so the dispatch layer still sees `content-type`,
    `content-length`, and `last-event-id`.
    """
    # An empty `Content-Length` (set by some WSGI servers, e.g. wsgiref, on
    # requests without bodies) is meaningless and rejected by the protocol
    # layer — strip it like the WSGI adapter strips empty CONTENT_LENGTH.
    headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in request.headers.items()
        if not (name.lower() == "content-length" and not value.strip())
    ]
    path = f"/{trpc_path}" if trpc_path else "/"
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": request.scheme,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": request.META.get("QUERY_STRING", "").encode("latin-1"),
        "root_path": "",
        "headers": headers,
        "client": (request.META.get("REMOTE_ADDR", ""), 0),
        "server": ("django", 0),
    }


def _text_headers(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """Decode ASGI byte headers into a Django-friendly `dict` (last wins)."""
    return {name.decode("latin-1"): value.decode("latin-1") for name, value in headers}


def asgi_view(asgi_app: Callable[..., Any]) -> Callable[..., Any]:
    """Return a Django *sync* view serving an arbitrary ASGI `http` app.

    The generic form of `trpc_view` — use it to mount `mount_rest(router)`
    (or any other ASGI app) inside a Django URLconf. The URL pattern must
    capture the remaining path under the converter name `path`.

    Args:
        asgi_app: The ASGI application to serve.

    Returns:
        A Django view callable `(request, path="") -> HttpResponse`;
        `StreamingHttpResponse` when the app answers with SSE.

    Example:
        ```python
        from django.urls import path
        from trpc_rs_py import mount_rest
        from trpc_rs_py.django import asgi_view

        urlpatterns = [path("api/<path:path>", asgi_view(mount_rest(router)))]
        ```
    """

    def view(request: Any, path: str = "") -> Any:
        """Serve one Django request through the ASGI dispatch path."""
        response = serve_asgi_sync(
            asgi_app, _scope_from_request(request, path), _request_body(request)
        )
        return _to_django_response(response)

    return view


def trpc_view(
    router: Router,
    *,
    production: bool = False,
    limits: Any = None,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int | None = None,
) -> Callable[..., Any]:
    """Return a Django *sync* view serving `router` as tRPC v11 endpoints.

    Queries and mutations return a `HttpResponse`; SSE subscriptions return a
    `StreamingHttpResponse` — one worker thread per open stream, disconnects
    noticed on write (the documented WSGI limitations).

    Args:
        router: The router whose procedures are served.
        production: Forwarded to `mount()`.
        limits: Forwarded to `mount()`.
        sse_ping_interval: Forwarded to `mount()` — recommended under WSGI.
        max_sse_event_size: Forwarded to `mount()`.

    Returns:
        A Django view callable `(request, trpc_path="") -> HttpResponse`.

    Example:
        ```python
        from django.urls import path
        from trpc_rs_py.django import trpc_view
        from myapp.trpc import router

        urlpatterns = [path("trpc/<path:trpc_path>", trpc_view(router))]
        ```
    """
    generic = asgi_view(
        _mount_app(
            router,
            {
                "production": production,
                "limits": limits,
                "sse_ping_interval": sse_ping_interval,
                "max_sse_event_size": max_sse_event_size,
            },
        )
    )

    def view(request: Any, trpc_path: str = "") -> Any:
        """Serve one Django request through the ASGI dispatch path."""
        return generic(request, trpc_path)

    return view


def asgi_view_async(asgi_app: Callable[..., Any]) -> Callable[..., Any]:
    """Return a Django *async* view serving an arbitrary ASGI `http` app.

    The generic form of `trpc_view_async` — use it to mount
    `mount_rest(router)` (or any other ASGI app) inside an async Django
    URLconf. The URL pattern must capture the remaining path under the
    converter name `path`. SSE responses stream with real pull-based
    backpressure on Django's own event loop.

    Args:
        asgi_app: The ASGI application to serve.

    Returns:
        An async Django view callable `(request, path="")`.

    Example:
        ```python
        from django.urls import path
        from trpc_rs_py import mount_rest
        from trpc_rs_py.django import asgi_view_async

        urlpatterns = [path("api/<path:path>", asgi_view_async(mount_rest(router)))]
        ```
    """
    from django.http import HttpResponse, StreamingHttpResponse

    async def view(request: Any, path: str = "") -> Any:
        """Serve one Django request through the ASGI dispatch path."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def send(message: dict[str, Any]) -> None:
            await queue.put(message)

        task = asyncio.ensure_future(
            asgi_app(_scope_from_request(request, path), make_receive(_request_body(request)), send)
        )
        get = asyncio.ensure_future(queue.get())
        done, _ = await asyncio.wait({get, task}, return_when=asyncio.FIRST_COMPLETED)
        if get not in done:
            get.cancel()
            task.result()  # surface the app's own exception
            msg = "ASGI app ended without a response"
            raise RuntimeError(msg)
        first = get.result()
        status: int = first["status"]

        async def body_iter() -> AsyncIterator[bytes]:
            pending: asyncio.Future[dict[str, Any]] | None = None
            try:
                while True:
                    pending = asyncio.ensure_future(queue.get())
                    finished, _ = await asyncio.wait(
                        {pending, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if pending not in finished:
                        pending.cancel()
                        task.result()
                        return
                    message = pending.result()
                    if message["type"] == "http.response.body":
                        yield message.get("body", b"")
                        if not message.get("more_body"):
                            task.result()
                            return
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                if not task.done():
                    task.cancel()

        headers = _text_headers(strip_hop_by_hop(first.get("headers", [])))
        if headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingHttpResponse(body_iter(), status=status, headers=headers)
        chunks: list[bytes] = [chunk async for chunk in body_iter()]
        return HttpResponse(b"".join(chunks), status=status, headers=headers)

    return view


def trpc_view_async(
    router: Router,
    *,
    production: bool = False,
    limits: Any = None,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int | None = None,
) -> Callable[..., Any]:
    """Return a Django *async* view serving `router` as tRPC v11 endpoints.

    Runs the ASGI app on Django's ASGI event loop directly — SSE streams
    through an async `StreamingHttpResponse` with real pull-based
    backpressure and no extra threads. Django also runs this view correctly
    under WSGI (it wraps async views itself), though the sync view is
    cheaper there.

    Args:
        router: The router whose procedures are served.
        production: Forwarded to `mount()`.
        limits: Forwarded to `mount()`.
        sse_ping_interval: Forwarded to `mount()`.
        max_sse_event_size: Forwarded to `mount()`.

    Returns:
        An async Django view callable `(request, trpc_path="")`.
    """
    generic = asgi_view_async(
        _mount_app(
            router,
            {
                "production": production,
                "limits": limits,
                "sse_ping_interval": sse_ping_interval,
                "max_sse_event_size": max_sse_event_size,
            },
        )
    )

    async def view(request: Any, trpc_path: str = "") -> Any:
        """Serve one Django request through the ASGI dispatch path."""
        return await generic(request, trpc_path)

    return view


def urlpatterns(
    router: Router,
    *,
    prefix: str = "trpc",
    asynchronous: bool = False,
    production: bool = False,
    limits: Any = None,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int | None = None,
) -> list[Any]:
    """Return `path()` entries serving `router` — the URL include helper.

    Args:
        router: The router whose procedures are served.
        prefix: URL prefix for the tRPC endpoints; `/trpc/posts.byId` reaches
            procedure `posts.byId`. Defaults to `"trpc"`.
        asynchronous: Pick the async view (`trpc_view_async`) when `True`,
            the sync view (`trpc_view`) otherwise. Django can run either
            under both WSGI and ASGI; pick the sync view for WSGI
            deployments and the async view for ASGI ones.
        production: Forwarded to the view.
        limits: Forwarded to the view.
        sse_ping_interval: Forwarded to the view.
        max_sse_event_size: Forwarded to the view.

    Returns:
        A list of `django.urls.path()` objects; spread into `urlpatterns`.

    Example:
        ```python
        # mysite/urls.py
        from trpc_rs_py.django import urlpatterns as trpc_urlpatterns
        from myapp.trpc import router

        urlpatterns = [
            path("admin/", admin.site.urls),
            *trpc_urlpatterns(router, asynchronous=True),
        ]
        ```
    """
    from django.urls import path as django_path

    kwargs: dict[str, Any] = {
        "production": production,
        "limits": limits,
        "sse_ping_interval": sse_ping_interval,
        "max_sse_event_size": max_sse_event_size,
    }
    view = trpc_view_async(router, **kwargs) if asynchronous else trpc_view(router, **kwargs)
    return [django_path(f"{prefix}/<path:trpc_path>", view)]
