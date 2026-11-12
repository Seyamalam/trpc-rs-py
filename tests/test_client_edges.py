"""Edge-path tests for `trpc_rs_py.client` — sync and async clients.

`test_client.py` covers the happy-path wire contract against a real mounted
router; these tests exercise the failure and edge branches against a
hand-written ASGI app serving non-envelope bodies, malformed batch
payloads, and unusual SSE frames — plus the client-side branches that need
no server (`close()` mid-stream, `timeout=None`, `last-event-id` header,
missing httpx).
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn

from trpc_rs_py.client import (
    SubscriptionEvent,
    TrpcClientError,
    _subscription_request,  # pyright: ignore[reportPrivateUsage]
    create_async_client,
    create_client,
)

try:
    import httpx  # noqa: F401

    HAS_HTTPX = True
except ImportError:  # pragma: no cover - httpx is a dev dependency
    HAS_HTTPX = False

needs_httpx = pytest.mark.skipif(not HAS_HTTPX, reason="httpx is not installed")


async def _edge_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI app serving deliberately off-contract tRPC responses."""
    assert scope["type"] == "http"
    path = scope["path"]

    async def respond(status: int, body: bytes, content_type: bytes = b"application/json") -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", content_type)],
            }
        )
        await send({"type": "http.response.body", "body": body})

    if path == "/trpc/ok":
        await respond(200, b'{"result":{"data":"ok"}}')
    elif path == "/trpc/err200":
        # A 200 response still carrying a tRPC error envelope.
        await respond(
            200,
            json.dumps(
                {
                    "error": {
                        "message": "denied",
                        "code": -32003,
                        "data": {"code": "FORBIDDEN", "httpStatus": 403},
                    }
                }
            ).encode(),
        )
    elif path == "/trpc/errnodata":
        # Error envelope with no `data` member — code lives on the envelope.
        await respond(200, b'{"error":{"message":"flat","code":-32000}}')
    elif path == "/trpc/weird":
        # A 200 body that is neither a result nor an error envelope.
        await respond(200, b'{"unexpected": true}')
    elif path == "/trpc/text500":
        await respond(500, b"broke", content_type=b"text/plain")
    elif path == "/trpc/json500":
        # JSON error body without a tRPC envelope member.
        await respond(500, b'{"no": "envelope"}')
    elif path == "/trpc/missing":
        await respond(
            404,
            b'{"error":{"message":"gone","code":-32004,'
            b'"data":{"code":"NOT_FOUND","httpStatus":404}}}',
        )
    elif path == "/trpc/plain":
        await respond(200, b"hello", content_type=b"text/plain")
    elif path == "/trpc/batchbad":
        # A batch list with a non-envelope entry.
        await respond(200, b'[{"result":{"data":"fine"}}, 42]')
    elif path == "/trpc/badjson":
        await respond(200, b"not json at all")
    elif path == "/trpc/batch500":
        await respond(500, b"junk", content_type=b"text/plain")
    elif path == "/trpc/batcherr":
        # A batch that fails before dispatch: dict error, not a list.
        await respond(
            200,
            b'{"error":{"message":"mixed","code":-32600,'
            b'"data":{"code":"BAD_REQUEST","httpStatus":400}}}',
        )
    elif path == "/trpc/partial":
        # Stream ending mid-frame: no trailing newline before EOF.
        await respond(200, b'data: {"a": 1}', content_type=b"text/event-stream")
    elif path == "/trpc/comments":
        body = (
            b": keepalive\n"  # comment line — ignored
            b"nocolon\n"  # field name without a colon — ignored
            b"event: ping\n\n"  # control frame — consumed silently
            b'data: {"k": 1}\nid: 42\n\n'
            b"event: return\ndata: \n\n"
        )
        await respond(200, body, content_type=b"text/event-stream")
    elif path == "/trpc/streamforever":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'event: connected\ndata: {}\n\n\ndata: {"n": 0}\nid: 0\n\n\n',
                "more_body": True,
            }
        )
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
    else:
        await respond(
            404,
            b'{"error":{"message":"nope","code":-32004,'
            b'"data":{"code":"NOT_FOUND","httpStatus":404}}}',
        )


