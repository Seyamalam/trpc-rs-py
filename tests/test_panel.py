# Playground panel tests (MISSION 2.7): enablement rules, the introspection
# document, dispatch ordering, and `panel` forwarding across the WSGI and
# Django adapters.

from __future__ import annotations

import asyncio
import json
import types
from collections.abc import AsyncIterator, Callable
from typing import Any

import django
import pytest
from django.conf import settings
from werkzeug.test import Client as WsgiClient
from werkzeug.wrappers import Response as WsgiResponse

from trpc_rs_py import RestRoute, Router, attach, mount, mount_wsgi, wsgi_from_asgi
from trpc_rs_py.panel import introspection_document, panel_subpath

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

from django.test import Client as DjangoClient
from django.test.utils import override_settings

from trpc_rs_py.django import urlpatterns as trpc_urlpatterns


def make_router() -> Router:
    router = Router()

    @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
    def by_id(post_id: int) -> str:
        return str(post_id)

    @router.query
    def echo(message: str, loud: bool = False) -> str:
        return message.upper() if loud else message

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title}

    @router.subscription
    def ticks() -> AsyncIterator[int]:
        async def gen() -> AsyncIterator[int]:
            yield 1

        return gen()

    @router.query
    def ping() -> str:
        return "pong"

    return router


def _scope(
    method: str, path: str, query: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }


def _call(
    app: Any,
    method: str,
    path: str,
    query: bytes = b"",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes]:
    """Drive an ASGI app through one request/response cycle."""
    sent: list[dict[str, Any]] = []
    inbox = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if inbox:
            return inbox.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(_scope(method, path, query, headers), receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k: v for k, v in start["headers"]}
    return start["status"], headers, payload


