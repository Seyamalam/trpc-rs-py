# Django adapter tests: sync view, async view, and the urlpatterns helper —
# settings are configured in-module so no project scaffolding is needed.

import asyncio
import json
from typing import Any

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        SECRET_KEY="test-secret",
        DATABASES={},
        INSTALLED_APPS=["django.contrib.contenttypes"],
    )
    django.setup()

from django.test import AsyncClient, Client
from django.urls import path as django_path

from trpc_rs_py import RestRoute, Router, mount_rest
from trpc_rs_py.django import asgi_view, asgi_view_async, trpc_view
from trpc_rs_py.django import urlpatterns as trpc_urlpatterns
from trpc_rs_py.errors import TRPCError, TRPCErrorCode


def make_router() -> Router:
    router = Router()

    @router.query(rest=RestRoute("GET", "/ping"))
    def ping() -> str:
        return "pong"

    @router.query
    def echo(message: str) -> str:
        return f"echo: {message}"

    @router.query
    def boom() -> str:
        raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

    @router.query
    def explode() -> str:
        raise RuntimeError("secret internals")

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title, "created": True}

    async def async_echo(message: str) -> str:
        return f"async: {message}"

    router.query(name="async_echo")(async_echo)

    @router.subscription
    def ticks():
        async def stream():
            yield {"n": 0}
            yield {"n": 1}

        return stream()

    return router


ROUTER = make_router()
REST = mount_rest(ROUTER, title="Test API", version="0.1.0")

urlpatterns = [
    *trpc_urlpatterns(ROUTER),
    *trpc_urlpatterns(ROUTER, prefix="atrpc", asynchronous=True),
    django_path("api/<path:path>", asgi_view(REST)),
    django_path("aapi/<path:path>", asgi_view_async(REST)),
]


def encode_input(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


# --- sync view (WSGI-side bridge) ---------------------------------------------


def test_sync_query() -> None:
    r = Client().get("/trpc/echo", {"input": encode_input({"message": "hi"})})
    assert r.status_code == 200
    assert r.json() == {"result": {"data": "echo: hi"}}


def test_sync_no_input() -> None:
    assert Client().get("/trpc/ping").json() == {"result": {"data": "pong"}}


def test_sync_mutation_post() -> None:
    r = Client().post(
        "/trpc/create",
        data=encode_input({"title": "hello"}),
        content_type="application/json",
    )
    assert r.json() == {"result": {"data": {"title": "hello", "created": True}}}


def test_sync_batched() -> None:
    r = Client().get(
        "/trpc/ping,echo",
        {"batch": "1", "input": encode_input({"0": None, "1": {"message": "x"}})},
    )
    assert r.json() == [
        {"result": {"data": "pong"}},
        {"result": {"data": "echo: x"}},
    ]


def test_sync_not_found() -> None:
    r = Client().get("/trpc/nope")
    assert r.status_code == 404
    assert r.json()["error"]["data"]["code"] == "NOT_FOUND"
    assert r.json()["error"]["data"]["path"] == "nope"


def test_sync_typed_error() -> None:
    r = Client().get("/trpc/boom")
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "nope"


def test_sync_internal_error_development() -> None:
    r = Client().get("/trpc/explode")
    assert r.status_code == 500
    assert "secret internals" in r.json()["error"]["message"]


def test_sync_internal_error_production() -> None:
    """production=True redacts internals: swap URLconf via override_settings."""
    import types

    from django.test.utils import override_settings

    prod = types.ModuleType("prod_urlconf")
    prod.urlpatterns = trpc_urlpatterns(ROUTER, production=True)
    with override_settings(ROOT_URLCONF=prod):
        r = Client().get("/trpc/explode")
    assert r.status_code == 500
    assert r.json()["error"]["message"] == "Internal server error"
    assert "secret" not in r.json()["error"]["message"]


def test_sync_method_not_supported() -> None:
    r = Client().get("/trpc/create", {"input": encode_input({"title": "x"})})
    assert r.status_code == 405


def test_sync_sse_streams() -> None:
    r = Client().get("/trpc/ticks")
    assert r.status_code == 200
    assert r.streaming
    body = b"".join(r.streaming_content).decode()
    assert "event: connected" in body
    assert '"n":1' in body
    assert "event: return" in body


# --- async view ---------------------------------------------------------------


def test_async_query() -> None:
    async def run() -> None:
        r = await AsyncClient().get("/atrpc/echo", {"input": encode_input({"message": "a"})})
        assert r.status_code == 200
        assert r.json() == {"result": {"data": "echo: a"}}

    asyncio.run(run())


def test_async_mutation() -> None:
    async def run() -> None:
        r = await AsyncClient().post(
            "/atrpc/create",
            data=encode_input({"title": "x"}),
            content_type="application/json",
        )
        assert r.json() == {"result": {"data": {"title": "x", "created": True}}}

    asyncio.run(run())


def test_async_not_found() -> None:
    async def run() -> None:
        r = await AsyncClient().get("/atrpc/nope")
        assert r.status_code == 404
        assert r.json()["error"]["data"]["path"] == "nope"

    asyncio.run(run())


def test_async_sse_streams() -> None:
    async def run() -> None:
        r = await AsyncClient().get("/atrpc/ticks")
        assert r.status_code == 200
        chunks = [chunk async for chunk in r.streaming_content]
        body = b"".join(chunks).decode()
        assert "event: connected" in body
        assert '"n":0' in body

    asyncio.run(run())


# --- generic ASGI views (REST mount) ------------------------------------------


def test_asgi_view_serves_rest_get() -> None:
    r = Client().get("/api/ping")
    assert r.status_code == 200
    assert r.json() == "pong"


def test_asgi_view_serves_openapi_doc() -> None:
    r = Client().get("/api/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "Test API"


def test_asgi_view_async_serves_rest_get() -> None:
    async def run() -> None:
        r = await AsyncClient().get("/aapi/ping")
        assert r.status_code == 200
        assert r.json() == "pong"

    asyncio.run(run())


# --- helper surface -----------------------------------------------------------


def test_urlpatterns_returns_path_list() -> None:
    pats = trpc_urlpatterns(make_router(), prefix="api")
    assert len(pats) == 1
    assert "trpc_path" in pats[0].pattern.describe()


def test_manual_view_wiring() -> None:
    """trpc_view wired by hand (not via urlpatterns) serves the same path."""
    import types

    from django.test.utils import override_settings

    manual = types.ModuleType("manual_urlconf")
    manual.urlpatterns = [django_path("t/<path:trpc_path>", trpc_view(ROUTER))]
    with override_settings(ROOT_URLCONF=manual):
        r = Client().get("/t/ping")
    assert r.json() == {"result": {"data": "pong"}}
