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
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

if TYPE_CHECKING:
    from .context import RequestContext
    from .router import ProcedureKind

__all__ = [
    "Call",
    "Middleware",
    "Next",
    "logging_middleware",
    "run_middleware",
    "timing_middleware",
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
