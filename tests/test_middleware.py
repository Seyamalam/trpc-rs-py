# Middleware chain tests: mount-level and router-level `Middleware` — order,
# `next(ctx=...)` swaps, short-circuits, error mapping — plus the same chain
# on the REST, subscription, WSGI, and Django paths (ADR 0011).

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient
from werkzeug.test import Client as WsgiClient
from werkzeug.wrappers import Response as WsgiResponse

from trpc_rs_py import (
    RequestContext,
    RestRoute,
    Router,
    TRPCError,
    TRPCErrorCode,
    attach,
    mount,
    mount_rest,
    mount_wsgi,
)
from trpc_rs_py.middleware import Call, Next, logging_middleware, timing_middleware
from trpc_rs_py.router import ProcedureKind


async def _collect_sse(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Drive an ASGI app with a receive that never disconnects."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    await app(scope, receive, send)
    return sent


def _tag(name: str, log: list[str]) -> Any:
    """Return an async middleware appending `name` to `log` around next()."""

    async def layer(call: Call, next: Next) -> Any:
        log.append(f"{name}:before")
        result = await next()
        log.append(f"{name}:after")
        return result

    return layer


async def _sink(scope: Any, receive: Any, send: Any) -> None:
    """Fallback ASGI app answering a fixed JSON body."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b'{"native":true}'})


def _dict_factory(request: RequestContext) -> dict[str, Any]:
    return {"user": "factory-user"}


class TickEvent(BaseModel):
    """One SSE event payload for the subscription tests."""

    n: int
    user: str


class TestChainOrder:
    def test_mount_middleware_wraps_router_chain(self) -> None:
        log: list[str] = []
        router = Router(middleware=[_tag("router", log)])

        @router.query
        def hello() -> str:
            log.append("handler")
            return "hi"

        client = TestClient(mount(router, middleware=[_tag("mount", log)]))
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"
        assert log == [
            "mount:before",
            "router:before",
            "handler",
            "router:after",
            "mount:after",
        ]

    def test_order_within_a_level_is_registration_order(self) -> None:
        log: list[str] = []
        router = Router(middleware=[_tag("first", log), _tag("second", log)])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        client.get("/trpc/hello")
        assert log == ["first:before", "second:before", "second:after", "first:after"]

    def test_call_carries_path_kind_input_and_request_context(self) -> None:
        seen: list[Call] = []

        async def spy(call: Call, next: Next) -> Any:
            seen.append(call)
            return await next()

        router = Router(middleware=[spy])

        @router.query
        def echo(message: str) -> str:
            return message

        @router.mutation
        def create(title: str) -> str:
            return title

        client = TestClient(mount(router))
        client.get('/trpc/echo?input={"message":"hi"}')
        client.post(
            "/trpc/create",
            content=b'{"title":"x"}',
            headers={"content-type": "application/json"},
        )
        assert [(c.path, c.kind, c.input) for c in seen] == [
            ("echo", ProcedureKind.QUERY, {"message": "hi"}),
            ("create", ProcedureKind.MUTATION, {"title": "x"}),
        ]
        assert isinstance(seen[0].request_context, RequestContext)
        assert seen[0].request_context.method == "GET"

    def test_batch_runs_chain_per_call(self) -> None:
        log: list[str] = []
        router = Router(middleware=[_tag("m", log)])

        @router.query
        def ping() -> str:
            return "pong"

        client = TestClient(mount(router))
        response = client.get("/trpc/ping,ping?batch=1&input=%7B%7D")
        assert len(response.json()) == 2
        assert log.count("m:before") == 2


class TestCtxSwap:
    def test_next_ctx_swaps_handler_ctx(self) -> None:
        async def swap(call: Call, next: Next) -> Any:
            return await next(ctx={"user": "middleware-user"})

        router = Router()

        @router.query
        def whoami(ctx) -> str:
            return ctx["user"]

        client = TestClient(mount(router, context_factory=_dict_factory, middleware=[swap]))
        assert client.get("/trpc/whoami").json()["result"]["data"] == "middleware-user"

    def test_next_without_ctx_keeps_factory_ctx(self) -> None:
        async def passthrough(call: Call, next: Next) -> Any:
            return await next()

        router = Router()

        @router.query
        def whoami(ctx) -> str:
            return ctx["user"]

        client = TestClient(mount(router, context_factory=_dict_factory, middleware=[passthrough]))
        assert client.get("/trpc/whoami").json()["result"]["data"] == "factory-user"

    def test_swap_propagates_through_inner_middleware(self) -> None:
        async def outer(call: Call, next: Next) -> Any:
            return await next(ctx={"user": "outer-swapped"})

        async def inner(call: Call, next: Next) -> Any:
            # Inner layers see the ctx the outer layer swapped in.
            return await next(ctx={**call.ctx, "inner": True})

        router = Router(middleware=[outer, inner])

        @router.query
        def whoami(ctx) -> str:
            return f"{ctx['user']}:{ctx['inner']}"

        client = TestClient(mount(router, context_factory=_dict_factory))
        assert client.get("/trpc/whoami").json()["result"]["data"] == "outer-swapped:True"


class TestSyncAndAsync:
    def test_sync_middleware_runs(self) -> None:
        log: list[str] = []

        def sync_middleware(call: Call, next: Next) -> Any:
            log.append(call.path)
            return next()  # awaited by run_middleware

        router = Router(middleware=[sync_middleware])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"
        assert log == ["hello"]

    def test_async_middleware_runs(self) -> None:
        log: list[str] = []

        async def async_middleware(call: Call, next: Next) -> Any:
            await asyncio.sleep(0)
            log.append(call.path)
            return await next()

        router = Router(middleware=[async_middleware])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"
        assert log == ["hello"]


class TestShortCircuit:
    def test_middleware_can_skip_the_handler(self) -> None:
        ran: list[str] = []

        async def short(call: Call, next: Next) -> Any:
            return "shorted"

        router = Router(middleware=[short])

        @router.query
        def hello() -> str:
            ran.append("handler")
            return "hi"

        client = TestClient(mount(router))
        assert client.get("/trpc/hello").json()["result"]["data"] == "shorted"
        assert ran == []

    def test_short_circuit_result_is_output_validated(self) -> None:
        async def wrong_type(call: Call, next: Next) -> Any:
            return {"not": "a string"}

        router = Router(middleware=[wrong_type])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        response = client.get("/trpc/hello")
        assert response.status_code == 500
        assert response.json()["error"]["data"]["code"] == "INTERNAL_SERVER_ERROR"


class TestMiddlewareErrors:
    def test_trpc_error_code_and_message_preserved(self) -> None:
        async def forbidden(call: Call, next: Next) -> Any:
            raise TRPCError("no access", code=TRPCErrorCode.FORBIDDEN)

        router = Router(middleware=[forbidden])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        response = client.get("/trpc/hello")
        assert response.status_code == 403
        error = response.json()["error"]
        assert error["message"] == "no access"
        assert error["data"]["code"] == "FORBIDDEN"

    def test_internal_server_error_redacted_in_production(self) -> None:
        async def internal(call: Call, next: Next) -> Any:
            raise TRPCError("db password leaked", code=TRPCErrorCode.INTERNAL_SERVER_ERROR)

        router = Router(middleware=[internal])

        @router.query
        def hello() -> str:
            return "hi"

        response = TestClient(mount(router, production=True)).get("/trpc/hello")
        assert response.status_code == 500
        assert response.json()["error"]["message"] == "Internal server error"
        assert "db password" not in response.text

    def test_unexpected_error_redaction_policy(self) -> None:
        async def boom(call: Call, next: Next) -> Any:
            raise RuntimeError("secret internals")

        router = Router(middleware=[boom])

        @router.query
        def hello() -> str:
            return "hi"

        dev = TestClient(mount(router)).get("/trpc/hello")
        assert "secret internals" in dev.json()["error"]["message"]
        prod = TestClient(mount(router, production=True)).get("/trpc/hello")
        assert prod.json()["error"]["message"] == "Internal server error"
        assert "secret" not in prod.text

    def test_input_parse_failure_never_reaches_middleware(self) -> None:
        ran: list[str] = []

        async def spy(call: Call, next: Next) -> Any:
            ran.append(call.path)
            return await next()

        router = Router(middleware=[spy])

        @router.query
        def takes_int(n: int) -> int:
            return n

        client = TestClient(mount(router))
        response = client.get('/trpc/takes_int?input={"n":"abc"}')
        assert response.status_code == 400
        assert response.json()["error"]["data"]["code"] == "BAD_REQUEST"
        assert ran == []


class TestRouterSnapshotSemantics:
    def test_use_applies_to_later_registrations_only(self) -> None:
        log: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            log.append(call.path)
            return await next()

        router = Router()

        @router.query
        def early() -> str:
            return "early"

        router.use(tag)

        @router.query
        def late() -> str:
            return "late"

        assert router.procedures["early"].middleware == ()
        assert router.procedures["late"].middleware == (tag,)

        client = TestClient(mount(router))
        client.get("/trpc/early")
        client.get("/trpc/late")
        assert log == ["late"]

    def test_use_usable_as_decorator(self) -> None:
        router = Router()

        @router.use
        async def tagged(call: Call, next: Next) -> Any:
            return await next()

        assert router.middleware == (tagged,)

    def test_constructor_middleware_applies_to_all_registrations(self) -> None:
        log: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            log.append(call.path)
            return await next()

        router = Router(middleware=[tag])

        @router.query
        def first() -> str:
            return "1"

        @router.query
        def second() -> str:
            return "2"

        assert router.procedures["first"].middleware == (tag,)
        assert router.procedures["second"].middleware == (tag,)

        client = TestClient(mount(router))
        client.get("/trpc/first")
        client.get("/trpc/second")
        assert log == ["first", "second"]


class TestMergedAndNestedRouters:
    def test_merge_keeps_each_procedures_chain(self) -> None:
        hits: list[tuple[str, str]] = []

        def tag(name: str) -> Any:
            async def layer(call: Call, next: Next) -> Any:
                hits.append((name, call.path))
                return await next()

            return layer

        router_a = Router(middleware=[tag("a")])

        @router_a.query
        def from_a() -> str:
            return "a"

        router_b = Router(middleware=[tag("b")])

        @router_b.query
        def from_b() -> str:
            return "b"

        router_a.merge(router_b)
        client = TestClient(mount(router_a))
        client.get("/trpc/from_a")
        client.get("/trpc/from_b")
        assert hits == [("a", "from_a"), ("b", "from_b")]

    def test_nest_keeps_the_nested_routers_chain(self) -> None:
        hits: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next()

        inner = Router(middleware=[tag])

        @inner.query
        def leaf() -> str:
            return "leaf"

        app_router = Router()
        app_router.nest("sub", inner)

        client = TestClient(mount(app_router))
        assert client.get("/trpc/sub.leaf").json()["result"]["data"] == "leaf"
        assert hits == ["leaf"]


class TestHelpers:
    def test_logging_middleware_logs_ok(self, caplog: pytest.LogCaptureFixture) -> None:
        router = Router(middleware=[logging_middleware()])

        @router.query
        def ping() -> str:
            return "pong"

        client = TestClient(mount(router))
        with caplog.at_level(logging.INFO, logger="trpc_rs_py.middleware"):
            assert client.get("/trpc/ping").json()["result"]["data"] == "pong"
        messages = [record.getMessage() for record in caplog.records]
        assert any(message.startswith("trpc ping (query) ok in ") for message in messages)

    def test_logging_middleware_logs_failures(self, caplog: pytest.LogCaptureFixture) -> None:
        router = Router(middleware=[logging_middleware()])

        @router.query
        def boom() -> str:
            raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

        client = TestClient(mount(router))
        with caplog.at_level(logging.INFO, logger="trpc_rs_py.middleware"):
            assert client.get("/trpc/boom").status_code == 404
        messages = [record.getMessage() for record in caplog.records]
        assert any("trpc boom (query) failed in " in message for message in messages)

    def test_timing_middleware_reports_to_sink(self) -> None:
        durations: list[float] = []
        router = Router(middleware=[timing_middleware(sink=lambda call, ms: durations.append(ms))])

        @router.query
        def ping() -> str:
            return "pong"

        client = TestClient(mount(router))
        client.get("/trpc/ping")
        assert len(durations) == 1
        assert durations[0] >= 0

    def test_timing_middleware_default_writes_to_dict_ctx(self) -> None:
        ctx_obj: dict[str, Any] = {}
        router = Router(middleware=[timing_middleware()])

        @router.query
        def ping() -> str:
            return "pong"

        client = TestClient(mount(router, context_factory=lambda rc: ctx_obj))
        client.get("/trpc/ping")
        assert ctx_obj["trpc_duration_ms"] >= 0


class TestRestMiddleware:
    def _rest_router(self, middleware: Any = ()) -> Router:
        router = Router(middleware=middleware)

        @router.query(rest=RestRoute("GET", "/hello/{name}"))
        def hello(ctx, name: str) -> str:
            return f"{ctx['via']}:{name}"

        return router

    def test_mount_rest_runs_the_same_chain(self) -> None:
        log: list[tuple[str, ProcedureKind]] = []

        async def tag(call: Call, next: Next) -> Any:
            log.append((call.path, call.kind))
            return await next(ctx={"via": "rest"})

        router = self._rest_router()
        client = TestClient(mount_rest(router, middleware=[tag]))
        assert client.get("/hello/ada").json() == "rest:ada"
        assert log == [("hello", ProcedureKind.QUERY)]

    def test_mount_rest_runs_router_level_chain(self) -> None:
        hits: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next(ctx={"via": "router-level"})

        client = TestClient(mount_rest(self._rest_router(middleware=[tag])))
        assert client.get("/hello/bob").json() == "router-level:bob"
        assert hits == ["hello"]

    def test_attach_runs_middleware_on_trpc_and_rest_paths(self) -> None:
        hits: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next()

        router = Router()

        @router.query(rest=RestRoute("GET", "/hello"))
        def hello() -> str:
            return "hi"

        app = attach(_sink, router, path="/trpc", rest_path="/api", middleware=[tag])
        client = TestClient(app)
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"
        assert client.get("/api/hello").json() == "hi"
        assert client.get("/other").json() == {"native": True}
        assert hits == ["hello", "hello"]


class TestSubscriptionMiddleware:
    def test_chain_runs_once_at_subscription_start(self) -> None:
        log: list[ProcedureKind] = []

        async def tag(call: Call, next: Next) -> Any:
            log.append(call.kind)
            return await next()

        router = Router()

        @router.subscription
        def ticks(count: int = 2) -> AsyncIterator[TickEvent]:
            async def gen() -> AsyncIterator[TickEvent]:
                for i in range(count):
                    yield TickEvent(n=i, user="u")

            return gen()

        app = mount(router, path="", middleware=[tag])
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/ticks",
            "query_string": b"input=%7B%7D",
            "headers": [],
        }
        sent = asyncio.run(_collect_sse(app, scope))
        body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body").decode()
        assert log == [ProcedureKind.SUBSCRIPTION]
        assert '"n":0' in body and '"n":1' in body
        assert "event: return" in body

    def test_ctx_swap_reaches_the_subscription_generator(self) -> None:
        async def swap(call: Call, next: Next) -> Any:
            return await next(ctx={"user": "sub-user"})

        router = Router()

        @router.subscription
        def ticks(ctx) -> AsyncIterator[TickEvent]:
            async def gen() -> AsyncIterator[TickEvent]:
                yield TickEvent(n=0, user=ctx["user"])

            return gen()

        app = mount(router, path="", context_factory=_dict_factory, middleware=[swap])
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/ticks",
            "query_string": b"input=%7B%7D",
            "headers": [],
        }
        sent = asyncio.run(_collect_sse(app, scope))
        body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body").decode()
        assert '"user":"sub-user"' in body


class TestWsgiMiddleware:
    def test_mount_wsgi_forwards_middleware(self) -> None:
        hits: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next()

        router = Router()

        @router.query
        def ping() -> str:
            return "pong"

        client = WsgiClient(mount_wsgi(router, middleware=[tag]), WsgiResponse)
        assert client.get("/trpc/ping").get_json()["result"]["data"] == "pong"
        assert hits == ["ping"]

    def test_attach_flask_forwards_middleware(self) -> None:
        flask = pytest.importorskip("flask")

        hits: list[str] = []

        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next()

        router = Router()

        @router.query
        def ping() -> str:
            return "pong"

        from trpc_rs_py import attach_flask

        app = flask.Flask(__name__)
        attach_flask(app, router, middleware=[tag])
        response = app.test_client().get("/trpc/ping")
        assert response.get_json()["result"]["data"] == "pong"
        assert hits == ["ping"]


class TestDjangoMiddleware:
    def _configure(self) -> None:
        pytest.importorskip("django")
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                DEBUG=True,
                SECRET_KEY="test",
                ALLOWED_HOSTS=["*"],
                ROOT_URLCONF=__name__,
            )
            django.setup()

    def _router(self, hits: list[str]) -> Router:
        async def tag(call: Call, next: Next) -> Any:
            hits.append(call.path)
            return await next()

        router = Router(middleware=[tag])

        @router.query
        def ping() -> str:
            return "pong"

        return router

    def test_trpc_view_forwards_middleware(self) -> None:
        self._configure()
        import types

        from django.test import Client
        from django.test.utils import override_settings
        from django.urls import path as django_path

        from trpc_rs_py.django import trpc_view

        hits: list[str] = []
        urlconf = types.ModuleType("middleware_urlconf")
        urlconf.urlpatterns = [django_path("trpc/<path:trpc_path>", trpc_view(self._router(hits)))]
        with override_settings(ROOT_URLCONF=urlconf):
            response = Client().get("/trpc/ping")
        assert response.json()["result"]["data"] == "pong"
        assert hits == ["ping"]

    def test_urlpatterns_async_forwards_middleware(self) -> None:
        self._configure()
        import types

        from django.test import AsyncClient
        from django.test.utils import override_settings

        from trpc_rs_py.django import urlpatterns as trpc_urlpatterns

        hits: list[str] = []
        urlconf = types.ModuleType("middleware_async_urlconf")
        urlconf.urlpatterns = trpc_urlpatterns(self._router(hits), asynchronous=True)

        async def run() -> Any:
            with override_settings(ROOT_URLCONF=urlconf):
                return await AsyncClient().get("/trpc/ping")

        response = asyncio.run(run())
        assert response.json()["result"]["data"] == "pong"
        assert hits == ["ping"]