@pytest.fixture(scope="module")
def edge_url() -> Iterator[str]:
    """Serve `_edge_app` under uvicorn on an ephemeral localhost port."""
    config = uvicorn.Config(_edge_app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn server did not start"
            time.sleep(0.02)
        socket = server.servers[0].sockets[0]
        port = socket.getsockname()[1]
        yield f"http://127.0.0.1:{port}/trpc"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


# -- sync Client -------------------------------------------------------------


def test_sync_query_envelope_and_shape_errors(edge_url: str) -> None:
    """200-status error envelopes raise; shapeless bodies raise RuntimeError."""
    with create_client(edge_url) as client:
        with pytest.raises(TrpcClientError) as raised:
            client.query("err200")
        assert raised.value.code == "FORBIDDEN"
        assert raised.value.http_status == 403

        with pytest.raises(TrpcClientError) as raised:
            client.query("errnodata")
        error = raised.value
        assert error.code == "-32000"  # code falls back to envelope["code"]
        assert error.data is None
        assert error.http_status is None
        assert str(error) == "-32000: flat"

        with pytest.raises(RuntimeError, match="Unexpected tRPC response"):
            client.query("weird")


def test_sync_query_http_errors_without_envelope(edge_url: str) -> None:
    """Non-envelope HTTP errors become UNKNOWN errors carrying the status."""
    with create_client(edge_url) as client:
        with pytest.raises(TrpcClientError) as raised:
            client.query("text500")
        assert raised.value.code == "UNKNOWN"
        assert raised.value.http_status == 500
        assert "HTTP 500" in raised.value.message

        with pytest.raises(TrpcClientError) as raised:
            client.query("json500")
        assert raised.value.code == "UNKNOWN"
        assert "HTTP 500" in raised.value.message


def test_sync_batch_edge_responses(edge_url: str) -> None:
    """Malformed entries, undecodable bodies, and bare HTTP errors."""
    with create_client(edge_url) as client:
        with pytest.raises(RuntimeError, match="Unexpected batch entry"):
            client.batch([{"path": "batchbad"}])
        with pytest.raises(RuntimeError, match="Unexpected batch response"):
            client.batch([{"path": "badjson"}])
        with pytest.raises(TrpcClientError) as raised:
            client.batch([{"path": "batch500"}])
        assert raised.value.code == "UNKNOWN"
        assert raised.value.http_status == 500


def test_sync_subscribe_non_sse_and_frame_edges(edge_url: str) -> None:
    """Error envelopes on subscribe raise; comments/ids/partial lines parse."""
    with create_client(edge_url) as client:
        with pytest.raises(TrpcClientError) as raised:
            list(client.subscribe("err200"))
        assert raised.value.code == "FORBIDDEN"

        with pytest.raises(RuntimeError, match="did not produce an SSE stream"):
            list(client.subscribe("plain"))

        # A stream ending without a trailing newline just ends.
        assert list(client.subscribe("partial")) == []

        # Comments and colon-less fields are ignored; `id:` attaches to the
        # data frame; `return` ends iteration cleanly.
        assert list(client.subscribe("comments")) == [SubscriptionEvent({"k": 1}, id="42")]


def test_sync_close_aborts_open_stream(edge_url: str) -> None:
    """`close()` releases in-flight subscription streams."""
    client = create_client(edge_url)
    stream = client.subscribe("streamforever")
    assert next(stream) == SubscriptionEvent({"n": 0}, id="0")
    client.close()
    stream.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.query("ok")


def test_sync_timeout_none_and_header_resume(edge_url: str) -> None:
    """`timeout=None` skips the timeout kwarg; non-dict input uses the header."""
    with create_client(edge_url, timeout=None) as client:
        assert client.query("ok") == "ok"

    assert _subscription_request(5, "ev-9") == (5, {"last-event-id": "ev-9"})
    assert _subscription_request(None, "ev-1") == ({"lastEventId": "ev-1"}, {})
    assert _subscription_request({"a": 1}, "ev-2") == (
        {"a": 1, "lastEventId": "ev-2"},
        {},
    )


# -- async AsyncClient ---------------------------------------------------------


@needs_httpx
def test_async_query_and_mutation_errors(edge_url: str) -> None:
    """Async query/mutation map envelopes and bare HTTP errors alike."""

    async def main() -> None:
        async with create_async_client(edge_url) as client:
            with pytest.raises(TrpcClientError) as raised:
                await client.query("missing")
            assert raised.value.code == "NOT_FOUND"
            assert raised.value.http_status == 404

            with pytest.raises(TrpcClientError) as raised:
                await client.query("text500")
            assert raised.value.code == "UNKNOWN"
            assert "HTTP 500" in raised.value.message

            with pytest.raises(TrpcClientError) as raised:
                await client.mutation("text500", {"x": 1})
            assert raised.value.code == "UNKNOWN"
            assert raised.value.http_status == 500

    asyncio.run(main())


@needs_httpx
def test_async_batch_edge_responses(edge_url: str) -> None:
    """Async batch: top-level error envelope, HTTP error, unexpected body."""

    async def main() -> None:
        async with create_async_client(edge_url) as client:
            with pytest.raises(TrpcClientError) as raised:
                await client.batch([{"path": "batcherr"}])
            assert raised.value.code == "BAD_REQUEST"
            assert raised.value.http_status == 400

            with pytest.raises(TrpcClientError) as raised:
                await client.batch([{"path": "batch500"}])
            assert raised.value.code == "UNKNOWN"
            assert raised.value.http_status == 500

            with pytest.raises(RuntimeError, match="Unexpected batch response"):
                await client.batch([{"path": "badjson"}])

    asyncio.run(main())


@needs_httpx
def test_async_subscribe_edge_responses(edge_url: str) -> None:
    """Async subscribe: HTTP error, error envelope, and non-SSE bodies."""

    async def main() -> None:
        async with create_async_client(edge_url) as client:
            with pytest.raises(TrpcClientError) as raised:
                _ = [event async for event in client.subscribe("missing")]
            assert raised.value.code == "NOT_FOUND"

            with pytest.raises(TrpcClientError) as raised:
                _ = [event async for event in client.subscribe("err200")]
            assert raised.value.code == "FORBIDDEN"

            with pytest.raises(RuntimeError, match="did not produce an SSE stream"):
                _ = [event async for event in client.subscribe("plain")]

    asyncio.run(main())


@needs_httpx
def test_async_closed_client_rejects_every_call(edge_url: str) -> None:
    """Every `AsyncClient` operation refuses once closed."""

    async def main() -> None:
        client = create_async_client(edge_url)
        await client.close()
        with pytest.raises(RuntimeError, match="closed"):
            await client.query("ok")
        with pytest.raises(RuntimeError, match="closed"):
            await client.mutation("ok")
        with pytest.raises(RuntimeError, match="closed"):
            await client.batch([{"path": "ok"}])
        with pytest.raises(RuntimeError, match="closed"):
            _ = [event async for event in client.subscribe("ok")]

    asyncio.run(main())


def test_create_async_client_without_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing httpx fails the factory with the install hint."""
    monkeypatch.setitem(sys.modules, "httpx", None)
    with pytest.raises(RuntimeError, match="httpx"):
        create_async_client("http://127.0.0.1:1/trpc")
