"""In-process test clients for tRPC routers (ADR 0011).

`test_client(router)` dispatches requests against the real ASGI app built
by `mount()` — no socket, no server fixture, no framework dependency.
Each call runs the app coroutine on a fresh `asyncio.run` loop with
in-memory `receive`/`send` callables, so the full protocol path
(parsing, validation, context, middleware, serialization) is exercised.

For framework parity, `flask_test_client` and `django_test_client` hand
back each framework's *own* test client wrapping the same router, so
tests assert against werkzeug/Django responses exactly as production
requests see them.

All methods are synchronous. The client must not be used from inside a
running event loop (`asyncio.run` rejects nesting); async tests should
call the ASGI `app` directly or use `django_test_client(asynchronous=True)`.

Example:
    ```python
    from trpc_rs_py import Router
    from trpc_rs_py.testing import test_client

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"

    client = test_client(router)
    assert client.query("greeting", {"name": "Ada"}) == "Hello Ada!"
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import types
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

from .asgi import ASGIApp, Message, mount
from .context import ContextFactory
from .middleware import Middleware
from .router import Router
from .wsgi import mount_wsgi

if TYPE_CHECKING:
    from django.test import AsyncClient as _DjangoAsyncClient
    from django.test import Client as _DjangoClient
    from werkzeug.test import Client as _WerkzeugClient

__all__ = [
    "SubscriptionEvent",
    "TestClient",
    "TrpcTestClientError",
    "django_test_client",
    "flask_test_client",
    "test_client",
]

_SSE_FRAME_END = b"\n\n\n"
"""Frame terminator produced by `SseEncoder` (`field\\n` + `\\n\\n`)."""


def _dumps(value: Any) -> str:
    """Serialize `value` as compact JSON (the tRPC `input` wire form)."""
    return json.dumps(value, separators=(",", ":"))


def _header_list(headers: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    """Encode `(name, value)` text pairs into ASGI byte pairs."""
    return [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers]


def _error_from_envelope(envelope: Mapping[str, Any]) -> TrpcTestClientError:
    """Build a `TrpcTestClientError` from a wire `{"error": ...}` member."""
    raw_data = envelope.get("data")
    data = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else {}
    message = envelope.get("message")
    code = data.get("code", envelope.get("code", "UNKNOWN"))
    return TrpcTestClientError(
        str(code),
        str(message) if message is not None else "tRPC call failed",
        data=data or None,
        http_status=data.get("httpStatus"),
    )


def _parse_sse_fields(raw: bytes) -> dict[str, str]:
    """Parse one SSE frame body into a `field -> value` map.

    Each line is `name: value`; per the SSE spec a single leading space is
    stripped from the value. First occurrence wins.
    """
    fields: dict[str, str] = {}
    for line in raw.decode("utf-8").split("\n"):
        name, separator, value = line.partition(":")
        if not separator or name in fields:
            continue
        fields[name] = value[1:] if value.startswith(" ") else value
    return fields


class TrpcTestClientError(Exception):
    """A tRPC error envelope raised by the test client.

    `query`, `mutation`, and `subscribe` raise it; `batch` returns it
    per-call in the results list so a partial batch failure is inspectable
    without exception handling.

    Attributes:
        code: The official string code from `error.data.code`
            (`"NOT_FOUND"`, `"BAD_REQUEST"`, ...).
        message: The `error.message` text.
        data: The `error.data` mapping (holds `code`, `httpStatus`,
            `path`, and any typed-error extras), or `None`.
        http_status: `error.data.httpStatus` when present.

    Example:
        ```python
        import pytest

        with pytest.raises(TrpcTestClientError) as excinfo:
            client.query("missing")
        assert excinfo.value.code == "NOT_FOUND"
        ```
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: Any = None,
        http_status: int | None = None,
    ) -> None:
        """Initialize from the wire error shape."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.http_status = http_status

    def __str__(self) -> str:
        """Return `"CODE: message"`."""
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class SubscriptionEvent:
    """One decoded data event from a subscription stream.

    Attributes:
        data: The JSON-decoded event payload.
        id: The SSE `id` for `tracked()` events, else `None`.

    Example:
        ```python
        events = client.subscribe("ticks")
        assert events[0].data == {"tick": 0}
        assert events[0].id == "0"
        ```
    """

    data: Any
    id: str | None = None


class TestClient:
    """Sync in-process client for a `Router`, built by `test_client()`.

    Each method builds an ASGI scope, runs the app to completion on a
    private event loop, and unwraps the tRPC envelope. `app` is the raw
    ASGI application for driving requests this client does not model;
    `requests` records one `{"method", "path", "query_string"}` entry per
    dispatched request for assertions.
    """

    __test__ = False  # keep pytest from collecting this as a test class

    def __init__(self, app: ASGIApp, *, path: str = "/trpc") -> None:
        """Wrap an ASGI app; prefer `test_client(router)` over direct use.

        Args:
            app: The ASGI application, e.g. from `mount(router)`.
            path: The mount prefix `app` was built with.
        """
        self.app = app
        self._prefix = path.rstrip("/")
        self.requests: list[dict[str, str]] = []

    def query(
        self,
        proc_path: str,
        input: Any = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Run one query procedure over GET and return `result.data`.

        Args:
            proc_path: Dotted procedure path (`"posts.by_id"`).
            input: JSON-ready input; `None` sends no `input` parameter.
            headers: Extra request headers.

        Returns:
            The procedure's result data.

        Raises:
            TrpcTestClientError: The response carried an error envelope.

        Example:
            ```python
            client.query("echo", {"message": "hi"})
            ```
        """
        params = {} if input is None else {"input": _dumps(input)}
        _status, _headers, body = self._request("GET", proc_path, urlencode(params), headers)
        return _unwrap(json.loads(body))

    def mutation(
        self,
        proc_path: str,
        input: Any = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Run one mutation procedure over POST and return `result.data`.

        Args:
            proc_path: Dotted procedure path.
            input: JSON body; `None` posts an empty body.
            headers: Extra request headers.

        Returns:
            The procedure's result data.

        Raises:
            TrpcTestClientError: The response carried an error envelope.

        Example:
            ```python
            client.mutation("posts.create", {"title": "hi"})
            ```
        """
        body = b"" if input is None else _dumps(input).encode("utf-8")
        merged = {"content-type": "application/json", **dict(headers or {})}
        _status, _headers, raw = self._request("POST", proc_path, "", headers=merged, body=body)
        return _unwrap(json.loads(raw))

    def batch(
        self,
        calls: Iterable[Mapping[str, Any]],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> list[Any]:
        """Run several calls in one batched GET (`?batch=1`).

        Args:
            calls: Mappings with a `"path"` key and an optional `"input"`
                key. All calls must share one procedure kind, matching the
                wire contract.
            headers: Extra request headers.

        Returns:
            Per-call list: each entry is the call's `result.data`, or a
            `TrpcTestClientError` *instance* for calls that failed (the
            list is returned, not raised, so partial failures are
            inspectable). A top-level error (for example a mixed-kind
            batch) is raised instead.

        Raises:
            TrpcTestClientError: The batch itself failed before dispatch.

        Example:
            ```python
            results = client.batch([
                {"path": "ping"},
                {"path": "echo", "input": {"message": "x"}},
            ])
            ```
        """
        call_list = list(calls)
        paths = ",".join(str(call["path"]) for call in call_list)
        input_map = {str(index): call.get("input") for index, call in enumerate(call_list)}
        query_string = urlencode({"batch": "1", "input": _dumps(input_map)})
        _status, _headers, body = self._request("GET", paths, query_string, headers)
        payload = json.loads(body)
        if isinstance(payload, dict) and "error" in payload:
            raise _error_from_envelope(cast("dict[str, Any]", payload)["error"])
        if not isinstance(payload, list):
            msg = f"Unexpected batch response: {payload!r}"
            raise RuntimeError(msg)
        results: list[Any] = []
        for item in cast("Sequence[Any]", payload):
            if isinstance(item, dict) and "error" in item:
                entry = cast("dict[str, Any]", item)
                results.append(_error_from_envelope(entry["error"]))
            elif isinstance(item, dict) and "result" in item:
                results.append(cast("dict[str, Any]", item)["result"].get("data"))
            else:
                msg = f"Unexpected batch entry: {item!r}"
                raise RuntimeError(msg)
        return results

    def subscribe(
        self,
        proc_path: str,
        input: Any = None,
        *,
        headers: Mapping[str, str] | None = None,
        last_event_id: str | None = None,
        max_events: int = 100,
        timeout: float | None = 30.0,
    ) -> list[SubscriptionEvent]:
        """Drive a subscription's SSE stream and collect its data events.

        The stream runs until the generator ends, `max_events` data events
        have arrived, or `timeout` seconds pass — whichever comes first.
        When `max_events` is hit the client synthesizes an
        `http.disconnect`, which runs the adapter's real disconnect path
        (the pending `__anext__` is cancelled and the generator closed), so
        never-ending generators cannot hang the test.

        Args:
            proc_path: Dotted subscription procedure path.
            input: JSON-ready input; `None` sends no `input` parameter.
            headers: Extra request headers.
            last_event_id: Resume id sent as the `last-event-id` header.
            max_events: Stop after this many data events; `0` disconnects
                before the first event.
            timeout: Seconds before the client gives up and disconnects
                even if the generator is blocked mid-yield, or `None` to
                wait forever.

        Returns:
            The decoded `data:` events in stream order (`connected`,
            `ping`, `return`, and `restore-id` control frames excluded).

        Raises:
            TrpcTestClientError: The stream ended in `serialized-error`,
                or the path answered with a JSON error envelope.
            RuntimeError: The procedure did not produce an SSE stream.

        Example:
            ```python
            events = client.subscribe("ticks", {"count": 2})
            assert [event.data for event in events] == [{"tick": 0}, {"tick": 1}]
            ```
        """
        params = {} if input is None else {"input": _dumps(input)}
        header_pairs: list[tuple[str, str]] = []
        if last_event_id is not None:
            header_pairs.append(("last-event-id", last_event_id))
        header_pairs.extend((headers or {}).items())
        scope = self._scope("GET", proc_path, urlencode(params), header_pairs)

        events, error, is_sse, json_body = self._collect_sse(
            scope, max_events=max_events, timeout=timeout
        )
        if not is_sse:
            payload = json.loads(json_body) if json_body else None
            if isinstance(payload, dict) and "error" in payload:
                raise _error_from_envelope(cast("dict[str, Any]", payload)["error"])
            msg = f'Procedure "{proc_path}" did not produce an SSE stream'
            raise RuntimeError(msg)
        if error is not None:
            raise error
        return events

    # -- internals ---------------------------------------------------------

    def _scope(
        self,
        method: str,
        proc_path: str,
        query_string: str,
        headers: Iterable[tuple[str, str]],
    ) -> dict[str, Any]:
        """Build the ASGI `http` scope for one request and record it."""
        path = f"{self._prefix}/{proc_path}" if self._prefix else f"/{proc_path}"
        self.requests.append({"method": method, "path": path, "query_string": query_string})
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("latin-1"),
            "query_string": query_string.encode("latin-1"),
            "root_path": "",
            "headers": _header_list(headers),
            "client": ("trpc-test-client", 0),
            "server": ("trpc-test-client", 80),
        }

    def _request(
        self,
        method: str,
        proc_path: str,
        query_string: str,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        """Dispatch one request through the app on a fresh event loop."""
        scope = self._scope(method, proc_path, query_string, (headers or {}).items())
        return asyncio.run(self._drive(scope, body))

    async def _drive(
        self, scope: dict[str, Any], body: bytes
    ) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        """Run the app once; return `(status, headers, body)`."""
        body_sent = False
        status = 0
        response_headers: list[tuple[bytes, bytes]] = []
        chunks: list[bytes] = []

        async def receive() -> Message:
            nonlocal body_sent
            if body_sent:
                return {"type": "http.disconnect"}
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            nonlocal status, response_headers
            if message["type"] == "http.response.start":
                status = int(message["status"])
                response_headers = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(bytes(message.get("body", b"")))

        await self.app(scope, receive, send)
        if status == 0:
            msg = "ASGI app ended without a response"
            raise RuntimeError(msg)
        return status, response_headers, b"".join(chunks)

    def _collect_sse(
        self, scope: dict[str, Any], *, max_events: int, timeout: float | None
    ) -> tuple[list[SubscriptionEvent], TrpcTestClientError | None, bool, bytes]:
        """Run an SSE request; return events, error, SSE flag, JSON body."""
        app = self.app

        async def _run() -> tuple[list[SubscriptionEvent], TrpcTestClientError | None, bool, bytes]:
            disconnect = asyncio.Event()
            buffer = bytearray()
            chunks: list[bytes] = []
            events: list[SubscriptionEvent] = []
            error: TrpcTestClientError | None = None
            is_sse = False

            async def receive() -> Message:
                # The app only calls receive() from its disconnect waiter on
                # a GET subscription; signal disconnect once max_events are
                # in or the idle timeout expires, so the app always ends.
                if timeout is None:
                    await disconnect.wait()
                else:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(disconnect.wait(), timeout)
                return {"type": "http.disconnect"}

            async def send(message: Message) -> None:
                nonlocal is_sse, error
                if message["type"] == "http.response.start":
                    headers = message.get("headers", [])
                    is_sse = any(
                        name.lower() == b"content-type" and value.startswith(b"text/event-stream")
                        for name, value in headers
                    )
                    return
                if message["type"] != "http.response.body":
                    return
                body = bytes(message.get("body", b""))
                if not is_sse:
                    chunks.append(body)
                    return
                buffer.extend(body)
                while _SSE_FRAME_END in buffer:
                    frame_end = buffer.index(_SSE_FRAME_END)
                    raw = bytes(buffer[:frame_end])
                    del buffer[: frame_end + len(_SSE_FRAME_END)]
                    fields = _parse_sse_fields(raw)
                    event_name = fields.get("event")
                    if event_name == "serialized-error":
                        error = _error_from_envelope(json.loads(fields.get("data", "{}")))
                    elif event_name is None and "data" in fields:
                        events.append(
                            SubscriptionEvent(
                                data=json.loads(fields["data"]),
                                id=fields.get("id") or None,
                            )
                        )
                    # connected/ping/return/restore-id frames are control.
                if len(events) >= max_events:
                    disconnect.set()

            await app(scope, receive, send)
            return events, error, is_sse, b"".join(chunks)

        return asyncio.run(_run())


def _unwrap(payload: Any) -> Any:
    """Return `result.data` from an envelope; raise on an error envelope."""
    if isinstance(payload, dict):
        envelope = cast("dict[str, Any]", payload)
        error = envelope.get("error")
        if isinstance(error, dict):
            raise _error_from_envelope(cast("dict[str, Any]", error))
        result = envelope.get("result")
        if isinstance(result, dict):
            return cast("dict[str, Any]", result).get("data")
    msg = f"Unexpected tRPC response: {payload!r}"
    raise RuntimeError(msg)


def test_client(
    router: Router,
    *,
    context_factory: ContextFactory | None = None,
    production: bool = False,
    path: str = "/trpc",
    middleware: Iterable[Middleware] = (),
) -> TestClient:
    """Return a sync `TestClient` dispatching against `router` in-process.

    Builds the same ASGI app `mount()` produces — identical parsing,
    validation, context, middleware, and error policy — and drives it with
    in-memory `receive`/`send` callables, so tests exercise the real wire
    path without a socket or a server fixture.

    Args:
        router: The router under test.
        context_factory: Forwarded to `mount()`; called once per request.
        production: Forwarded to `mount()` (`True` enables the production
            error-redaction policy).
        path: Mount prefix; requests go to `{path}/{proc_path}`.
        middleware: Mount-level middleware; forwarded to `mount()`.

    Returns:
        A `TestClient` with `query`/`mutation`/`batch`/`subscribe`.

    Example:
        ```python
        from trpc_rs_py.testing import test_client

        client = test_client(router, context_factory=my_factory)
        assert client.query("me") == {"name": "ada"}
        ```
    """
    app = mount(
        router,
        path=path,
        production=production,
        context_factory=context_factory,
        middleware=middleware,
    )
    return TestClient(app, path=path)


# Keep pytest from collecting this factory as a test when the module is
# imported into a test file.
test_client.__test__ = False  # type: ignore[attr-defined]


def flask_test_client(router: Router, **mount_wsgi_kwargs: Any) -> _WerkzeugClient:
    """Return a werkzeug `Client` wrapping `mount_wsgi(router)`.

    This is the framework's own test client, not the tRPC-aware
    `TestClient`: calls take real URLs and return `werkzeug.wrappers.
    Response` objects carrying the raw JSON envelope — fixture parity with
    a Flask deployment rather than the unwrapped conveniences.

    Args:
        router: The router under test.
        **mount_wsgi_kwargs: Forwarded to `mount_wsgi` (`path`,
            `production`, `context_factory`, `middleware`, ...).

    Returns:
        `werkzeug.test.Client(app, werkzeug.wrappers.Response)`.

    Raises:
        RuntimeError: `werkzeug` is not installed.

    Example:
        ```python
        from trpc_rs_py.testing import flask_test_client

        client = flask_test_client(router)
        response = client.get("/trpc/ping")
        assert response.json == {"result": {"data": "pong"}}
        ```
    """
    try:
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
    except ImportError as error:
        msg = "flask_test_client requires werkzeug (install Flask or werkzeug)"
        raise RuntimeError(msg) from error
    return Client(mount_wsgi(router, **mount_wsgi_kwargs), Response)


def django_test_client(
    router: Router,
    *,
    asynchronous: bool = False,
    prefix: str = "trpc",
    **view_kwargs: Any,
) -> _DjangoClient | _DjangoAsyncClient:
    """Return a Django `Client`/`AsyncClient` routed to `router`.

    Configures Django minimally when it is not configured yet (debug off,
    any host, a synthetic urlconf serving the view under `prefix/`); when
    settings already exist the returned client wraps each request in
    `override_settings(ROOT_URLCONF=...)` pointing at the synthetic
    urlconf, so existing test-suite settings are untouched. Like
    `flask_test_client`, this is the framework's own client: real URLs in,
    raw JSON envelopes out.

    Args:
        router: The router under test.
        asynchronous: Use `trpc_view_async` and return `AsyncClient` when
            `True`; `trpc_view` and `Client` otherwise.
        prefix: URL prefix the procedures are mounted under;
            `/{prefix}/{proc_path}` reaches the router.
        **view_kwargs: Forwarded to `trpc_view`/`trpc_view_async`
            (`production`, `context_factory`, `middleware`, ...).

    Returns:
        `django.test.AsyncClient` when `asynchronous`, else
        `django.test.Client`.

    Raises:
        RuntimeError: `django` is not installed.

    Example:
        ```python
        from trpc_rs_py.testing import django_test_client

        client = django_test_client(router)
        response = client.get("/trpc/ping")
        assert response.json() == {"result": {"data": "pong"}}
        ```
    """
    try:
        import django
        from django.conf import settings
        from django.test import AsyncClient, Client
        from django.test.utils import override_settings
        from django.urls import path as django_path
    except ImportError as error:
        msg = "django_test_client requires Django"
        raise RuntimeError(msg) from error

    from .django import trpc_view, trpc_view_async

    view = (
        trpc_view_async(router, **view_kwargs) if asynchronous else trpc_view(router, **view_kwargs)
    )
    urlconf = types.ModuleType("trpc_rs_py_test_urlconf")
    urlconf.urlpatterns = [  # type: ignore[attr-defined]
        django_path(f"{prefix.strip('/')}/<path:trpc_path>", view)
    ]

    if not settings.configured:
        settings.configure(
            DEBUG=False,
            ALLOWED_HOSTS=["*"],
            ROOT_URLCONF=urlconf,
            SECRET_KEY="trpc-rs-py-testing",
            DATABASES={},
            INSTALLED_APPS=["django.contrib.contenttypes"],
        )
        django.setup()
        return AsyncClient() if asynchronous else Client()

    override = override_settings(ROOT_URLCONF=urlconf)
    raw = AsyncClient() if asynchronous else Client()
    client: Any = raw
    # Patch the instance's request() so each call runs under the synthetic
    # urlconf without touching the caller's configured settings. Instance
    # patching (rather than a local subclass) keeps stubtest happy — it
    # flattens function-local class definitions into the module view.
    client._trpc_urlconf_override = override
    client._trpc_original_request = client.request
    client.request = types.MethodType(
        _trpc_overridden_request_async if asynchronous else _trpc_overridden_request,
        client,
    )
    return raw


def _trpc_overridden_request(self: Any, **request: Any) -> Any:
    """Instance `request()` running under the stored urlconf override."""
    with self._trpc_urlconf_override:
        return self._trpc_original_request(**request)


async def _trpc_overridden_request_async(self: Any, **request: Any) -> Any:
    """Async variant of `_trpc_overridden_request` for `AsyncClient`."""
    with self._trpc_urlconf_override:
        return await self._trpc_original_request(**request)
