"""tRPC-style procedure middleware (ADR 0011).

A middleware is a callable `(call, next) -> result` wrapping one procedure
invocation — input already validated, output not yet. It sees the
procedure's `path`, `kind`, raw decoded `input`, and the `ctx` object, and
may inspect, short-circuit, raise (`TRPCError` keeps its code), or replace
the context downstream sees via `await next(ctx=...)`.

Register per router — `Router(middleware=[...])` or `router.use(fn)` —
which applies to procedures *registered afterwards* (the chain is
snapshotted onto each `Procedure` at decoration time, so merged routers
keep their own), and per mount (`mount(..., middleware=[...])`), which
wraps every procedure through that mount outermost-first:

    ```python
    from trpc_rs_py import Router, mount
    from trpc_rs_py.middleware import logging_middleware, timing_middleware

    router = Router(middleware=[logging_middleware()])
    app = mount(router, middleware=[timing_middleware()])
    ```

Subscriptions run the chain once at subscription start (the handler's
async generator is the chain's result); the per-event path is not
re-entered. REST routes through `mount_rest` run the same chain.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import threading
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

from .errors import TRPCError, TRPCErrorCode

if TYPE_CHECKING:
    from .context import RequestContext
    from .router import ProcedureKind

__all__ = [
    "Call",
    "Middleware",
    "Next",
    "logging_middleware",
    "rate_limit",
    "require_auth",
    "run_middleware",
    "timing_middleware",
    "tracing_middleware",
]

_UNSET: Any = object()
"""Sentinel distinguishing `next()` from `next(ctx=None)`."""


class Next(Protocol):
    """The `next` callable handed to a middleware.

    `await next()` runs the rest of the chain and the handler, returning
    the handler's result; `await next(ctx=obj)` replaces the context object
    downstream middleware and the handler see.
    """

    def __call__(self, *, ctx: Any = _UNSET) -> Awaitable[Any]:
        """Run the remaining chain, optionally swapping `ctx`."""
        ...


Middleware: TypeAlias = Callable[["Call", Next], Any]
"""Callable wrapping one procedure call; may be sync or async.