async def _sink(scope: Any, receive: Any, send: Any) -> None:
    """Fallback ASGI app answering a fixed JSON body."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b'{"native":true}'})


class TestEnablement:
    def test_dev_default_serves_page_and_schema(self) -> None:
        app = mount(make_router())
        status, headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 200
        assert headers[b"content-type"] == b"text/html; charset=utf-8"
        assert b"<title>tRPC panel</title>" in body
        status, headers, body = _call(app, "GET", "/trpc/panel/schema")
        assert status == 200
        assert headers[b"content-type"] == b"application/json"
        assert json.loads(body)["procedures"]

    def test_production_default_disables(self) -> None:
        app = mount(make_router(), production=True)
        status, _headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 404
        assert json.loads(body)["error"]["data"]["code"] == "NOT_FOUND"
        # With the panel off, `panel/schema` is merely an invalid procedure
        # path (the `/` is outside the path charset) — a protocol 400.
        status, _headers, body = _call(app, "GET", "/trpc/panel/schema")
        assert status == 400
        assert json.loads(body)["error"]["data"]["code"] == "BAD_REQUEST"

    def test_explicit_panel_true_in_production(self) -> None:
        app = mount(make_router(), production=True, panel=True)
        status, _headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 200
        assert b"<title>tRPC panel</title>" in body

    def test_explicit_panel_false_in_dev(self) -> None:
        app = mount(make_router(), panel=False)
        status, _headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 404
        assert json.loads(body)["error"]["data"]["code"] == "NOT_FOUND"

    def test_custom_mount_path(self) -> None:
        app = mount(make_router(), path="/api/trpc")
        status, _headers, body = _call(app, "GET", "/api/trpc/panel")
        assert status == 200
        status, _headers, body = _call(app, "GET", "/api/trpc/panel/schema")
        assert status == 200
        # Procedures dispatch under the same prefix.
        status, _headers, body = _call(app, "GET", "/api/trpc/ping")
        assert status == 200
        assert json.loads(body)["result"]["data"] == "pong"


class TestDispatchRules:
    def test_unknown_panel_subpath_404s(self) -> None:
        status, _headers, body = _call(mount(make_router()), "GET", "/trpc/panel/nope")
        assert status == 404
        assert json.loads(body)["error"]["code"] == "NOT_FOUND"

    def test_post_to_panel_405s(self) -> None:
        status, headers, body = _call(mount(make_router()), "POST", "/trpc/panel")
        assert status == 405
        assert headers[b"allow"] == b"GET"
        assert json.loads(body)["error"]["code"] == "METHOD_NOT_ALLOWED"
        status, _headers, _body = _call(
            mount(make_router()), "POST", "/trpc/panel/schema", body=b"{}"
        )
        assert status == 405

    def test_panel_named_procedure_is_shadowed(self) -> None:
        router = Router()

        @router.query
        def panel() -> str:
            return "the procedure"

        app = mount(router)
        status, headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 200
        assert headers[b"content-type"] == b"text/html; charset=utf-8"
        # Disabling the panel frees the procedure path again.
        status, _headers, body = _call(mount(router, panel=False), "GET", "/trpc/panel")
        assert status == 200
        assert json.loads(body)["result"]["data"] == "the procedure"

    def test_panel_dot_paths_reach_procedures(self) -> None:
        router = Router()

        @router.query(name="panel.metrics")
        def metrics() -> str:
            return "m"

        status, _headers, body = _call(mount(router), "GET", "/trpc/panel.metrics")
        assert status == 200
        assert json.loads(body)["result"]["data"] == "m"

    def test_procedure_calls_still_work(self) -> None:
        app = mount(make_router())
        status, _headers, body = _call(
            app, "GET", "/trpc/echo", b"input=%7B%22message%22%3A%22hi%22%7D"
        )
        assert status == 200
        assert json.loads(body)["result"]["data"] == "hi"
        status, _headers, body = _call(
            app,
            "POST",
            "/trpc/create",
            body=b'{"title":"x"}',
            headers=[(b"content-type", b"application/json")],
        )
        assert status == 200
        assert json.loads(body)["result"]["data"] == {"title": "x"}


class TestIntrospection:
    def test_document_shape(self) -> None:
        doc = introspection_document(make_router())
        procs = {p["path"]: p for p in doc["procedures"]}
        assert procs["echo"]["kind"] == "query"
        assert procs["create"]["kind"] == "mutation"
        assert procs["ticks"]["kind"] == "subscription"
        echo_in = procs["echo"]["input"]
        assert echo_in["type"] == "object"
        assert echo_in["properties"]["message"] == {"type": "string", "title": "Message"}
        assert echo_in["required"] == ["message"]
        assert procs["echo"]["output"] == {"type": "string"}
        assert procs["by_id"]["rest"] == {"method": "GET", "path": "/posts/{post_id}"}
        assert "rest" not in procs["echo"]
        # A subscription's output describes one yielded item.
        assert procs["ticks"]["output"] == {"type": "integer"}
        # Parameterless procedures take no input.
        assert procs["ping"]["input"] is None

    def test_ctx_parameter_is_not_input(self) -> None:
        router = Router()

        @router.query
        def whoami(ctx, n: int) -> int:
            return n

        (proc,) = introspection_document(router)["procedures"]
        assert list(proc["input"]["properties"]) == ["n"]

    def test_unserializable_annotations_become_null(self) -> None:
        router = Router()

        @router.query
        def weird_input(cb: Callable) -> str:
            return "x"

        @router.query
        def weird_output() -> Callable:
            return lambda: None

        procs = {p["path"]: p for p in introspection_document(router)["procedures"]}
        assert procs["weird_input"]["input"] is None
        assert procs["weird_output"]["output"] is None
        # The endpoint still serves over HTTP — nothing crashes.
        status, _headers, body = _call(mount(router), "GET", "/trpc/panel/schema")
        assert status == 200
        assert {p["path"] for p in json.loads(body)["procedures"]} == {
            "weird_input",
            "weird_output",
        }

    def test_panel_subpath(self) -> None:
        assert panel_subpath("panel") == ""
        assert panel_subpath("panel/schema") == "schema"
        assert panel_subpath("panel/") == ""
        assert panel_subpath("panel.metrics") is None
        assert panel_subpath("panels") is None
        assert panel_subpath("echo") is None


class TestAttach:
    def test_attach_serves_panel_and_passes_through(self) -> None:
        app = attach(_sink, make_router())
        status, _headers, body = _call(app, "GET", "/trpc/panel")
        assert status == 200
        assert b"<title>tRPC panel</title>" in body
        status, _headers, body = _call(app, "GET", "/trpc/panel/schema")
        assert status == 200
        assert json.loads(body)["procedures"]
        # Non-tRPC paths still reach the wrapped app.
        status, _headers, body = _call(app, "GET", "/other")
        assert status == 200
        assert json.loads(body) == {"native": True}

    def test_attach_production_default_disables(self) -> None:
        app = attach(_sink, make_router(), production=True)
        status, _headers, _body = _call(app, "GET", "/trpc/panel")
        assert status == 404

    def test_attach_explicit_panel(self) -> None:
        app = attach(_sink, make_router(), production=True, panel=True)
        status, _headers, _body = _call(app, "GET", "/trpc/panel")
        assert status == 200


class TestWsgiForwarding:
    def test_mount_wsgi_serves_panel(self) -> None:
        client = WsgiClient(mount_wsgi(make_router()), WsgiResponse)
        r = client.get("/trpc/panel")
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert b"tRPC panel" in r.data
        r = client.get("/trpc/panel/schema")
        assert r.status_code == 200
        assert {p["path"] for p in r.json["procedures"]} >= {"echo", "create"}

    def test_mount_wsgi_panel_flags(self) -> None:
        prod = WsgiClient(mount_wsgi(make_router(), production=True), WsgiResponse)
        assert prod.get("/trpc/panel").status_code == 404
        prod_on = WsgiClient(mount_wsgi(make_router(), production=True, panel=True), WsgiResponse)
        assert prod_on.get("/trpc/panel").status_code == 200
        off = WsgiClient(mount_wsgi(make_router(), panel=False), WsgiResponse)
        assert off.get("/trpc/panel").status_code == 404

    def test_wsgi_from_asgi_wraps_panel(self) -> None:
        client = WsgiClient(wsgi_from_asgi(mount(make_router())), WsgiResponse)
        assert client.get("/trpc/panel").status_code == 200

    def test_attach_flask_forwards_panel(self) -> None:
        flask = pytest.importorskip("flask")
        app = flask.Flask(__name__)
        from trpc_rs_py import attach_flask

        attach_flask(app, make_router())
        client = WsgiClient(app, WsgiResponse)
        assert client.get("/trpc/panel").status_code == 200
        assert client.get("/trpc/panel/schema").status_code == 200


class TestDjangoForwarding:
    def _urlconf(self, name: str, **kwargs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        module.urlpatterns = trpc_urlpatterns(make_router(), **kwargs)
        return module

    def test_trpc_view_serves_panel(self) -> None:
        with override_settings(ROOT_URLCONF=self._urlconf("panel_on")):
            r = DjangoClient().get("/trpc/panel")
            assert r.status_code == 200
            assert b"tRPC panel" in r.content
            r = DjangoClient().get("/trpc/panel/schema")
            assert r.status_code == 200
            assert {p["path"] for p in r.json()["procedures"]} >= {"echo"}
            # Procedure dispatch still works through the same view.
            r = DjangoClient().get("/trpc/ping")
            assert r.json() == {"result": {"data": "pong"}}

    def test_trpc_view_panel_flags(self) -> None:
        with override_settings(ROOT_URLCONF=self._urlconf("panel_off", panel=False)):
            assert DjangoClient().get("/trpc/panel").status_code == 404
        with override_settings(ROOT_URLCONF=self._urlconf("panel_prod", production=True)):
            assert DjangoClient().get("/trpc/panel").status_code == 404
        with override_settings(
            ROOT_URLCONF=self._urlconf("panel_prod_on", production=True, panel=True)
        ):
            assert DjangoClient().get("/trpc/panel").status_code == 200

    def test_trpc_view_async_serves_panel(self) -> None:
        module = types.ModuleType("panel_async")
        module.urlpatterns = trpc_urlpatterns(make_router(), asynchronous=True)
        with override_settings(ROOT_URLCONF=module):
            assert DjangoClient().get("/trpc/panel").status_code == 200


class TestPageContent:
    def test_html_hook_markers(self) -> None:
        _status, _headers, body = _call(mount(make_router()), "GET", "/trpc/panel")
        for marker in (
            b'id="procs"',
            b'id="search"',
            b"panel/schema",
            b"EventSource",
            b"fetch(",
            b"lastEventId",
        ):
            assert marker in body, marker

    def test_page_is_self_contained(self) -> None:
        _status, _headers, body = _call(mount(make_router()), "GET", "/trpc/panel")
        # No external asset references of any kind (MISSION 2.7: no CDN).
        assert b"http://" not in body
        assert b"https://" not in body
        assert b"<script src" not in body
        assert b"<link" not in body
        assert b"@import" not in body
        assert b"url(" not in body
