"""Per-request context: `RequestContext`, `context_factory`, and `ctx` injection.

Every adapter entry point (`mount`, `attach`, `mount_wsgi`, `attach_flask`,
`trpc_view`, `trpc_view_async`, `mount_rest`) accepts an optional
`context_factory`. It is called once per HTTP request with a
`RequestContext` and returns whatever object the application wants handlers
to see — a dict, a dataclass, or the `RequestContext` itself (the default).

A handler opts in by naming its first parameter `ctx`; the parameter is
excluded from input validation, REST/OpenAPI parameter mapping, `codegen
ts`, and `codegen zod`, and receives the context object at call time:

    ```python
    from trpc_rs_py import RequestContext, Router, mount

    router = Router()

    @router.query
    def me(ctx, user_id: int) -> dict:
        assert isinstance(ctx.user, str)
        return {"id": user_id, "name": ctx.user}

    def context_factory(request: RequestContext) -> RequestContext:
        request.user = request.headers.get("x-user", "anonymous")
        return request

    app = mount(router, context_factory=context_factory)
    ```
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

__all__ = [
    "ContextFactory",
    "RequestContext",
    "ctx_injection",
    "inject_ctx",
    "request_context_from_scope",
]

ContextFactory: TypeAlias = Callable[["RequestContext"], Any]
"""Callable producing the `ctx` object handlers receive.

Called once per HTTP request — not per batched call — with the
`RequestContext`; the return value may itself be a `RequestContext`, a dict,
or any application type. Async factories are awaited by the adapters.
"""

_SCOPE_REQUEST_KEY = "trpc.request"
"""Scope key carrying the framework-native request object.

The WSGI and Django adapters store the raw `environ` / `HttpRequest` here so
`RequestContext.request` is the object users of that framework expect
instead of the translated ASGI scope.
"""


@dataclass
class RequestContext:
    """Uniform per-request information handed to `context_factory`.

    Attributes:
        method: HTTP method (`"GET"`, `"POST"`, ...).
        path: Request path as routed by the framework (`"/trpc/greet"`).
        query_string: Raw query string, no leading `?`.
        headers: Lower-cased request headers; the first occurrence wins when
            a header is repeated (use `raw_headers` for multi-values).
        raw_headers: `(name, value)` pairs in wire order, lower-cased.
        client: `(host, port)` of the peer when the server supplied it.
        request: Framework-native request object — the ASGI scope dict under
            `mount()`/`attach()`, the WSGI `environ` under `mount_wsgi()`/
            `attach_flask()`, the `django.http.HttpRequest` under
            `trpc_view`/`trpc_view_async`.
        user: Free slot for application/middleware data (`None` default).

    Example:
        ```python
        from trpc_rs_py.context import RequestContext

        def factory(request: RequestContext) -> RequestContext:
            request.user = request.headers.get("x-user", "anonymous")
            return request
        ```
    """

    method: str
    path: str
    query_string: str
    headers: Mapping[str, str]
    raw_headers: tuple[tuple[str, str], ...]
    client: tuple[str, int] | None
    request: Any
    user: Any = None


def request_context_from_scope(scope: Mapping[str, Any]) -> RequestContext:
    """Build the `RequestContext` for one ASGI `http` scope.

    Args:
        scope: The ASGI connection scope.

    Returns:
        The populated `RequestContext`.

    Example:
        ```python
        from trpc_rs_py.context import request_context_from_scope

        scope = {"type": "http", "method": "GET", "path": "/trpc/greet",
                 "query_string": b"a=1", "headers": [(b"host", b"x")]}
        ctx = request_context_from_scope(scope)
        assert ctx.headers["host"] == "x"
        ```
    """
    raw: list[tuple[str, str]] = []
    headers: dict[str, str] = {}
    for name_bytes, value_bytes in scope.get("headers", []):
        name = name_bytes.decode("latin-1").lower()
        value = value_bytes.decode("latin-1")
        raw.append((name, value))
        headers.setdefault(name, value)
    client_raw = scope.get("client")
    client = (str(client_raw[0]), int(client_raw[1])) if client_raw else None
    return RequestContext(
        method=str(scope.get("method", "GET")),
        path=str(scope.get("path", "")),
        query_string=(scope.get("query_string", b"") or b"").decode("latin-1"),
        headers=headers,
        raw_headers=tuple(raw),
        client=client,
        request=scope.get(_SCOPE_REQUEST_KEY, scope),
    )


_CTX_INJECTION_CACHE_MAXSIZE = 512
"""Bound on the `ctx_injection` signature-inspection cache."""

_ctx_injection_cache: dict[Callable[..., Any], Literal["prepend", "kwarg"] | None] = {}


def ctx_injection(handler: Callable[..., Any]) -> Literal["prepend", "kwarg"] | None:
    """Return how `ctx` must be injected for `handler`, or `None`.

    Only the *first* declared parameter named `ctx` opts in. A positional
    `ctx` is satisfied by prepending it to the positional arguments
    (`"prepend"`); a keyword-only `ctx` — possible after `*args` or `*` — is
    satisfied as a keyword argument (`"kwarg"`). A `ctx` anywhere else is an
    ordinary input parameter. Results are cached so signature inspection
    stays out of the request hot path.

    Args:
        handler: The procedure handler.

    Returns:
        `"prepend"`, `"kwarg"`, or `None` when the handler takes no injected
        `ctx` or its signature cannot be inspected.

    Example:
        ```python
        from trpc_rs_py.context import ctx_injection

        def a(ctx, name: str) -> str: ...
        def b(name: str) -> str: ...
        def c(*, ctx) -> None: ...

        assert ctx_injection(a) == "prepend"
        assert ctx_injection(b) is None
        assert ctx_injection(c) == "kwarg"
        ```
    """
    try:
        return _ctx_injection_cache[handler]
    except (KeyError, TypeError):
        pass
    result = _inspect_ctx_injection(handler)
    try:
        if len(_ctx_injection_cache) < _CTX_INJECTION_CACHE_MAXSIZE:
            _ctx_injection_cache[handler] = result
    except TypeError:
        pass
    return result


def _inspect_ctx_injection(handler: Callable[..., Any]) -> Literal["prepend", "kwarg"] | None:
    """Inspect `handler`'s signature for a leading `ctx` parameter."""
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return None
    if not parameters or parameters[0].name != "ctx":
        return None
    return "prepend" if parameters[0].kind is not inspect.Parameter.KEYWORD_ONLY else "kwarg"


