"""CORS allowlist middleware for ASGI apps (MISSION section 7, ADR 0011).

`cors` wraps any ASGI app — most usefully the app returned by `mount`,
`attach`, or `mount_rest` — adding deny-by-default Cross-Origin Resource
Sharing: with no explicit allowlist no `Access-Control-*` header is ever
emitted and every preflight is refused with `403`. Because the WSGI and
Django adapters run through the same ASGI app (ADR 0010), one
implementation covers every adapter — `wsgi_from_asgi(cors(mount(...)))`,
`asgi_view(cors(...))`.

This is an ASGI middleware *wrapper*, not procedure middleware: CORS must
answer preflights before dispatch and cover REST routes, so the
`middleware=` chain is the wrong layer for it. See `docs/cors.md`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .asgi import ASGIApp, Message, Receive, Scope, Send

__all__ = ["cors"]

_PREFLIGHT_VARY = b"Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
"""`Vary` value on preflight responses: the answer depends on all three."""


def _names(value: Iterable[str]) -> tuple[str, ...]:
    """Normalize an option accepting either an iterable or one bare string.

    Args:
        value: Configured names; a single `str` is treated as one name, not
            iterated character by character.

    Returns:
        The names as a tuple.

    Example:
        ```python
        from trpc_rs_py.cors import _names

        assert _names("https://a.com") == ("https://a.com",)
        assert _names(["a", "b"]) == ("a", "b")
        ```
    """
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """Return the first header value matching `name` (case-insensitive).

    Args:
        headers: ASGI header pairs (lowercase keys per the ASGI spec).
        name: Header name to find, lowercase bytes.

    Returns:
        The header value, or `None` when absent.

    Example:
        ```python
        from trpc_rs_py.cors import _header

        assert _header([(b"origin", b"https://a.com")], b"origin") == b"https://a.com"
        ```
    """
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


def _merge_vary(headers: list[tuple[bytes, bytes]], value: bytes) -> list[tuple[bytes, bytes]]:
    """Add `value` tokens to the `Vary` response header without duplicating.

    Args:
        headers: Response headers so far (mutated-free: a new list is
            returned).
        value: Comma-separated tokens to guarantee in `Vary`, e.g.
            `b"Origin"`.

    Returns:
        The headers with `Vary` merged or appended.

    Example:
        ```python
        from trpc_rs_py.cors import _merge_vary

        out = _merge_vary([(b"vary", b"Accept-Encoding")], b"Origin")
        assert out == [(b"vary", b"Accept-Encoding, Origin")]
        ```
    """
    tokens = [token.strip() for token in value.split(b",") if token.strip()]
    for index, (name, existing) in enumerate(headers):
        if name.lower() != b"vary":
            continue
        present = {token.strip().lower() for token in existing.split(b",")}
        missing = [token for token in tokens if token.lower() not in present]
        if missing:
            headers[index] = (name, existing + b", " + b", ".join(missing))
        return headers
    if tokens:
        headers.append((b"vary", value))
    return headers


def cors(
    app: ASGIApp,
    *,
    allow_origins: Iterable[str] = (),
    allow_methods: Iterable[str] = ("GET", "POST", "OPTIONS"),
    allow_headers: Iterable[str] = ("content-type",),
    expose_headers: Iterable[str] = (),
    allow_credentials: bool = False,
    max_age: int = 600,
) -> ASGIApp:
    """Wrap an ASGI app with deny-by-default CORS handling.

    Preflight requests (`OPTIONS` carrying both `Origin` and
    `Access-Control-Request-Method`) are answered directly — `204` with the
    allow headers for an allowed origin, `403` otherwise — and never reach
    the wrapped app. Actual requests pass through; when the request's
    `Origin` is allowed the response gains `Access-Control-Allow-Origin`
    plus `Vary: Origin`, credentials, and expose-headers as configured.
    Disallowed origins get the app's response unchanged: the browser is
    what enforces the denial.

    Origin matching is an exact string comparison against the allowlist —
    `https://a.com` and `http://a.com` are different origins. Non-`http`
    scopes (`websocket`, `lifespan`) pass through untouched, and response
    bodies are never buffered, so SSE subscriptions stream normally.

    Args:
        app: The ASGI application to wrap.
        allow_origins: Origins allowed to make cross-origin requests. A
            bare `str` is one origin; the entry `"*"` allows any origin.
            Empty (the default) emits no CORS headers and refuses all
            preflights — deny by default.
        allow_methods: Methods listed in `Access-Control-Allow-Methods` on
            preflight responses.
        allow_headers: Header names listed in `Access-Control-Allow-Headers`
            on preflight responses; the entry `"*"` echoes the request's
            `Access-Control-Request-Headers` instead.
        expose_headers: Header names listed in
            `Access-Control-Expose-Headers` on actual responses.
        allow_credentials: When `True`, emit
            `Access-Control-Allow-Credentials: true`. With
            `allow_origins=["*"]` the request's concrete origin is
            reflected instead of `*` — the CORS spec forbids `*` with
            credentials.
        max_age: `Access-Control-Max-Age` value in seconds on preflight
            responses.

    Returns:
        An ASGI application wrapping `app`.

    Example:
        ```python
        from trpc_rs_py import Router, mount
        from trpc_rs_py.cors import cors

        router = Router()
        app = cors(mount(router), allow_origins=["https://app.example.com"])
        # or through the WSGI bridge: wsgi_from_asgi(cors(mount(router), ...))
        ```
    """
    origins = frozenset(_names(allow_origins))
    methods = ", ".join(_names(allow_methods)).encode("latin-1")
    headers_configured = ", ".join(_names(allow_headers)).encode("latin-1")
    headers_any = "*" in _names(allow_headers)
    exposed = ", ".join(_names(expose_headers)).encode("latin-1")
    max_age_bytes = str(max_age).encode("ascii")
    allow_any = "*" in origins

    def allowed_origin(origin: bytes) -> bytes | None:
        """Map a request `Origin` to the `Access-Control-Allow-Origin` value.

        Returns `None` when the origin is not allowed. Exact string match
        against the allowlist; `"*"` either emits literally or, when
        credentials are on, reflects the request's concrete origin.
        """
        if allow_any:
            return origin if allow_credentials else b"*"
        return origin if origin.decode("latin-1") in origins else None

    async def preflight(send: Send, origin: bytes, requested_headers: bytes | None) -> None:
        """Answer a CORS preflight without touching the wrapped app."""
        allow_origin = allowed_origin(origin)
        if allow_origin is None:
            payload = b"Forbidden"
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        response_headers = [
            (b"access-control-allow-origin", allow_origin),
            (b"access-control-allow-methods", methods),
            (b"access-control-max-age", max_age_bytes),
            (b"vary", _PREFLIGHT_VARY),
        ]
        if headers_any:
            response_headers.append((b"access-control-allow-headers", requested_headers or b"*"))
        elif headers_configured:
            response_headers.append((b"access-control-allow-headers", headers_configured))
        if allow_credentials:
            response_headers.append((b"access-control-allow-credentials", b"true"))
        await send({"type": "http.response.start", "status": 204, "headers": response_headers})
        await send({"type": "http.response.body", "body": b""})

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI request, decorating or refusing per the policy."""
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return
        request_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        origin = _header(request_headers, b"origin")
        if scope.get("method") == "OPTIONS":
            # A preflight is OPTIONS + Origin + Access-Control-Request-Method;
            # anything else is a plain OPTIONS the app answers itself.
            if (
                origin is not None
                and _header(request_headers, b"access-control-request-method") is not None
            ):
                await preflight(
                    send,
                    origin,
                    _header(request_headers, b"access-control-request-headers"),
                )
            else:
                await app(scope, receive, send)
            return
        if origin is None:
            await app(scope, receive, send)
            return
        allow_origin = allowed_origin(origin)
        if allow_origin is None:
            await app(scope, receive, send)
            return

        async def cors_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", allow_origin))
                if allow_credentials:
                    headers.append((b"access-control-allow-credentials", b"true"))
                if exposed:
                    headers.append((b"access-control-expose-headers", exposed))
                headers = _merge_vary(headers, b"Origin")
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, cors_send)

    return wrapped
