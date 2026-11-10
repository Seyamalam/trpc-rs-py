# Per-request context tests: `ctx` injection, `context_factory`, and
# `RequestContext` across the ASGI, WSGI, Django, and REST dispatch paths
# (ADR 0011).

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient

from trpc_rs_py import (
    RequestContext,
    RestRoute,
    Router,
    generate_openapi,
    generate_types,
    generate_zod,
    mount,
    mount_rest,
    mount_wsgi,
)
from trpc_rs_py.asgi import _call_handler, _input_fits_signature
from trpc_rs_py.context import (
    ctx_injection,
    inject_ctx,
    request_context_from_scope,
    strip_ctx_parameter,
)
from trpc_rs_py.router import Procedure, ProcedureKind
from trpc_rs_py.streaming import Tracked, tracked


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


class TestCtxInjectionDetection:
    def test_first_positional_ctx_prepends(self) -> None:
        def f(ctx, name: str) -> str:
            return name

        assert ctx_injection(f) == "prepend"

    def test_positional_only_ctx_prepends(self) -> None:
        def f(ctx, /, name: str) -> str:
            return name

        assert ctx_injection(f) == "prepend"

    def test_keyword_only_ctx_uses_kwarg(self) -> None:
        def f(*, ctx) -> None:
            pass

        assert ctx_injection(f) == "kwarg"

    def test_no_ctx(self) -> None:
        def f(name: str) -> str:
            return name

        assert ctx_injection(f) is None

    def test_non_first_ctx_is_ordinary_input(self) -> None:
        def f(name: str, ctx: str) -> str:
            return name + ctx

        assert ctx_injection(f) is None

    def test_ctx_after_varargs_is_ordinary_input(self) -> None:
        def f(*args: str, ctx: str) -> str:
            return ",".join(args) + ctx

        assert ctx_injection(f) is None

    def test_uninspectable_handler(self) -> None:
        assert ctx_injection(len) is None


class TestInjectCtx:
    def test_prepend(self) -> None:
        assert inject_ctx("prepend", (1,), {"a": 2}, "CTX") == (("CTX", 1), {"a": 2})

    def test_kwarg(self) -> None:
        assert inject_ctx("kwarg", (1,), {"a": 2}, "CTX") == ((1,), {"a": 2, "ctx": "CTX"})

    def test_none_passthrough(self) -> None:
        assert inject_ctx(None, (1,), {"a": 2}, "CTX") == ((1,), {"a": 2})


class TestStripCtxParameter:
    def test_strips_leading_ctx(self) -> None:
        def f(ctx, name: str) -> str:
            return name

        stripped = strip_ctx_parameter(inspect.signature(f))
        assert stripped is not None
        assert list(stripped.parameters) == ["name"]

    def test_leaves_other_first_params(self) -> None:
        def f(name: str, ctx: str) -> str:
            return name

        stripped = strip_ctx_parameter(inspect.signature(f))
        assert stripped is not None
        assert list(stripped.parameters) == ["name", "ctx"]

    def test_none(self) -> None:
        assert strip_ctx_parameter(None) is None


class TestRequestContextFromScope:
    def test_populates_from_scope(self) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/trpc/greet",
            "query_string": b"a=1&b=2",
            "headers": [
                (b"X-User", b"ada"),
                (b"accept", b"application/json"),
                (b"accept", b"text/plain"),
            ],
            "client": ("10.0.0.1", 4321),
        }
        ctx = request_context_from_scope(scope)
        assert ctx.method == "POST"
        assert ctx.path == "/trpc/greet"
        assert ctx.query_string == "a=1&b=2"
        # First occurrence wins for the mapping; the raw list keeps all.
        assert ctx.headers["accept"] == "application/json"
        assert ctx.headers["x-user"] == "ada"
        assert ctx.raw_headers[1:] == (("accept", "application/json"), ("accept", "text/plain"))
        assert ctx.client == ("10.0.0.1", 4321)
        assert ctx.request is scope
        assert ctx.user is None

    def test_framework_request_overrides_scope(self) -> None:
        environ = {"REQUEST_METHOD": "GET"}
        scope = {"type": "http", "method": "GET", "trpc.request": environ}
        assert request_context_from_scope(scope).request is environ

    def test_missing_pieces_default(self) -> None:
        ctx = request_context_from_scope({"type": "http", "method": "GET"})
        assert ctx.client is None
        assert ctx.headers == {}
        assert ctx.query_string == ""


def make_router() -> Router:
    router = Router()

    @router.query
    def me(ctx) -> dict:
        return {
            "user": ctx.user,
            "method": ctx.method,
            "has_request": ctx.request is not None,
        }

    @router.query
    def greet(ctx, name: str) -> str:
        return f"hi {name} from {ctx.user}"

    @router.mutation
    def whoami(ctx) -> str | None:
        return ctx.user

    @router.subscription
    def ticks(ctx, count: int = 2) -> AsyncIterator[Tracked[TickEvent]]:
        async def gen() -> AsyncIterator[Tracked[TickEvent]]:
            for i in range(count):
                yield tracked(str(i), TickEvent(n=i, user=ctx.user))

        return gen()

    return router