Signature: `(call: Call, next: Next) -> Any`. Returning without calling
`next` short-circuits the handler (the value becomes the result); raising
`TRPCError` produces the normal error envelope with its code intact.
"""


@dataclass(frozen=True)
class Call:
    """Frozen view of one procedure invocation handed to middleware.

    Attributes:
        path: Full dotted procedure path (`posts.by_id`).
        kind: `ProcedureKind` of the procedure.
        input: Raw decoded JSON input (`None` when absent).
        ctx: The context object the handler will receive unless a
            middleware replaces it through `next(ctx=...)`.
        request_context: The `RequestContext` for the HTTP request, or
            `None` on paths that never built one (direct unit calls).
        extra: Scratch space for middleware-to-middleware data.
    """

    path: str
    kind: ProcedureKind
    input: Any
    ctx: Any
    request_context: RequestContext | None = None
    extra: dict[str, Any] = field(default_factory=lambda: {})


async def run_middleware(
    middlewares: tuple[Middleware, ...],
    call: Call,
    terminal: Callable[[Call], Awaitable[Any]],
) -> Any:
    """Run `middlewares` around `terminal` and return the chain's result.

    Args:
        middlewares: The chain, outermost first.
        call: The invocation view for the first middleware.
        terminal: Async callable producing the handler's result from the
            (possibly ctx-swapped) `Call` that reaches the innermost layer.

    Returns:
        Whatever the chain yields — normally the handler's result.
    """

    async def invoke(index: int, current: Call) -> Any:
        if index == len(middlewares):
            return await terminal(current)
        layer = middlewares[index]

        async def nxt(*, ctx: Any = _UNSET) -> Any:
            downstream = current if ctx is _UNSET else replace(current, ctx=ctx)
            return await invoke(index + 1, downstream)

        result = layer(current, nxt)
        if inspect.isawaitable(result):
            result = await result
        return result

    return await invoke(0, call)


def logging_middleware(
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
) -> Middleware:
    """Log `path`, `kind`, duration, and outcome for every call.

    Args:
        logger: Logger to write to; defaults to `trpc_rs_py.middleware`.
        level: `logging` level for the records (INFO default).

    Returns:
        A middleware emitting one record per call.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.middleware import logging_middleware

        router = Router(middleware=[logging_middleware()])
        ```
    """
    log = logger if logger is not None else logging.getLogger("trpc_rs_py.middleware")

    async def middleware(call: Call, next: Next) -> Any:
        start = time.perf_counter()
        try:
            result = await next()
        except Exception as error:
            log.log(
                level,
                "trpc %s (%s) failed in %.2fms: %s",
                call.path,
                call.kind.value,
                (time.perf_counter() - start) * 1000,
                type(error).__name__,
            )
            raise
        log.log(
            level,
            "trpc %s (%s) ok in %.2fms",
            call.path,
            call.kind.value,
            (time.perf_counter() - start) * 1000,
        )
        return result

    return middleware


def timing_middleware(
    sink: Callable[[Call, float], Any] | None = None,
) -> Middleware:
    """Record each call's duration in milliseconds.

    Args:
        sink: Optional `(call, ms)` callback. Default writes
            `ctx["trpc_duration_ms"]` for a `MutableMapping` ctx or the
            `trpc_duration_ms` attribute otherwise.

    Returns:
        A middleware recording wall-clock duration per call.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.middleware import timing_middleware

        router = Router(middleware=[timing_middleware()])
        ```
    """

    def default_sink(call: Call, ms: float) -> None:
        ctx: Any = call.ctx
        if isinstance(ctx, MutableMapping):
            cast("MutableMapping[str, Any]", ctx)["trpc_duration_ms"] = ms
        else:
            with contextlib.suppress(AttributeError, TypeError):
                ctx.trpc_duration_ms = ms

    record = sink if sink is not None else default_sink

    async def middleware(call: Call, next: Next) -> Any:
        start = time.perf_counter()
        try:
            return await next()
        finally:
            record(call, (time.perf_counter() - start) * 1000)

    return middleware


def tracing_middleware(tracer: Any = None) -> Middleware:
    """Open a tracing span around every call (OpenTelemetry).

    Each invocation runs inside a `trpc.<path>` span carrying
    `trpc.path` and `trpc.kind` attributes. On error the span records
    the exception and is marked `ERROR` before the error re-raises, so
    the tRPC error envelope is unchanged.

    Requires the optional `opentelemetry-api` package
    (`pip install opentelemetry-api`); constructing the middleware
    raises `RuntimeError` when it is not installed. Pass any object
    implementing `start_as_current_span` as `tracer` — a real
    `opentelemetry.trace.Tracer`, a testing tracer, or a stub.

    Args:
        tracer: Tracer to open spans on. Defaults to
            `opentelemetry.trace.get_tracer("trpc_rs_py")`.

    Returns:
        A middleware opening one span per call.

    Raises:
        RuntimeError: If `opentelemetry-api` is not installed.

    Example:
        ```python
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        trace.set_tracer_provider(TracerProvider())

        from trpc_rs_py import Router
        from trpc_rs_py.middleware import tracing_middleware

        router = Router(middleware=[tracing_middleware()])
        # Or your own tracer: tracing_middleware(trace.get_tracer("my-app"))
        ```
    """
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
    except ImportError as error:
        msg = (
            "tracing_middleware requires the optional 'opentelemetry-api' "
            "package — install it to use tracing"
        )
        raise RuntimeError(msg) from error

    active_tracer = trace.get_tracer("trpc_rs_py") if tracer is None else tracer

    async def middleware(call: Call, next: Next) -> Any:
        with active_tracer.start_as_current_span(f"trpc.{call.path}") as span:
            span.set_attribute("trpc.path", call.path)
            span.set_attribute("trpc.kind", call.kind.value)
            try:
                return await next()
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR))
                raise

    return middleware


def _default_rate_limit_key(call: Call) -> str:
    """Bucket key for `rate_limit`: the client IP, or `"-"` without one."""
    request_context = call.request_context
    if request_context is not None and request_context.client:
        return request_context.client[0]
    return "-"


@dataclass
class _TokenBucket:
    """Mutable token-bucket state for one `rate_limit` key.

    Attributes:
        tokens: Current token count (fractional; refilled lazily).
        updated: `time.monotonic()` timestamp of the last access, used for
            idle eviction.
    """

    tokens: float
    updated: float


def rate_limit(
    requests_per_minute: float,
    key: Callable[[Call], str] | None = None,
) -> Middleware:
    """Limit calls per key with an in-process token bucket.

    Each distinct key gets its own bucket: capacity `requests_per_minute`
    (burst), refilled at `requests_per_minute / 60` tokens per second on a
    `time.monotonic()` clock, one token per call. An empty bucket raises
    `TRPCError` with code `TOO_MANY_REQUESTS` (HTTP 429).

    Buckets idle longer than ten full-refill times are evicted lazily on
    access, so untrusted keys cannot grow memory without bound. State is
    guarded by a `threading.Lock` — safe on event loops and WSGI worker
    threads alike. This is per-process: run multiple workers and each
    enforces its own limit.

    Args:
        requests_per_minute: Burst capacity and sustained rate per minute;
            must be `> 0`.
        key: Bucket key extractor, called per invocation. Defaults to the
            client IP from `call.request_context.client`, falling back to a
            shared `"-"` bucket when no client address is available (e.g.
            direct unit calls), so those calls are still limited.

    Returns:
        A middleware enforcing the limit.

    Raises:
        ValueError: If `requests_per_minute` is not positive.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.middleware import rate_limit

        router = Router(middleware=[rate_limit(120)])
        # Per-user limit instead of per-IP:
        # rate_limit(30, key=lambda call: call.ctx["user_id"])
        ```
    """
    if requests_per_minute <= 0:
        msg = f"requests_per_minute must be > 0, got {requests_per_minute}"
        raise ValueError(msg)
    capacity = float(requests_per_minute)
    refill_per_second = capacity / 60.0
    idle_ttl = 10.0 * capacity / refill_per_second
    lock = threading.Lock()
    buckets: dict[str, _TokenBucket] = {}
    resolve_key = key if key is not None else _default_rate_limit_key

    def take(bucket_key: str) -> bool:
        now = time.monotonic()
        with lock:
            stale = [name for name, b in buckets.items() if now - b.updated > idle_ttl]
            for name in stale:
                del buckets[name]
            bucket = buckets.get(bucket_key)
            if bucket is None:
                bucket = _TokenBucket(tokens=capacity, updated=now)
                buckets[bucket_key] = bucket
            else:
                bucket.tokens = min(
                    capacity, bucket.tokens + (now - bucket.updated) * refill_per_second
                )
                bucket.updated = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True

    async def middleware(call: Call, next: Next) -> Any:
        if not take(resolve_key(call)):
            raise TRPCError("Rate limit exceeded", code=TRPCErrorCode.TOO_MANY_REQUESTS)
        return await next()

    return middleware


def require_auth(predicate: Callable[[Any], Any]) -> Middleware:
    """Gate calls on a predicate over `call.ctx`.

    The predicate is invoked with the context object for each call; it may
    be sync or async (an awaitable result is awaited). A falsy verdict
    raises `TRPCError` with code `UNAUTHORIZED` (HTTP 401); a truthy one
    runs the rest of the chain.

    Args:
        predicate: `(ctx) -> Any` deciding whether the call proceeds. Read
            whatever your `context_factory` put on `ctx`.

    Returns:
        A middleware enforcing the predicate.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.middleware import require_auth

        router = Router(
            middleware=[require_auth(lambda ctx: ctx is not None and ctx.get("user"))]
        )
        ```
    """

    async def middleware(call: Call, next: Next) -> Any:
        verdict = predicate(call.ctx)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if not verdict:
            raise TRPCError("Unauthorized", code=TRPCErrorCode.UNAUTHORIZED)
        return await next()

    return middleware
