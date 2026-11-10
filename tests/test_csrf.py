# CSRF helper tests: the credential-keyed Origin/Sec-Fetch-Site filter that
# runs before dispatch (ADR 0011). Raw-ASGI harness for header combinations
# TestClient cannot express, plus TestClient and wsgi_from_asgi coverage.

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from starlette.testclient import TestClient

from trpc_rs_py import Router, mount, wsgi_from_asgi
from trpc_rs_py.asgi import ASGIApp
from trpc_rs_py.csrf import csrf_protect

ALLOWED_ORIGINS = ("https://app.example.com",)
FORBIDDEN_BODY = {"error": {"code": "FORBIDDEN", "message": "CSRF check failed"}}

COOKIE = (b"cookie", b"session=abc")
AUTHORIZATION = (b"authorization", b"Bearer token123")
ORIGIN_OK = (b"origin", b"https://app.example.com")
ORIGIN_BAD = (b"origin", b"https://evil.example")


def make_router() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title}

    return router


def _protect(**kwargs: Any) -> ASGIApp:
    return csrf_protect(mount(make_router()), **kwargs)


def _scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
    }


def _receive(body: bytes) -> Callable[[], Any]:
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return receive


def _request(
    app: ASGIApp,
    method: str = "GET",
    path: str = "/trpc/ping",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
) -> tuple[int, Any]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(_scope(method, path, headers or []), _receive(body), send))
    status = sent[0]["status"]
    payload = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, json.loads(payload) if payload else None


def _mutation(app: ASGIApp, *headers: tuple[bytes, bytes]) -> tuple[int, Any]:
    return _request(
        app,
        "POST",
        "/trpc/create",
        [(b"content-type", b"application/json"), *headers],
        b'{"title":"x"}',
    )


class TestUncredentialedRequests:
    def test_get_without_credentials_passes(self) -> None:
        status, body = _request(_protect(allowed_origins=ALLOWED_ORIGINS))
        assert status == 200
        assert body == {"result": {"data": "pong"}}

    def test_post_without_credentials_passes(self) -> None:
        # No Cookie/Authorization: nothing ambient to abuse, so the request
        # is unchecked even without Origin or fetch-metadata headers.
        status, body = _mutation(_protect(allowed_origins=ALLOWED_ORIGINS))
        assert status == 200
        assert body == {"result": {"data": {"title": "x"}}}

    def test_empty_cookie_header_is_not_credentialed(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS), headers=[(b"cookie", b"  ")]
        )
        assert status == 200


class TestCredentialedMutations:
    def test_cookie_post_with_allowlisted_origin(self) -> None:
        status, body = _mutation(_protect(allowed_origins=ALLOWED_ORIGINS), COOKIE, ORIGIN_OK)
        assert status == 200
        assert body == {"result": {"data": {"title": "x"}}}

    def test_cookie_post_with_disallowed_origin(self) -> None:
        status, body = _mutation(_protect(allowed_origins=ALLOWED_ORIGINS), COOKIE, ORIGIN_BAD)
        assert status == 403
        assert body == FORBIDDEN_BODY

    def test_cookie_post_without_origin_or_fetch_site(self) -> None:
        status, body = _mutation(_protect(allowed_origins=ALLOWED_ORIGINS), COOKIE)
        assert status == 403
        assert body == FORBIDDEN_BODY

    def test_origin_match_is_exact(self) -> None:
        for origin in (
            b"https://app.example.com.evil.com",
            b"http://app.example.com",  # scheme matters
            b"https://app.example.com:443",
            b"https://app.example.com/",
        ):
            status, body = _mutation(
                _protect(allowed_origins=ALLOWED_ORIGINS), COOKIE, (b"origin", origin)
            )
            assert status == 403, origin
            assert body == FORBIDDEN_BODY

    def test_empty_allowlist_never_matches_origin(self) -> None:
        status, _ = _mutation(_protect(), COOKIE, ORIGIN_OK)
        assert status == 403


class TestFetchSite:
    @pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
    def test_safe_fetch_site_allows_without_origin(self, site: str) -> None:
        status, _ = _mutation(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            COOKIE,
            (b"sec-fetch-site", site.encode()),
        )
        assert status == 200

    def test_cross_site_without_origin_is_forbidden(self) -> None:
        status, body = _mutation(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            COOKIE,
            (b"sec-fetch-site", b"cross-site"),
        )
        assert status == 403
        assert body == FORBIDDEN_BODY

    def test_cross_site_with_disallowed_origin_is_forbidden(self) -> None:
        status, _ = _mutation(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            COOKIE,
            (b"sec-fetch-site", b"cross-site"),
            ORIGIN_BAD,
        )
        assert status == 403

    def test_cross_site_with_allowlisted_origin_is_allowed(self) -> None:
        status, _ = _mutation(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            COOKIE,
            (b"sec-fetch-site", b"cross-site"),
            ORIGIN_OK,
        )
        assert status == 200

    def test_unrecognized_fetch_site_falls_back_to_origin(self) -> None:
        app = _protect(allowed_origins=ALLOWED_ORIGINS)
        status, _ = _mutation(app, COOKIE, (b"sec-fetch-site", b"garbage"), ORIGIN_OK)
        assert status == 200
        status, body = _mutation(app, COOKIE, (b"sec-fetch-site", b"garbage"))
        assert status == 403
        assert body == FORBIDDEN_BODY


