"""Tests for `trpc_rs_py.client` — the real-network sync/async HTTP client.

A module-scoped uvicorn server hosts `mount(router)` on an ephemeral port;
both `Client` (stdlib urllib) and `AsyncClient` (lazy httpx) exercise the
full tRPC v11 wire contract over a real socket — including SSE
subscriptions, which Starlette's TestClient cannot stream faithfully.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import uvicorn

from trpc_rs_py import RequestContext, Router, mount, tracked
from trpc_rs_py.client import (
    SubscriptionEvent,
    TrpcClientError,
    create_async_client,
    create_client,
)
from trpc_rs_py.errors import TRPCError, TRPCErrorCode

try:
    import httpx  # noqa: F401

    HAS_HTTPX = True
except ImportError:  # pragma: no cover - httpx is a dev dependency
    HAS_HTTPX = False

needs_httpx = pytest.mark.skipif(not HAS_HTTPX, reason="httpx is not installed")


def _build_router() -> Router:
    """Router exercising every client path: errors, ctx, streams, tracked."""
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query
    def echo(message: str) -> dict[str, Any]:
        return {"echo": message}

    @router.query
    def missing() -> str:
        raise TRPCError("nothing here", code=TRPCErrorCode.NOT_FOUND)

    @router.query
    def whoami(ctx) -> dict[str, Any]:
        return {"user": ctx.user}

    @router.mutation
    def create(title: str, priority: int = 0) -> dict[str, Any]:
        return {"title": title, "priority": priority, "created": True}

    @router.mutation
    def fail_mutation() -> dict[str, Any]:
        raise TRPCError("bad input", code=TRPCErrorCode.BAD_REQUEST)

    @router.subscription
    def ticks(count: int = 3) -> AsyncIterator[dict[str, Any]]:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            for index in range(count):
                yield tracked(f"tick-{index}", {"index": index})

        return stream()

    @router.subscription
    def guarded() -> AsyncIterator[int]:
        async def stream() -> AsyncIterator[int]:
            raise TRPCError("no access", code=TRPCErrorCode.FORBIDDEN)
            yield 0  # pragma: no cover - unreachable

        return stream()

    @router.subscription
    def resume_from(lastEventId: str | None = None) -> AsyncIterator[dict[str, Any]]:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            start = int(lastEventId.split("-")[1]) + 1 if lastEventId else 0
            for index in range(start, 5):
                yield tracked(f"ev-{index}", {"index": index})

        return stream()

    return router


def _context_factory(request: RequestContext) -> RequestContext:
    """Mirror the request user header into ctx.user for header tests."""
    request.user = request.headers.get("x-user", "anon")
    return request


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    """Run `mount(router)` under uvicorn on an ephemeral localhost port."""
    app = mount(_build_router(), context_factory=_context_factory)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
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


def test_query_returns_result_data(server_url: str) -> None:
    """`query` unwraps `{result: {data: ...}}` and returns the payload."""
    with create_client(server_url) as client:
        assert client.query("ping") == "pong"
        assert client.query("echo", {"message": "hi"}) == {"echo": "hi"}


def test_mutation_posts_json_body(server_url: str) -> None:
    """`mutation` POSTs the input as the JSON body."""
    with create_client(server_url) as client:
        assert client.mutation("create", {"title": "hello"}) == {
            "title": "hello",
            "priority": 0,
            "created": True,
        }
        assert client.mutation("create", {"title": "x", "priority": 2})["priority"] == 2


def test_error_envelope_raises_trpc_client_error(server_url: str) -> None:
    """Error envelopes surface code/message/data/http_status."""
    with create_client(server_url) as client, pytest.raises(TrpcClientError) as raised:
        client.query("missing")
    error = raised.value
    assert error.code == "NOT_FOUND"
    assert error.message == "nothing here"
    assert error.http_status == 404
    assert error.data["code"] == "NOT_FOUND"
    assert error.data["path"] == "missing"
    assert error.data["httpStatus"] == 404


def test_unknown_path_raises_not_found(server_url: str) -> None:
    """Unknown procedure paths map to NOT_FOUND errors, not transport errors."""
    with create_client(server_url) as client, pytest.raises(TrpcClientError) as raised:
        client.query("no.such.procedure")
    assert raised.value.code == "NOT_FOUND"


def test_batch_returns_per_call_results(server_url: str) -> None:
    """`batch` sends one `?batch=1` request and returns ordered results."""
    with create_client(server_url) as client:
        results = client.batch(
            [
                {"path": "ping"},
                {"path": "echo", "input": {"message": "a"}},
                {"path": "missing"},
                {"path": "echo", "input": {"message": "b"}},
            ]
        )
    assert results[0] == "pong"
    assert results[1] == {"echo": "a"}
    assert isinstance(results[2], TrpcClientError)
    assert results[2].code == "NOT_FOUND"  # type: ignore[union-attr]
    assert results[3] == {"echo": "b"}


def test_batch_with_mutation_raises(server_url: str) -> None:
    """GET batches cannot carry mutations: the server rejects the whole call."""
    with create_client(server_url) as client, pytest.raises(TrpcClientError) as raised:
        client.batch([{"path": "ping"}, {"path": "create", "input": {"title": "x"}}])
    assert raised.value.code in {"METHOD_NOT_SUPPORTED", "BAD_REQUEST", "NOT_FOUND"}


def test_default_headers_reach_context(server_url: str) -> None:
    """Constructor `headers=` are sent on every request."""
    with create_client(server_url, headers={"x-user": "devin"}) as client:
        assert client.query("whoami") == {"user": "devin"}


def test_subscribe_yields_tracked_events(server_url: str) -> None:
    """SSE data frames become `SubscriptionEvent`s carrying their `id:`."""
    with create_client(server_url) as client:
        events = list(client.subscribe("ticks", {"count": 3}))
    assert events == [
        SubscriptionEvent({"index": 0}, id="tick-0"),
        SubscriptionEvent({"index": 1}, id="tick-1"),
        SubscriptionEvent({"index": 2}, id="tick-2"),
    ]


def test_subscribe_serialized_error_raises(server_url: str) -> None:
    """A `serialized-error` SSE frame raises `TrpcClientError` mid-stream."""
    with create_client(server_url) as client, pytest.raises(TrpcClientError) as raised:
        list(client.subscribe("guarded"))
    assert raised.value.code == "FORBIDDEN"
    assert raised.value.message == "no access"


def test_subscribe_resume_sends_last_event_id(server_url: str) -> None:
    """`last_event_id` merges into input per the official client contract."""
    with create_client(server_url) as client:
        events = list(client.subscribe("resume_from", last_event_id="ev-1"))
    assert events == [
        SubscriptionEvent({"index": 2}, id="ev-2"),
        SubscriptionEvent({"index": 3}, id="ev-3"),
        SubscriptionEvent({"index": 4}, id="ev-4"),
    ]


def test_subscribe_resume_with_explicit_input(server_url: str) -> None:
    """Resume also flows when the caller passes a base input dict."""
    with create_client(server_url) as client:
        events = list(client.subscribe("resume_from", {}, last_event_id="ev-3"))
    assert events == [SubscriptionEvent({"index": 4}, id="ev-4")]


def test_subscribe_on_query_raises_runtime_error(server_url: str) -> None:
    """Subscribing a non-subscription raises a descriptive RuntimeError."""
    with (
        create_client(server_url) as client,
        pytest.raises(RuntimeError, match="did not produce an SSE stream"),
    ):
        list(client.subscribe("ping"))


def test_close_rejects_further_calls(server_url: str) -> None:
    """`close()` marks the client unusable."""
    client = create_client(server_url)
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.query("ping")


def test_subscribe_unknown_path_raises_not_found(server_url: str) -> None:
    """An HTTP error status on subscribe becomes a `TrpcClientError`."""
    with create_client(server_url) as client, pytest.raises(TrpcClientError) as raised:
        list(client.subscribe("no.such.subscription"))
    assert raised.value.code == "NOT_FOUND"


@needs_httpx
def test_async_query_mutation_batch(server_url: str) -> None:
    """`AsyncClient` mirrors the sync wire for queries/mutations/batches."""

    async def main() -> None:
        async with create_async_client(server_url) as client:
            assert await client.query("ping") == "pong"
            assert await client.mutation("create", {"title": "async"}) == {
                "title": "async",
                "priority": 0,
                "created": True,
            }
            results = await client.batch([{"path": "ping"}, {"path": "missing"}])
            assert results[0] == "pong"
            assert isinstance(results[1], TrpcClientError)

    asyncio.run(main())


@needs_httpx
def test_async_subscribe_tracked_and_error(server_url: str) -> None:
    """Async SSE iteration yields events and raises on serialized-error."""

    async def main() -> None:
        async with create_async_client(server_url) as client:
            events = [event async for event in client.subscribe("ticks", {"count": 2})]
            assert events == [
                SubscriptionEvent({"index": 0}, id="tick-0"),
                SubscriptionEvent({"index": 1}, id="tick-1"),
            ]
            with pytest.raises(TrpcClientError) as raised:
                _ = [event async for event in client.subscribe("guarded")]
            assert raised.value.code == "FORBIDDEN"
            with pytest.raises(TrpcClientError) as raised:
                _ = [event async for event in client.subscribe("no.such.subscription")]
            assert raised.value.code == "NOT_FOUND"

    asyncio.run(main())


@needs_httpx
def test_async_resume_and_headers(server_url: str) -> None:
    """Async client honors last_event_id and default headers."""

    async def main() -> None:
        async with create_async_client(server_url, headers={"x-user": "bot"}) as client:
            assert await client.query("whoami") == {"user": "bot"}
            events = [
                event async for event in client.subscribe("resume_from", last_event_id="ev-3")
            ]
            assert events == [SubscriptionEvent({"index": 4}, id="ev-4")]

    asyncio.run(main())
