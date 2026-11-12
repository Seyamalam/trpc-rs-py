# Starlette

`mount(router)` returns a plain ASGI app; add it to the route table with
`Mount`, or call `app.mount()`.

## Minimal app

```python
# server.py
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.routing import Mount

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


app = Starlette(routes=[Mount("/trpc", app=mount(router))])
```

```bash
uvicorn server:app --reload
```

Equivalent without the routes list: `app.mount("/trpc", mount(router))` on
a bare `Starlette()` instance — same `Mount` underneath.

## Mounting REST alongside

`mount_rest(router)` is an ASGI app too, so it drops into the same routes
list for procedures carrying `rest=RestRoute(...)`:

```python
from trpc_rs_py import RestRoute, mount_rest

app = Starlette(
    routes=[
        Mount("/trpc", app=mount(router)),
        Mount("/api", app=mount_rest(router, title="my-api", version="0.1.0")),
    ]
)
```

`attach(app, router)` is the middleware-style alternative: it wraps the
whole app, serves the configured prefix, and forwards everything else —
lifespan and websockets included — to the wrapped Starlette app:

```python
from trpc_rs_py import attach

application = attach(app, router, path="/trpc")
```

Both mounting styles run the identical dispatch; pick per taste. The
`mount()` keyword table is in the [FastAPI guide](fastapi.md#options) and
[adapters](../adapters.md).

## Running

Development: `uvicorn server:app --reload`.

Production:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
# or: gunicorn server:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
```

with `mount(router, production=True)`.

## Starlette-specific notes

- **The adapter tolerates both mounting conventions.** Whether the scope's
  `path` still carries the `/trpc` prefix or Starlette already stripped it,
  the procedure path resolves the same — `Mount` at any depth works.
- **`lifespan` is answered locally.** `mount()` accepts startup/shutdown
  itself, so a mounted sub-app works whether or not the framework
  forwards lifespan scopes to it.
- **Websockets are refused.** A `websocket` scope reaching the mount is
  closed with code 1008 — subscriptions run over SSE, not websockets.
- **Workers are processes.** `--workers N` means N independent
  interpreters; in-process state is per-worker.
- **`RequestContext.request` is the ASGI `scope` dict** inside a
  `context_factory` — build custom context with
  [`context_factory`](../context.md).

Runnable example: `examples/starlette-basic`.
