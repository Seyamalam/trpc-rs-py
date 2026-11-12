# FastAPI

`mount(router)` returns a plain ASGI app; `app.mount()` puts it under a
path prefix alongside your normal FastAPI routes.

## Minimal app

```python
# server.py
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, mount


class Post(BaseModel):
    id: int
    title: str


router = Router()


@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"


@router.mutation
def create_post(title: str) -> Post:
    return Post(id=1, title=title)


app = FastAPI()
app.mount("/trpc", mount(router))
```

```bash
uvicorn server:app --reload
```

The router is served at `/trpc` — `client.greeting.query({ name })` calls
`GET (or POST) /trpc/greeting`. The dev playground is at `/trpc/panel`.

## Options

`mount(router, ...)` accepts — see [adapters](../adapters.md) for the full
table shared by every framework:

| Option | Effect |
| --- | --- |
| `path="/trpc"` | Prefix stripped when the framework did not strip it |
| `production=False` | Redact internal errors; also gates the panel |
| `limits=None` | [`Limits`](../limits.md) — body/batch/JSON-depth/timeout |
| `sse_ping_interval=None` | SSE keep-alive interval in seconds |
| `max_sse_event_size` | Per-frame SSE cap (default 1 MiB) |
| `context_factory=None` | Per-request context for `ctx` handlers |
| `middleware=()` | Mount-level middleware, outermost first |
| `panel=None` | Playground on in dev, off in production |

Optional extras on the same app:

```python
from trpc_rs_py import mount_rest

app.mount("/api", mount_rest(router))      # REST + OpenAPI for RestRoute procs
```

`attach(app, router)` is the alternative to `app.mount`: it wraps the whole
ASGI app, serves the configured prefix, and forwards everything else —
including lifespan and websockets — untouched. Use it when you need
`rest_path=` on the same wrapper or middleware-style composition:

```python
from trpc_rs_py import attach

application = attach(app, router, path="/trpc", rest_path="/api")
```

## Running

Development: `uvicorn server:app --reload`.

Production: worker *processes* for CPU parallelism, and `production=True`:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
# or the standard gunicorn pairing:
gunicorn server:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
```

```python
app.mount("/trpc", mount(router, production=True))
```

## FastAPI-specific notes

- **Workers are processes.** `--workers N` / gunicorn `-w N` each get their
  own interpreter: in-process state (in-memory dicts, the `rate_limit`
  token buckets) is per-worker, not shared.
- **Sync handlers do not block the loop.** `def` procedures run via
  `asyncio.to_thread`; `async def` procedures run on the event loop — keep
  them non-blocking.
- **SSE streams natively.** Subscriptions run on the server's own loop
  with pull-based backpressure — no WSGI-style worker pinning.
- **`RequestContext.request` is the ASGI `scope` dict** when you read it
  in a `context_factory` — not a FastAPI `Request`.
- The mount is invisible to FastAPI dependency injection, `Depends`, and
  OpenAPI — it is a self-contained ASGI app. Use
  [`context_factory`](../context.md) for auth/state and `mount_rest` for
  an OpenAPI surface.

Runnable example: `examples/fastapi-basic` (and `examples/fastapi-react-vite`
for a full frontend wiring).
