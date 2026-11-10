# CORS middleware tests: deny-by-default policy, preflight handling,
# wildcard/credentials interplay, the WSGI bridge, and SSE pass-through
# (MISSION section 7, ADR 0011).

import asyncio
import json
from collections.abc import Callable
from typing import Any

from starlette.testclient import TestClient
from werkzeug.test import Client
from werkzeug.wrappers import Response

from trpc_rs_py import Router, mount, wsgi_from_asgi
from trpc_rs_py.cors import cors


def make_router() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title, "created": True}

    @router.subscription
    def ticks():
        async def stream():
            yield {"n": 0}
            yield {"n": 1}

        return stream()

    return router


def _scope(
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
    }


def _receive(messages: list[dict[str, Any]]) -> Callable[[], Any]:
    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return receive


def _send(sent: list[dict[str, Any]]) -> Callable[[dict[str, Any]], Any]:
    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    return send


def _run(app: Any, scope: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> Any:
    sent: list[dict[str, Any]] = []
    asyncio.run(app(scope, _receive(messages or []), _send(sent)))
    return sent


def _headers(sent: list[dict[str, Any]]) -> dict[bytes, bytes]:
    return dict(sent[0]["headers"])


def test_default_deny_emits_nothing_and_refuses_preflights() -> None:
    app = cors(mount(make_router()))
    origin = (b"origin", b"https://app.example.com")

    sent = _run(app, _scope("GET", "/trpc/ping", [origin]))
    assert sent[0]["status"] == 200
    assert _headers(sent).get(b"access-control-allow-origin") is None
    assert json.loads(sent[1]["body"]) == {"result": {"data": "pong"}}

    sent = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [origin, (b"access-control-request-method", b"GET")],
        ),
    )
    assert sent[0]["status"] == 403
    assert _headers(sent).get(b"access-control-allow-origin") is None


def test_allowlist_decorates_allowed_and_ignores_disallowed() -> None:
    app = cors(
        mount(make_router()),
        allow_origins=["https://app.example.com"],
        expose_headers=["x-request-id"],
    )
    allowed = _run(
        app,
        _scope("GET", "/trpc/ping", [(b"origin", b"https://app.example.com")]),
    )
    headers = _headers(allowed)
    assert headers[b"access-control-allow-origin"] == b"https://app.example.com"
    assert headers[b"vary"] == b"Origin"
    assert headers[b"access-control-expose-headers"] == b"x-request-id"

    post = _run(
        app,
        _scope(
            "POST",
            "/trpc/create",
            [
                (b"origin", b"https://app.example.com"),
                (b"content-type", b"application/json"),
            ],
        ),
        [{"type": "http.request", "body": b'{"title":"x"}', "more_body": False}],
    )
    assert _headers(post)[b"access-control-allow-origin"] == b"https://app.example.com"

    denied = _run(
        app,
        _scope("GET", "/trpc/ping", [(b"origin", b"https://evil.example.com")]),
    )
    assert denied[0]["status"] == 200
    assert _headers(denied).get(b"access-control-allow-origin") is None
    assert b"vary" not in _headers(denied)


def test_origin_matching_is_exact_string() -> None:
    app = cors(mount(make_router()), allow_origins=["https://a.com"])
    sent = _run(app, _scope("GET", "/trpc/ping", [(b"origin", b"http://a.com")]))
    assert _headers(sent).get(b"access-control-allow-origin") is None


def test_preflight_allowed_origin_gets_204_with_allow_headers() -> None:
    app = cors(
        mount(make_router()),
        allow_origins=["https://app.example.com"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "x-trpc-source"],
        max_age=42,
    )
    sent = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/create",
            [
                (b"origin", b"https://app.example.com"),
                (b"access-control-request-method", b"POST"),
                (b"access-control-request-headers", b"content-type"),
            ],
        ),
    )
    assert sent[0]["status"] == 204
    headers = _headers(sent)
    assert headers[b"access-control-allow-origin"] == b"https://app.example.com"
    assert headers[b"access-control-allow-methods"] == b"GET, POST, OPTIONS"
    assert headers[b"access-control-allow-headers"] == b"content-type, x-trpc-source"
    assert headers[b"access-control-max-age"] == b"42"
    assert b"access-control-allow-credentials" not in headers
    vary = {token.strip() for token in headers[b"vary"].split(b",")}
    assert vary == {
        b"Origin",
        b"Access-Control-Request-Method",
        b"Access-Control-Request-Headers",
    }


def test_preflight_disallowed_origin_is_403_without_cors_headers() -> None:
    app = cors(mount(make_router()), allow_origins=["https://app.example.com"])
    sent = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [
                (b"origin", b"https://evil.example.com"),
                (b"access-control-request-method", b"GET"),
            ],
        ),
    )
    assert sent[0]["status"] == 403
    for name in _headers(sent):
        assert not name.startswith(b"access-control")


def test_preflight_credentials_flag_only_when_set() -> None:
    app = cors(
        mount(make_router()),
        allow_origins=["https://app.example.com"],
        allow_credentials=True,
    )
    sent = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [
                (b"origin", b"https://app.example.com"),
                (b"access-control-request-method", b"GET"),
            ],
        ),
    )
    assert _headers(sent)[b"access-control-allow-credentials"] == b"true"


