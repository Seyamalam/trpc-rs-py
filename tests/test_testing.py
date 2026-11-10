# Test-client tests: `trpc_rs_py.testing` — the sync in-process TestClient
# (query/mutation/batch/subscribe), context_factory, production redaction,
# and the werkzeug/Django framework-client wrappers (ADR 0011).

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from trpc_rs_py import RequestContext, Router, tracked
from trpc_rs_py.errors import TRPCError, TRPCErrorCode
from trpc_rs_py.streaming import Tracked
from trpc_rs_py.testing import (
    SubscriptionEvent,
    TestClient,
    TrpcTestClientError,
    django_test_client,
    flask_test_client,
    test_client,
)


class Tick(BaseModel):
    """One SSE event payload for the subscription tests."""

    n: int


def make_router() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query
    def echo(message: str) -> str:
        return f"echo: {message}"

    @router.query
    def me(ctx) -> dict:
        return {"user": ctx.user, "method": ctx.method}

    @router.query
    def boom() -> str:
        raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

    @router.query
    def explode() -> str:
        raise RuntimeError("secret internals")

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title, "created": True}

    @router.subscription
    def ticks(count: int = 3) -> AsyncIterator[Tracked[Tick]]:
        async def gen() -> AsyncIterator[Tracked[Tick]]:
            for index in range(count):
                yield tracked(str(index), Tick(n=index))

        return gen()

    @router.subscription
    async def endless() -> AsyncIterator[int]:
        index = 0
        while True:
            yield index
            index += 1

    @router.subscription
    async def stuck() -> AsyncIterator[int]:
        await asyncio.sleep(3600)
        yield 0  # pragma: no cover - unreachable

    @router.subscription
    async def guarded() -> AsyncIterator[int]:
        raise TRPCError("no access", code=TRPCErrorCode.UNAUTHORIZED)
        yield 0  # pragma: no cover - unreachable

    return router


def user_factory(request: RequestContext) -> RequestContext:
    request.user = request.headers.get("x-user", "anon")
    return request


# --- query/mutation/batch ---------------------------------------------------


def test_query_returns_result_data() -> None:
    client = test_client(make_router())
    assert client.query("ping") == "pong"


def test_query_with_input() -> None:
    client = test_client(make_router())
    assert client.query("echo", {"message": "hi"}) == "echo: hi"


def test_query_url_encodes_input() -> None:
    client = test_client(make_router())
    assert client.query("echo", {"message": "a b&c"}) == "echo: a b&c"
    query = client.requests[-1]["query_string"]
    assert query.startswith("input=%7B")
    assert "%26" in query  # the literal `&` in the message is escaped


def test_query_error_raises_trpc_test_client_error() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.query("boom")
    error = excinfo.value
    assert error.code == "NOT_FOUND"
    assert error.message == "nope"
    assert error.data["path"] == "boom"
    assert error.http_status == 404


def test_unknown_procedure_raises_not_found() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.query("missing")
    assert excinfo.value.code == "NOT_FOUND"


def test_mutation_posts_json() -> None:
    client = test_client(make_router())
    assert client.mutation("create", {"title": "hello"}) == {
        "title": "hello",
        "created": True,
    }


def test_mutation_without_input() -> None:
    router = Router()

    @router.mutation
    def noop() -> str:
        return "done"

    assert test_client(router).mutation("noop") == "done"


def test_batch_returns_per_call_results() -> None:
    client = test_client(make_router())
    results = client.batch(
        [
            {"path": "ping"},
            {"path": "echo", "input": {"message": "two"}},
        ]
    )
    assert results == ["pong", "echo: two"]


def test_batch_returns_error_instances_per_call() -> None:
    client = test_client(make_router())
    results = client.batch([{"path": "ping"}, {"path": "boom"}])
    assert results[0] == "pong"
    assert isinstance(results[1], TrpcTestClientError)
    assert results[1].code == "NOT_FOUND"


def test_batch_records_one_request() -> None:
    client = test_client(make_router())
    client.batch([{"path": "ping"}, {"path": "ping"}])
    assert len(client.requests) == 1
    assert client.requests[0]["path"] == "/trpc/ping,ping"
    assert client.requests[0]["query_string"].startswith("batch=1")


def test_mixed_kind_batch_raises_top_level_error() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.batch([{"path": "ping"}, {"path": "create"}])
    assert excinfo.value.code == "BAD_REQUEST"


# --- subscriptions ----------------------------------------------------------


def test_subscribe_collects_tracked_events() -> None:
    client = test_client(make_router())
    events = client.subscribe("ticks", {"count": 2})
    assert events == [
        SubscriptionEvent(data={"n": 0}, id="0"),
        SubscriptionEvent(data={"n": 1}, id="1"),
    ]


def test_subscribe_untracked_events_have_no_id() -> None:
    router = Router()

    @router.subscription
    async def plain() -> AsyncIterator[int]:
        yield 1
        yield 2

    events = test_client(router).subscribe("plain")
    assert [event.data for event in events] == [1, 2]
    assert [event.id for event in events] == [None, None]


