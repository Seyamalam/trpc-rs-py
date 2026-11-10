"""Adapter-level CSRF protection for credential-carrying requests (ADR 0011).

`csrf_protect` wraps any ASGI app and rejects forged cross-site requests
*before* dispatch — it is an app wrapper, not procedure middleware, because
the check must also cover REST routes and anything else served by the same
app. Wrap the mounted app (or `mount_rest` output) and the same filter
protects every adapter through the WSGI/Django bridges (ADR 0010):

    ```python
    from trpc_rs_py import Router, mount
    from trpc_rs_py.csrf import csrf_protect

    app = csrf_protect(
        mount(router), allowed_origins=["https://app.example.com"]
    )
    ```

Threat model: tRPC mutations are POST + `application/json`, which a
cross-site page cannot send without a CORS preflight — so mutations are
already protected when CORS is deny-by-default. Queries, however, run over
GET, and a cross-site `<img>`, `<form>`, or navigation GET carries ambient
cookies. If queries read credential-dependent state (and `ctx` injection
exists precisely so they can), a forged GET reads data into an attacker
context. The check therefore keys on *credentials*, not method: a request
is checked when it carries a `Cookie` or `Authorization` header, for any
HTTP method. Uncredentialed requests pass through untouched — they cannot
leak session-bound data because there is no session to abuse.

Decision rules for a *checked* (credentialed) request:

1. `Sec-Fetch-Site` is `same-origin`, `same-site`, or `none` (direct
   navigation, user-typed URL) → **allow**.
2. `Sec-Fetch-Site` is `cross-site` → **allow** only when `Origin` is
   present and *exactly* matches an entry of `allowed_origins`. The `"*"`
   wildcard never rescues a declared-cross-site request, and
   `allow_missing_origin` does not apply — no fallback-to-missing.
3. `Sec-Fetch-Site` is absent or carries an unrecognized value → the
   `Origin` header decides: present and matching `allowed_origins` (or
   `"*"` configured) → **allow**; absent → `allow_missing_origin`
   (default `False`) decides.
4. Everything else → **reject** with `403` and a fixed JSON body.

`Sec-Fetch-Site` values are compared case-insensitively (browsers send
lowercase tokens); `Origin` is an exact byte-string match — scheme,
host, and port all matter, nothing is normalized away.

Non-`http` scopes (`websocket`, `lifespan`) pass through untouched:
websockets have their own origin-check story, and no request reaches
dispatch on a lifespan scope.
"""

from __future__ import annotations

from collections.abc import Iterable

from .asgi import ASGIApp, Receive, Scope, Send

__all__ = ["csrf_protect"]

_SAFE_FETCH_SITES: frozenset[str] = frozenset({"same-origin", "same-site", "none"})
"""`Sec-Fetch-Site` values that always permit a credentialed request."""

_CREDENTIAL_HEADERS: frozenset[bytes] = frozenset({b"cookie", b"authorization"})
"""Header names that mark a request as credential-carrying."""

_FORBIDDEN_BODY: bytes = b'{"error":{"code":"FORBIDDEN","message":"CSRF check failed"}}'
"""Fixed rejection body — the REST `{"error": {"code", "message"}}` shape."""


def csrf_protect(
    app: ASGIApp,
    *,
    allowed_origins: Iterable[str] = (),
    allow_missing_origin: bool = False,
) -> ASGIApp:
    """Wrap `app` with a CSRF check on credential-carrying HTTP requests.

    The filter runs before dispatch and covers every route the wrapped app
    serves — tRPC procedures, REST routes, and any other endpoints. See the
    module docstring for the threat model and the exact decision rules.

    Args:
        app: The ASGI app to protect, e.g. `mount(router)` or
            `mount_rest(router)`.
        allowed_origins: Exact `Origin` header values to allow for checked
            requests whose `Sec-Fetch-Site` does not already permit them
            (for example `"https://app.example.com"` — the scheme matters).
            A bare `str` is treated as one origin. `"*"` accepts any
            *present* `Origin` but never rescues a request that declares
            `Sec-Fetch-Site: cross-site`.
        allow_missing_origin: When `True`, a checked request carrying
            neither `Sec-Fetch-Site` nor `Origin` is allowed through — the
            posture of older browsers and non-browser API clients. This
            weakens the check on exactly the header-less requests a simple
            cross-site `<img>`/form produces on legacy clients; prefer
            listing `allowed_origins`. Never applies to requests declaring
            `Sec-Fetch-Site: cross-site`.

    Returns:
        An ASGI app rejecting forged credentialed requests with
        `403 {"error":{"code":"FORBIDDEN","message":"CSRF check failed"}}`
        and forwarding everything else to `app` unchanged.

    Example:
        ```python
        from trpc_rs_py import Router, mount
        from trpc_rs_py.csrf import csrf_protect

        router = Router()
        app = csrf_protect(
            mount(router), allowed_origins=["https://app.example.com"]
        )
        # Requests carrying cookies now need a same-site/none
        # Sec-Fetch-Site or an Origin of https://app.example.com.
        ```
    """
    allowed = frozenset((allowed_origins,) if isinstance(allowed_origins, str) else allowed_origins)

    async def protected(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _request_allowed(
            scope.get("headers", ()), allowed, allow_missing_origin
        ):
            await app(scope, receive, send)
            return
        await _send_forbidden(send)

    return protected


def _request_allowed(
    headers: Iterable[tuple[bytes, bytes]],
    allowed: frozenset[str],
    allow_missing_origin: bool,
) -> bool:
    """Apply the module-level decision rules to one request's headers.

    Args:
        headers: ASGI header pairs (lowercase names per the ASGI spec).
        allowed: The `allowed_origins` allowlist as a set.
        allow_missing_origin: Forwarded from `csrf_protect`.

    Returns:
        `True` when the request may proceed to dispatch.
    """
    credentialed = False
    fetch_site: str | None = None
    origin: str | None = None
    for name, value in headers:
        key = name.lower()
        if key in _CREDENTIAL_HEADERS:
            if value.strip():
                credentialed = True
        elif key == b"sec-fetch-site" and fetch_site is None:
            fetch_site = value.decode("latin-1").lower()
        elif key == b"origin" and origin is None:
            origin = value.decode("latin-1")
    if not credentialed:
        return True
    if fetch_site in _SAFE_FETCH_SITES:
        return True
    if fetch_site == "cross-site":
        return origin is not None and origin in allowed
    if origin is not None:
        return origin in allowed or "*" in allowed
    return allow_missing_origin


async def _send_forbidden(send: Send) -> None:
    """Send the fixed 403 rejection response.

    Args:
        send: ASGI send callable.
    """
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_FORBIDDEN_BODY)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _FORBIDDEN_BODY})