def _factory(request: RequestContext) -> RequestContext:
    request.user = request.headers.get("x-user", "anon")
    return request


class TickEvent(BaseModel):
    """One SSE event payload for the subscription tests."""

    n: int
    user: str


class TestContextAsgi:
    def test_default_ctx_is_request_context(self) -> None:
        router = Router()

        @router.query
        def ip(ctx) -> str | None:
            return ctx.client[0] if ctx.client else None

        client = TestClient(mount(router))
        response = client.get("/trpc/ip")
        assert response.status_code == 200
        assert response.json()["result"]["data"] is not None

    def test_ctx_with_input(self) -> None:
        client = TestClient(mount(make_router(), context_factory=_factory))
        response = client.get('/trpc/greet?input={"name":"Ada"}')
        assert response.status_code == 200
        assert response.json()["result"]["data"] == "hi Ada from anon"

    def test_ctx_header_through_factory(self) -> None:
        client = TestClient(mount(make_router(), context_factory=_factory))
        response = client.get('/trpc/greet?input={"name":"Ada"}', headers={"x-user": "grace"})
        assert response.json()["result"]["data"] == "hi Ada from grace"

    def test_ctx_mutation(self) -> None:
        client = TestClient(mount(make_router(), context_factory=_factory))
        response = client.post(
            "/trpc/whoami",
            content=b"",
            headers={"x-user": "hopper", "content-type": "application/json"},
        )
        assert response.json()["result"]["data"] == "hopper"

    def test_ctx_in_batch(self) -> None:
        client = TestClient(mount(make_router(), context_factory=_factory))
        response = client.get(
            '/trpc/greet,me?batch=1&input={"0":{"name":"Ada"}}',
            headers={"x-user": "ada"},
        )
        results = response.json()
        assert results[0]["result"]["data"] == "hi Ada from ada"
        assert results[1]["result"]["data"]["user"] == "ada"

    def test_async_factory(self) -> None:
        async def factory(request: RequestContext) -> dict[str, Any]:
            await asyncio.sleep(0)
            return {"user": "async-" + request.headers.get("x-user", "anon")}

        def factory_wrapped(request: RequestContext) -> Any:
            return factory(request)

        router = Router()

        @router.query
        def me(ctx) -> str:
            return ctx["user"]

        client = TestClient(mount(router, context_factory=factory_wrapped))
        response = client.get("/trpc/me", headers={"x-user": "ada"})
        assert response.json()["result"]["data"] == "async-ada"

    def test_factory_returning_dict(self) -> None:
        router = Router()

        @router.query
        def me(ctx) -> str:
            return ctx["user"]

        client = TestClient(mount(router, context_factory=lambda rc: {"user": "map"}))
        assert client.get("/trpc/me").json()["result"]["data"] == "map"

    def test_factory_error_dev(self) -> None:
        def bad_factory(request: RequestContext) -> Any:
            raise RuntimeError("db down")

        client = TestClient(mount(make_router(), context_factory=bad_factory))
        response = client.get("/trpc/me")
        assert response.status_code == 500
        assert "db down" in response.text

    def test_factory_error_production_redacts(self) -> None:
        def bad_factory(request: RequestContext) -> Any:
            raise RuntimeError("db down")

        client = TestClient(mount(make_router(), production=True, context_factory=bad_factory))
        response = client.get("/trpc/me")
        assert response.status_code == 500
        assert "db down" not in response.text
        assert "Internal server error" in response.text

    def test_ctx_kwarg_only(self) -> None:
        router = Router()

        @router.query
        def who(*, ctx) -> str:
            return ctx.user

        client = TestClient(mount(router, context_factory=_factory))
        response = client.get("/trpc/who", headers={"x-user": "ada"})
        assert response.json()["result"]["data"] == "ada"

    def test_non_first_ctx_remains_input(self) -> None:
        router = Router()

        @router.query
        def weird(name: str, ctx: str) -> str:
            return f"{name}:{ctx}"

        client = TestClient(mount(router))
        response = client.get('/trpc/weird?input={"name":"a","ctx":"b"}')
        assert response.json()["result"]["data"] == "a:b"

    def test_input_not_leaked_into_ctx(self) -> None:
        # A client-supplied "ctx" key must not override the injected context.
        router = Router()

        @router.query
        def me(ctx, name: str = "x") -> str:
            return str(ctx.user)

        client = TestClient(mount(router, context_factory=_factory))
        response = client.get('/trpc/me?input={"ctx":{"user":"evil"}}', headers={"x-user": "ada"})
        # extra keys are forbidden by validation → BAD_REQUEST, not injection
        assert response.status_code == 400
        response2 = client.get("/trpc/me", headers={"x-user": "ada"})
        assert response2.json()["result"]["data"] == "ada"