def inject_ctx(
    injection: Literal["prepend", "kwarg"] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    ctx: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Apply `ctx_injection` to a validator-produced `(args, kwargs)` pair.

    Args:
        injection: The mode from `ctx_injection`.
        args: Positional arguments built for the input parameters.
        kwargs: Keyword arguments built for the input parameters.
        ctx: The context object to inject.

    Returns:
        The `(args, kwargs)` pair to invoke the handler with.

    Example:
        ```python
        from trpc_rs_py.context import inject_ctx

        assert inject_ctx("prepend", (1,), {"a": 2}, "CTX") == (("CTX", 1), {"a": 2})
        assert inject_ctx("kwarg", (), {"a": 2}, "CTX") == ((), {"a": 2, "ctx": "CTX"})
        ```
    """
    if injection == "prepend":
        return (ctx, *args), kwargs
    if injection == "kwarg":
        return args, {**kwargs, "ctx": ctx}
    return args, kwargs


def strip_ctx_parameter(signature: inspect.Signature | None) -> inspect.Signature | None:
    """Return `signature` without a leading `ctx` parameter, if present.

    Used by signature-binding pre-checks so the injected `ctx` is not
    confused with client input.

    Args:
        signature: The handler's signature, or `None` when uninspectable.

    Returns:
        The signature minus a leading `ctx` parameter, or `None`.

    Example:
        ```python
        import inspect

        from trpc_rs_py.context import strip_ctx_parameter

        def f(ctx, name: str) -> str: ...

        stripped = strip_ctx_parameter(inspect.signature(f))
        assert list(stripped.parameters) == ["name"]
        ```
    """
    if signature is None:
        return None
    parameters = list(signature.parameters.values())
    if parameters and parameters[0].name == "ctx":
        parameters = parameters[1:]
    return signature.replace(parameters=parameters)