def test_subscribe_caps_never_ending_stream() -> None:
    client = test_client(make_router())
    start = time.monotonic()
    events = client.subscribe("endless", max_events=5, timeout=10.0)
    elapsed = time.monotonic() - start
    assert [event.data for event in events] == [0, 1, 2, 3, 4]
    assert elapsed < 10.0


def test_subscribe_blocked_generator_disconnects_on_timeout() -> None:
    """A generator that never yields cannot hang the client."""
    client = test_client(make_router())
    start = time.monotonic()
    events = client.subscribe("stuck", timeout=0.2)
    assert events == []
    assert time.monotonic() - start < 10.0


def test_subscribe_serialized_error_raises() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.subscribe("guarded")
    assert excinfo.value.code == "UNAUTHORIZED"
    assert excinfo.value.message == "no access"


def test_subscribe_last_event_id_header() -> None:
    router = Router()
    seen: list[str | None] = []

    @router.subscription
    async def replay(lastEventId: str | None = None) -> AsyncIterator[int]:
        seen.append(lastEventId)
        yield 1

    test_client(router).subscribe("replay", last_event_id="7")
    assert seen == ["7"]


def test_subscribe_non_subscription_path() -> None:
    client = test_client(make_router())
    with pytest.raises(RuntimeError, match="did not produce an SSE stream"):
        client.subscribe("ping")


def test_subscribe_error_envelope_on_non_sse_response() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.subscribe("boom")
    assert excinfo.value.code == "NOT_FOUND"


# --- context / production / recording ---------------------------------------


def test_context_factory_ctx_reaches_handler() -> None:
    client = test_client(make_router(), context_factory=user_factory)
    assert client.query("me", headers={"x-user": "ada"}) == {
        "user": "ada",
        "method": "GET",
    }


def test_default_ctx_is_request_context() -> None:
    router = Router()

    @router.query
    def ip(ctx) -> str:
        return ctx.client[0]

    assert test_client(router).query("ip") == "trpc-test-client"


def test_production_redacts_internal_errors() -> None:
    client = test_client(make_router(), production=True)
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.query("explode")
    assert excinfo.value.message == "Internal server error"
    assert excinfo.value.code == "INTERNAL_SERVER_ERROR"


def test_development_leaks_internal_errors() -> None:
    client = test_client(make_router())
    with pytest.raises(TrpcTestClientError) as excinfo:
        client.query("explode")
    assert "secret internals" in excinfo.value.message


def test_requests_records_scope_details() -> None:
    client = test_client(make_router())
    client.query("ping")
    client.mutation("create", {"title": "x"})
    assert client.requests == [
        {"method": "GET", "path": "/trpc/ping", "query_string": ""},
        {"method": "POST", "path": "/trpc/create", "query_string": ""},
    ]


def test_app_attribute_is_the_asgi_app() -> None:
    client = test_client(make_router())
    assert callable(client.app)


def test_custom_mount_path() -> None:
    client = test_client(make_router(), path="/api/trpc")
    assert client.query("ping") == "pong"
    assert client.requests[0]["path"] == "/api/trpc/ping"


def test_client_type_exposed() -> None:
    assert isinstance(test_client(make_router()), TestClient)


# --- framework wrappers ------------------------------------------------------


def test_flask_test_client() -> None:
    client = flask_test_client(make_router())
    response = client.get("/trpc/ping")
    assert response.status_code == 200
    assert response.json == {"result": {"data": "pong"}}


def test_flask_test_client_honors_mount_kwargs() -> None:
    client = flask_test_client(make_router(), path="/api/trpc")
    response = client.get("/api/trpc/ping")
    assert response.json == {"result": {"data": "pong"}}


def test_django_test_client_sync() -> None:
    pytest.importorskip("django")
    client = django_test_client(make_router())
    response = client.get("/trpc/ping")
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "pong"}}


def test_django_test_client_sync_with_input() -> None:
    pytest.importorskip("django")
    client = django_test_client(make_router())
    response = client.get("/trpc/echo", {"input": '{"message":"hi"}'})
    assert response.json() == {"result": {"data": "echo: hi"}}


def test_django_test_client_async() -> None:
    pytest.importorskip("django")

    async def run() -> None:
        client = django_test_client(make_router(), asynchronous=True, prefix="atrpc")
        response = await client.get("/atrpc/ping")
        assert response.status_code == 200
        assert response.json() == {"result": {"data": "pong"}}

    asyncio.run(run())


def test_django_test_client_async_mutation() -> None:
    pytest.importorskip("django")

    async def run() -> None:
        client = django_test_client(make_router(), asynchronous=True)
        response = await client.post(
            "/trpc/create",
            data='{"title":"x"}',
            content_type="application/json",
        )
        assert response.json() == {"result": {"data": {"title": "x", "created": True}}}

    asyncio.run(run())


def test_any_router_accepted() -> None:
    """The wrappers take the same `Router` `mount()` takes."""
    client: Any = test_client(Router())
    assert isinstance(client, TestClient)
