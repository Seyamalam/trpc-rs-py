# Troubleshooting & FAQ

Real gotchas from the codebase, collected in one place. Each entry names the
symptom, the cause, and the fix.

## `create_async_client` raises `RuntimeError: ... requires the optional 'httpx' package`

The sync client (`create_client`) uses only the standard library; the async
client is built on `httpx`, which is an **optional** dependency imported
lazily:

```bash
pip install httpx        # or: uv add httpx
```

If you only need sync calls, no extra install is required.

## `httpx.ResponseNotRead` when handling subscription errors

`httpx` refuses `response.json()` while a streamed body is unread. Both the
sync and async `Client.subscribe` implementations drain the body first, so
you only hit this if you build on `httpx` yourself:

```python
# snippet: skip — excerpt inside an async caller; http and url are its own
async with http.stream("GET", url) as response:
    if response.status_code >= 400:
        await response.aread()          # drain before .json()
        payload = response.json()
```

Same for the sync client: `response.read()` before `response.json()`.

## My `ctx` parameter isn't being injected

Context injection is **positional and literal**: only the *first* declared
parameter of a handler, and only when it is named exactly `ctx`, is
treated as the injected context object. A `ctx` anywhere else is an
ordinary input parameter — it will be expected in the request input.

```python
# snippet: skip — excerpt; needs router
@router.query
def me(ctx, user_id: int): ...    # ctx injected — OK
def bad(context, user_id: int): ...  # 'context' is an input param — BAD
```

A keyword-only `ctx` (after `*args` or `*`) is injected as a keyword
argument; any other position is ignored. See [context](context.md).

## `stubtest` reports phantom exports like `_TestClient@694`

`mypy.stubtest` flattens **function-local class definitions** into the
module's export view, so a class defined inside a factory function looks
like a module-level export that doesn't exist at runtime. This bit the
testing helpers once already. When writing module code that stubtest
checks: hoist helper classes to module level, or patch instances
(`types.MethodType`) instead of defining classes inside functions.

## The playground panel is missing in production

By design: `panel=None` (the default) enables `{path}/panel` only when
`production=False`. In production the panel is off unless you opt in:

```python
# snippet: skip — excerpt; needs mount and router imported
app = mount(router, production=True, panel=True)   # explicit opt-in
```

While enabled, the panel answers before procedure dispatch — a procedure
literally named `panel` is shadowed.

## `trpc-py dev` refuses to run

Live type sync is development-only. If `TRPC_RS_PY_PRODUCTION` is set to a
truthy value (`1`, `true`, `yes`, `on`), `run_dev` raises:

```
RuntimeError: trpc-py dev refuses to run in production (TRPC_RS_PY_PRODUCTION is set)
```

Unset the variable (or set it to anything else) in the shell running the
watcher. Deployments should pass `production=True` to the mount instead.

## What does the `TIMEOUT` error code mean?

`TIMEOUT` (`-32008`, HTTP 408) is returned when a handler exceeds
`Limits(procedure_timeout=...)`. It is a dispatch-level bound on the
*handler call* — input/output validation and the middleware chain run
outside it. See [limits](limits.md#procedure_timeout) for the full
semantics, including the sync-handler caveat below.

## Sync handlers "time out" but keep running

`procedure_timeout` wraps the handler invocation in `asyncio.wait_for`.
An `async def` handler is cancelled at the bound; a plain `def` handler
runs on a worker thread via `asyncio.to_thread`, and after the timeout the
thread **still runs to completion in the background** — the bound is on
waiting, not on CPU burn. CPU-bound work belongs off the event loop
regardless.

## `mount_rest` ignores `procedure_timeout`

REST endpoints served by `mount_rest` run a separate dispatch path and are
**not** bounded by `procedure_timeout`. Bound them with your own
middleware or the proxy's timeouts.

## Subscriptions drop or error with large events

Each encoded SSE frame is capped by `max_sse_event_size` (default 1 MiB);
a yielded value serializing beyond it ends the stream with a
`serialized-error` frame. Raise it per mount if you legitimately emit
larger events:

```python
# snippet: skip — excerpt; needs mount and router imported
app = mount(router, max_sse_event_size=8 * 1024 * 1024)
```

Backpressure is pull-based: a slow client pauses the generator rather than
buffering, and `sse_ping_interval` keeps idle streams alive through
proxies. See [limits](limits.md#sse-limits) and
[subscriptions](subscriptions.md).

## How do I run the example smoke tests?

Each example ships a `smoke.sh` that boots the app on a free port and
exercises it end-to-end:

```bash
cd examples/fastapi-basic && ./smoke.sh
```

To run them all (what `just ci-examples` does in the gate):

```bash
./scripts/examples-smoke.sh
```

Examples with smokes: `fastapi-basic`, `starlette-basic`, `flask-basic`,
`django-sync`, `django-async`, `ai-chat`, `live-types`, `migrate-from-node`.

## Still stuck?

* [Production readiness](production-readiness.md) — the pre-1.0 checklist
* [Security threat model](security/threat-model.md) — what's defended and how
* [Feature matrix](feature-matrix.md) — what's supported per adapter