def test_preflight_wildcard_headers_echoes_requested() -> None:
    app = cors(
        mount(make_router()),
        allow_origins=["https://app.example.com"],
        allow_headers=["*"],
    )
    sent = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [
                (b"origin", b"https://app.example.com"),
                (b"access-control-request-method", b"GET"),
                (b"access-control-request-headers", b"Content-Type, X-Custom"),
            ],
        ),
    )
    assert _headers(sent)[b"access-control-allow-headers"] == b"Content-Type, X-Custom"


def test_wildcard_origin_emits_star() -> None:
    app = cors(mount(make_router()), allow_origins=["*"])
    sent = _run(app, _scope("GET", "/trpc/ping", [(b"origin", b"https://anything.io")]))
    assert _headers(sent)[b"access-control-allow-origin"] == b"*"

    preflight = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [
                (b"origin", b"https://anything.io"),
                (b"access-control-request-method", b"GET"),
            ],
        ),
    )
    assert _headers(preflight)[b"access-control-allow-origin"] == b"*"


def test_wildcard_with_credentials_reflects_concrete_origin() -> None:
    app = cors(mount(make_router()), allow_origins=["*"], allow_credentials=True)
    sent = _run(app, _scope("GET", "/trpc/ping", [(b"origin", b"https://a.com")]))
    headers = _headers(sent)
    assert headers[b"access-control-allow-origin"] == b"https://a.com"
    assert headers[b"access-control-allow-credentials"] == b"true"

    preflight = _run(
        app,
        _scope(
            "OPTIONS",
            "/trpc/ping",
            [
                (b"origin", b"https://b.com"),
                (b"access-control-request-method", b"POST"),
            ],
        ),
    )
    assert _headers(preflight)[b"access-control-allow-origin"] == b"https://b.com"


def test_options_without_request_method_passes_to_app_undecorated() -> None:
    # Not a preflight: the wrapped app answers (405 for tRPC) and no CORS
    # headers are added.
    app = cors(mount(make_router()), allow_origins=["https://app.example.com"])
    sent = _run(
        app,
        _scope("OPTIONS", "/trpc/ping", [(b"origin", b"https://app.example.com")]),
    )
    assert sent[0]["status"] == 405
    for name in _headers(sent):
        assert not name.startswith(b"access-control")


def test_request_without_origin_passes_through() -> None:
    app = cors(mount(make_router()), allow_origins=["https://app.example.com"])
    sent = _run(app, _scope("GET", "/trpc/ping"))
    assert sent[0]["status"] == 200
    assert _headers(sent).get(b"access-control-allow-origin") is None


def test_vary_merges_with_existing_vary_header() -> None:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"vary", b"Accept-Encoding"), (b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    app = cors(inner, allow_origins=["https://app.example.com"])
    sent = _run(app, _scope("GET", "/", [(b"origin", b"https://app.example.com")]))
    headers = _headers(sent)
    assert headers[b"vary"] == b"Accept-Encoding, Origin"
    assert headers[b"access-control-allow-origin"] == b"https://app.example.com"


def test_sse_subscription_streams_with_acao() -> None:
    app = cors(mount(make_router()), allow_origins=["https://app.example.com"])
    sent: list[dict[str, Any]] = []

    async def never_disconnect() -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    asyncio.run(
        app(
            _scope("GET", "/trpc/ticks", [(b"origin", b"https://app.example.com")]),
            never_disconnect,
            _send(sent),
        )
    )
    headers = _headers(sent)
    assert headers[b"access-control-allow-origin"] == b"https://app.example.com"
    assert headers[b"content-type"] == b"text/event-stream"
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    assert b"event: connected" in body
    assert b'"n":1' in body


def test_non_http_scopes_pass_through_untouched() -> None:
    calls: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        calls.append(scope["type"])
        await send({"type": f"{scope['type']}.custom", "marker": True})

    app = cors(inner, allow_origins=["https://app.example.com"])
    sent: list[dict[str, Any]] = []
    asyncio.run(app({"type": "websocket"}, _receive([]), _send(sent)))
    asyncio.run(app({"type": "lifespan"}, _receive([]), _send(sent)))
    assert calls == ["websocket", "lifespan"]
    assert sent == [
        {"type": "websocket.custom", "marker": True},
        {"type": "lifespan.custom", "marker": True},
    ]


def test_cors_through_wsgi_bridge() -> None:
    # wsgi_from_asgi(cors(mount(...))) — the ADR 0010 bridge means one
    # implementation covers WSGI adapters too.
    app = wsgi_from_asgi(cors(mount(make_router()), allow_origins=["https://app.example.com"]))
    client = Client(app, Response)

    response = client.get("/trpc/ping", headers={"Origin": "https://app.example.com"})
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://app.example.com"

    preflight = client.open(
        "/trpc/ping",
        method="OPTIONS",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["Access-Control-Allow-Origin"] == "https://app.example.com"


def test_cors_with_starlette_test_client() -> None:
    app = cors(
        mount(make_router()),
        allow_origins=["https://app.example.com"],
    )
    client = TestClient(app)

    response = client.get("/trpc/ping", headers={"Origin": "https://app.example.com"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
    assert response.json() == {"result": {"data": "pong"}}

    preflight = client.options(
        "/trpc/ping",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "https://app.example.com"

    denied = client.options(
        "/trpc/ping",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert denied.status_code == 403
    assert "access-control-allow-origin" not in denied.headers
