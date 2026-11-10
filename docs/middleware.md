# Middleware

Middleware is a callable `(call, next) -> result` wrapping one procedure
invocation — the tRPC onion model. Awaiting `next()` runs the rest of the
chain and the handler and returns the handler's result. Input validation
runs **before** the chain (a parse failure never reaches middleware);
output validation runs **after** it unwinds.

```python
async def audit_middleware(call, next):
    print("before", call.path)
    result = await next()
    print("after", call.path)
    return result
```

Middleware may be sync or async — a sync callable runs inline and its
return value becomes the result.

## The `Call` object

Each middleware receives a frozen `Call` view:

| Attribute | Contents |
| --- | --- |
| `path` | Dotted procedure path (`posts.by_id`) |
| `kind` | `ProcedureKind` of the procedure |
| `input` | Raw decoded JSON input (`None` when absent) |
| `ctx` | The context object the handler sees unless swapped |
| `request_context` | The `RequestContext`, or `None` on direct unit calls |
| `extra` | Scratch dict for middleware-to-middleware data |

`next(ctx=obj)` replaces the `ctx` that downstream middleware and the
handler see — pair it with a leading `ctx` handler param plus a
[`context_factory`](context.md):

```python
async def authed(call, next):
    user = await lookup(call.request_context.headers["authorization"])
    return await next(ctx={"user": user})
```

## Registration

**Router-level** — on the router itself, and it applies to procedures
*registered afterwards* only (the chain is snapshotted onto each
procedure at decoration time; `use()` affects later registrations, tRPC
builder semantics). Chains travel with procedures through `merge`/`nest`:

```python
from trpc_rs_py import Router

router = Router(middleware=[audit_middleware])


@router.use
async def timing(call, next):
    ...
```

**Mount-level** — the escape hatch covering every procedure regardless
of registration order. Pass `middleware=[...]` to `mount`, `attach`,
`mount_rest`, `mount_wsgi`, `attach_flask`, `trpc_view`,
`trpc_view_async`, or `urlpatterns`. Mount middleware is outermost first
and wraps the procedure's own router chain; the effective chain is
`(*mount_middleware, *procedure.middleware)`:

```python
app = mount(router, middleware=[timing_middleware()])
```

## Short-circuiting and errors

Return a value without calling `next()` to skip the handler entirely —
the value becomes the result (and output validation still applies).
Raising `TRPCError` produces the normal error envelope with its code
intact; production redaction applies as usual.

## Subscriptions and REST

For subscriptions the chain runs **once** at subscription start — the
result is the async generator; per-event delivery is not wrapped. See
[subscriptions](subscriptions.md). REST routes through `mount_rest` /
`attach(rest_path)` run the same chain. See
[REST + OpenAPI](rest.md).

## Built-in helpers

```python
from trpc_rs_py.middleware import logging_middleware, timing_middleware

router = Router(middleware=[
    logging_middleware(),                          # level=logging.INFO
    timing_middleware(),                           # default sink below
])
```

`logging_middleware(logger=None, level=logging.INFO)` logs `path`,
`kind`, duration, and outcome through a `logging.Logger` (default
`trpc_rs_py.middleware`). `timing_middleware(sink=None)` records each
call's duration in milliseconds; the default sink writes
`ctx["trpc_duration_ms"]` for a `MutableMapping` ctx or a
`trpc_duration_ms` attribute otherwise. Pass `sink=(call, ms) -> ...` to
record elsewhere.

## Auth example

```python
from trpc_rs_py import Router, TRPCError, TRPCErrorCode


async def auth(call, next):
    token = call.request_context.headers.get("authorization", "")
    user = await users_by_token(token)          # None when unknown
    if user is None:
        raise TRPCError("Unauthorized", code=TRPCErrorCode.UNAUTHORIZED)
    return await next(ctx={"user": user})


router = Router(middleware=[auth])


@router.query
def me(ctx) -> dict:
    return {"name": ctx["user"].name}
```

Without a `context_factory`, `ctx` defaults to the `RequestContext`
itself; with one, `next(ctx=...)` supplies whatever the factory
produces — middleware typically builds on top of it. See
[context](context.md).