class TestContextSubscription:
    def test_ctx_in_events(self) -> None:
        app = mount(make_router(), path="", context_factory=_factory)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/ticks",
            "query_string": b"input=%7B%7D",
            "headers": [(b"x-user", b"ada")],
        }
        sent = asyncio.run(_collect_sse(app, scope))
        body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body").decode()
        assert '"user":"ada"' in body
        assert '"n":0' in body and '"n":1' in body
        assert "event: return" in body


class TestContextRest:
    def _rest_router(self) -> Router:
        router = Router()

        @router.query(rest=RestRoute("GET", "/hello/{name}"))
        def hello(ctx, name: str) -> str:
            return f"hi {name} from {ctx.user}"

        return router

    def test_ctx_on_rest_route(self) -> None:
        client = TestClient(mount_rest(self._rest_router(), context_factory=_factory))
        response = client.get("/hello/Ada", headers={"x-user": "grace"})
        assert response.status_code == 200
        assert response.json() == "hi Ada from grace"

    def test_rest_factory_failure(self) -> None:
        def bad_factory(request: RequestContext) -> Any:
            raise RuntimeError("db down")

        client = TestClient(
            mount_rest(self._rest_router(), production=True, context_factory=bad_factory)
        )
        response = client.get("/hello/Ada")
        assert response.status_code == 500
        assert "db down" not in response.text


class TestContextWsgi:
    def test_ctx_through_wsgi(self) -> None:
        from werkzeug.test import Client
        from werkzeug.wrappers import Response

        app = mount_wsgi(make_router(), context_factory=_factory)
        client = Client(app, Response)
        response = client.get(
            "/trpc/greet?input=%7B%22name%22%3A%22Ada%22%7D",
            headers={"x-user": "ada"},
        )
        assert response.status_code == 200
        assert response.get_json()["result"]["data"] == "hi Ada from ada"

    def test_request_is_environ(self) -> None:
        router = Router()
        seen: list[Any] = []

        @router.query
        def probe(ctx) -> bool:
            seen.append(ctx.request)
            return True

        from werkzeug.test import Client
        from werkzeug.wrappers import Response

        client = Client(mount_wsgi(router), Response)
        client.get("/trpc/probe")
        assert len(seen) == 1
        assert "REQUEST_METHOD" in seen[0]  # WSGI environ, not the scope dict


class TestContextCodegen:
    def test_codegen_skips_ctx(self) -> None:
        dts = generate_types(make_router())
        greet_input = dts.split("GreetProcedure")[1].split("input:")[1].split("output:")[0]
        assert "ctx" not in greet_input
        me_input = dts.split("MeProcedure")[1].split("input:")[1].split("output:")[0]
        assert "undefined" in me_input

    def test_zodgen_skips_ctx(self) -> None:
        zod = generate_zod(make_router())
        assert "ctx" not in zod
        assert "name" in zod

    def test_openapi_skips_ctx(self) -> None:
        router = Router()

        @router.query(rest=RestRoute("GET", "/greet/{name}"))
        def greet(ctx, name: str, punct: str = "!") -> str:
            return name

        doc = generate_openapi(router)
        params = doc["paths"]["/greet/{name}"]["get"]["parameters"]
        assert [p["name"] for p in params] == ["name", "punct"]


class TestLegacyPath:
    """Procedures registered after mount use the signature pre-check path."""

    def test_call_handler_injects_ctx(self) -> None:
        def f(ctx, name: str) -> str:
            return f"{ctx}:{name}"

        proc = Procedure(path="f", kind=ProcedureKind.QUERY, handler=f)
        assert _call_handler(proc, {"name": "ada"}, "CTX") == "CTX:ada"

    def test_input_fits_ignores_ctx(self) -> None:
        def f(ctx, name: str) -> str:
            return name

        assert _input_fits_signature(f, {"name": "ada"})
        # The ctx param must not count as required input.
        assert not _input_fits_signature(f, {"ctx": "evil"})

    def test_ctx_only_handler_binds_empty(self) -> None:
        def f(ctx) -> str:
            return "x"

        assert _input_fits_signature(f, None)


class TestContextDjango:
    def test_ctx_through_django_sync_view(self) -> None:
        pytest.importorskip("django")
        import types

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

        from django.test import Client
        from django.test.utils import override_settings
        from django.urls import path as django_path

        from trpc_rs_py.django import trpc_view

        seen: list[Any] = []
        router = Router()

        @router.query
        def me(ctx) -> str:
            seen.append(ctx.request)
            return ctx.user

        urlconf = types.ModuleType("ctx_urlconf")
        urlconf.urlpatterns = [  # type: ignore[attr-defined]
            django_path("trpc/<path:trpc_path>", trpc_view(router, context_factory=_factory))
        ]
        client = Client()
        with override_settings(ROOT_URLCONF=urlconf):
            response = client.get("/trpc/me", HTTP_X_USER="ada")
        assert response.status_code == 200
        assert response.json()["result"]["data"] == "ada"
        from django.http import HttpRequest

        assert isinstance(seen[0], HttpRequest)