class TestCredentialedGetQueries:
    """The regression class CSRF exists for: ambient GETs carry cookies."""

    def test_get_with_cookie_and_no_origin_is_forbidden(self) -> None:
        status, body = _request(_protect(allowed_origins=ALLOWED_ORIGINS), headers=[COOKIE])
        assert status == 403
        assert body == FORBIDDEN_BODY

    def test_get_with_cookie_and_allowlisted_origin_is_allowed(self) -> None:
        status, body = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS), headers=[COOKIE, ORIGIN_OK]
        )
        assert status == 200
        assert body == {"result": {"data": "pong"}}

    def test_get_with_cookie_and_cross_site_fetch_is_forbidden(self) -> None:
        # The modern-browser shape of <img src>: fetch metadata present,
        # no Origin.
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            headers=[COOKIE, (b"sec-fetch-site", b"cross-site")],
        )
        assert status == 403

    def test_get_with_cookie_and_safe_fetch_site_is_allowed(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS),
            headers=[COOKIE, (b"sec-fetch-site", b"same-site")],
        )
        assert status == 200


class TestAllowMissingOrigin:
    def test_flag_passes_headerless_credentialed_request(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS, allow_missing_origin=True),
            headers=[COOKIE],
        )
        assert status == 200

    def test_flag_never_rescues_declared_cross_site(self) -> None:
        # cross-site ALWAYS requires an allowlisted Origin — no
        # fallback-to-missing.
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS, allow_missing_origin=True),
            headers=[COOKIE, (b"sec-fetch-site", b"cross-site")],
        )
        assert status == 403

    def test_flag_still_rejects_mismatched_origin(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS, allow_missing_origin=True),
            headers=[COOKIE, ORIGIN_BAD],
        )
        assert status == 403


class TestWildcardOrigins:
    def test_wildcard_allows_any_present_origin(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=("*",)),
            headers=[COOKIE, (b"origin", b"https://anything.example")],
        )
        assert status == 200

    def test_wildcard_still_rejects_declared_cross_site(self) -> None:
        # "*" is not an exact Origin; a request that declares itself
        # cross-site is rejected anyway — wildcard keeps the fetch-metadata
        # protection instead of disabling it.
        status, _ = _request(
            _protect(allowed_origins=("*",)),
            headers=[
                COOKIE,
                (b"sec-fetch-site", b"cross-site"),
                (b"origin", b"https://anything.example"),
            ],
        )
        assert status == 403

    def test_wildcard_still_requires_a_signal(self) -> None:
        # "*" accepts any Origin but the request still has to send one (or a
        # safe Sec-Fetch-Site); a header-less credentialed request fails.
        status, _ = _request(_protect(allowed_origins=("*",)), headers=[COOKIE])
        assert status == 403


class TestAuthorizationHeader:
    """API clients send Authorization but rarely Origin or fetch metadata."""

    def test_authorization_header_triggers_the_check(self) -> None:
        status, body = _request(_protect(allowed_origins=ALLOWED_ORIGINS), headers=[AUTHORIZATION])
        assert status == 403
        assert body == FORBIDDEN_BODY

    def test_authorization_with_allowlisted_origin(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS), headers=[AUTHORIZATION, ORIGIN_OK]
        )
        assert status == 200

    def test_authorization_api_client_needs_missing_origin_flag(self) -> None:
        status, _ = _request(
            _protect(allowed_origins=ALLOWED_ORIGINS, allow_missing_origin=True),
            headers=[AUTHORIZATION],
        )
        assert status == 200


class TestScopePassThrough:
    def test_websocket_scope_passes_through(self) -> None:
        seen: list[str] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            seen.append(scope["type"])

        app = csrf_protect(inner, allowed_origins=ALLOWED_ORIGINS)
        asyncio.run(app({"type": "websocket", "headers": [COOKIE]}, _receive(b""), _noop_send()))
        assert seen == ["websocket"]

    def test_lifespan_scope_passes_through(self) -> None:
        seen: list[str] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            seen.append(scope["type"])

        app = csrf_protect(inner, allowed_origins=ALLOWED_ORIGINS)
        asyncio.run(app({"type": "lifespan", "headers": [COOKIE]}, _receive(b""), _noop_send()))
        assert seen == ["lifespan"]

    def test_missing_headers_key_treated_as_no_headers(self) -> None:
        async def inner(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {"type": "http", "method": "GET", "path": "/x"}
        asyncio.run(csrf_protect(inner)(scope, _receive(b""), send))
        assert sent[0]["status"] == 204


def _noop_send() -> Callable[[dict[str, Any]], Any]:
    async def send(message: dict[str, Any]) -> None:
        pass

    return send


class TestThroughAdapters:
    def test_test_client_cookie_without_origin_is_forbidden(self) -> None:
        client = TestClient(_protect(allowed_origins=ALLOWED_ORIGINS))
        response = client.get("/trpc/ping", headers={"cookie": "session=abc"})
        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY

    def test_test_client_cookie_with_allowlisted_origin(self) -> None:
        client = TestClient(_protect(allowed_origins=ALLOWED_ORIGINS))
        response = client.get(
            "/trpc/ping",
            headers={"cookie": "session=abc", "origin": "https://app.example.com"},
        )
        assert response.status_code == 200
        assert response.json() == {"result": {"data": "pong"}}

    def test_wsgi_from_asgi_bridge(self) -> None:
        from werkzeug.test import Client
        from werkzeug.wrappers import Response

        app = wsgi_from_asgi(_protect(allowed_origins=ALLOWED_ORIGINS))
        client = Client(app, Response)
        # werkzeug owns Cookie via its jar; a literal "cookie" header is
        # stripped from `headers=`.
        client.set_cookie("session", "abc", domain="localhost")

        forbidden = client.get("/trpc/ping")
        assert forbidden.status_code == 403
        assert forbidden.get_json() == FORBIDDEN_BODY

        allowed = client.get("/trpc/ping", headers={"origin": "https://app.example.com"})
        assert allowed.status_code == 200
        assert allowed.get_json() == {"result": {"data": "pong"}}
