# Flask

`mount_wsgi(router)` returns a plain WSGI callable. Mount it alongside the
Flask app with Werkzeug's `DispatcherMiddleware` — or use `attach_flask`,
the one-call shortcut.

## Minimal app

```python
# server.py
from flask import Flask
from pydantic import BaseModel

from trpc_rs_py import Router, attach_flask


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


app = Flask(__name__)
attach_flask(app, router)   # serves /trpc/*, Flask routes unchanged
```

```bash
flask --app server run --debug
```

`attach_flask(app, router, path="/trpc")` wraps `app.wsgi_app` in a
`DispatcherMiddleware` for you and returns the same `app`.

## Explicit dispatch — tRPC + REST + plain WSGI

When you mount more than the tRPC app, build the dispatcher yourself:

```python
# snippet: skip — excerpt; needs app and router from server.py above
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from trpc_rs_py import mount_rest, mount_wsgi, wsgi_from_asgi

app.wsgi_app = DispatcherMiddleware(
    app.wsgi_app,
    {
        "/trpc": mount_wsgi(router),
        "/api": wsgi_from_asgi(mount_rest(router)),
    },
)
```

- `mount_wsgi(router, path="/trpc", ...)` — WSGI callable for the router;
  accepts the same `production`, `limits`, `sse_ping_interval`,
  `max_sse_event_size`, `context_factory`, `middleware`, `panel` kwargs as
  [`mount()`](fastapi.md#options).
- `wsgi_from_asgi(asgi_app)` — wraps **any** ASGI `http` app (here the
  REST/OpenAPI app) as a WSGI callable. `mount_rest` output is ASGI, so
  this is how REST lands on Flask.

## Running

Development: `flask --app server run --debug`, or `run_simple` from
`werkzeug.serving` (the examples use
`run_simple(host, port, app, threaded=True)`).

Production — any WSGI server:

```bash
gunicorn "server:app" -w 4 --threads 8 -b 0.0.0.0:8000
# or: waitress-serve --threads 8 --listen 0.0.0.0:8000 server:app
```

with `mount_wsgi(router, production=True)` / `attach_flask(..., production=True)`.

## WSGI streaming caveats

SSE subscriptions work through the WSGI bridge — each request pumps the
ASGI dispatch on a private event loop — but plan around two limits:

1. **One worker thread per open stream.** A WSGI server has no event
   loop; the bridge holds a thread for the stream's lifetime. Size
   gunicorn `--threads` / waitress `--threads` with concurrent-subscriber
   counts in mind. High-subscription workloads belong on an
   [ASGI adapter](../adapters.md).
2. **Disconnects are noticed on write.** The bridge learns a client left
   when `close()` is called on the response iterable or a send fails.
   Set `sse_ping_interval` (e.g. `5.0`) so idle streams produce writes
   that surface dead connections and reap workers.

The bridge also strips RFC 7230 hop-by-hop headers (`connection`,
`keep-alive`, …) from responses, which strict WSGI servers require.

## Flask-specific notes

- **`RequestContext.request` is the WSGI `environ` dict** in a
  `context_factory` — not a Flask `Request`. Use Flask's own
  request hooks for Flask-side concerns and
  [`context_factory`](../context.md) for handler context.
- **Threads, not tasks.** Every request is sync from Flask's view;
  `async def` handlers still work — the bridge runs them on the
  per-request loop.
- The dev playground sits at `/trpc/panel` like every other adapter.

Runnable example: `examples/flask-basic`.
