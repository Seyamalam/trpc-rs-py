# Deployment

trpc-rs-py is a library, not a service: you mount a `Router` into your own
app and run that app under whatever server fits your stack. This page covers
the production knobs and the per-framework ways to run them.

## Production mode

Every adapter accepts `production=True` (the standalone CLI takes
`--production`). It does two things:

* **Error redaction.** Messages of `TRPCError`s with code
  `INTERNAL_SERVER_ERROR`, and of unexpected exceptions, are replaced with
  the fixed `"Internal server error"`. `TRPCError`s with any other code are
  deliberate client-facing API errors (for example `"User not found"`) and
  pass through unchanged. See [typed errors](typed-errors.md).
* **Panel disabled by default.** The playground at `{path}/panel` is on in
  development only; pass `panel=True` explicitly to keep it in production.

```python
from trpc_rs_py import Router, mount

router = Router()
app = mount(router, production=True)
```

For tooling that reads the environment rather than the mount call,
`TRPC_RS_PY_PRODUCTION=1` marks a deployment as production:
`trpc_rs_py.dev.is_production()` returns `True`, and `trpc-py dev`
(live type sync) **refuses to run** while it is set — type sync is a
development-only tool. Mount functions do not read the variable
automatically; pass `production=True` (or `production=is_production()`).

## ASGI frameworks (FastAPI, Starlette, any ASGI app)

`mount()`/`attach()` return a plain ASGI app, so you scale with the server,
not with trpc-rs-py.

**uvicorn** — use multiple worker *processes* for CPU parallelism:

```bash
uvicorn myapp:app --host 0.0.0.0 --port 8000 --workers 4
```

Note that `--workers` spawns processes, so any in-process state (the
[token-bucket rate limiter](middleware.md#rate_limit), for example) is
per-worker — see the middleware docs.

**gunicorn + uvicorn workers** — the standard production pairing:

```bash
gunicorn myapp:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
```

For FastAPI:

```python
from fastapi import FastAPI
from trpc_rs_py import Router, mount

router = Router()

@router.query
def health() -> bool:
    return True

app = FastAPI()
app.mount("/trpc", mount(router, production=True))
```

## Django

**Sync deployment (WSGI / gunicorn):** use `trpc_view` in `urls.py`. It
returns `HttpResponse` for queries/mutations and a
`StreamingHttpResponse` for SSE subscriptions — one worker thread per open
stream, disconnects noticed on write. Set `sse_ping_interval` so dropped
connections are noticed on the next write.

```python
# urls.py
from django.urls import path
from trpc_rs_py.django import trpc_view
from myapp.trpc import router

urlpatterns = [path("trpc/<path:trpc_path>", trpc_view(router, production=True))]
```

```bash
gunicorn myproject.wsgi:application -w 4 -b 0.0.0.0:8000
```

**Async deployment (ASGI / uvicorn or Daphne):** use `trpc_view_async`,
which runs the ASGI app on Django's event loop with real pull-based
backpressure and no extra threads. Django also runs it correctly under WSGI,
but the sync view is cheaper there.

```python
from trpc_rs_py.django import trpc_view_async

urlpatterns = [path("trpc/<path:trpc_path>", trpc_view_async(router, production=True))]
```

```bash
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 1
```

With `trpc_view_async`, uvicorn workers are async tasks inside one process;
scale horizontally with more processes/instances rather than `--workers`
expectations borrowed from sync WSGI.

## Flask and other WSGI apps

`mount_wsgi` returns a WSGI callable; mount it with
`werkzeug.middleware.dispatcher.DispatcherMiddleware` or serve it under any
WSGI server (gunicorn's sync workers, waitress, etc.). `attach_flask`
integrates with Flask's dispatch. See [adapters](adapters.md).

## Standalone server (`trpc-py serve`)

The optional standalone server is a Rust ASGI server (`hyper`) in-process —
no uvicorn, no framework:

```bash
trpc-py serve --app myapp.server:router --host 0.0.0.0 --port 8000 --production
# or from Python:
from trpc_rs_py.serve import serve
serve(router, host="0.0.0.0", port=8000, production=True)
```

It is **single-process by design**: there is no built-in multi-worker mode.
Scale by running multiple instances behind a load balancer, exactly like any
single-process server. See [standalone server](standalone.md) for the
threading model.

## Container example

The published wheel is a prebuilt binary — `pip install` needs no Rust
toolchain on supported platforms (Linux x86_64/aarch64, macOS
x86_64/arm64, Windows x86_64; source builds are the fallback elsewhere):

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # includes trpc-rs-py

COPY . .
ENV TRPC_RS_PY_PRODUCTION=1
EXPOSE 8000

# FastAPI/Starlette app:
CMD ["uvicorn", "myapp:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
# or standalone:
# CMD ["trpc-py", "serve", "--app", "myapp.server:router", "--host", "0.0.0.0", "--port", "8000", "--production"]
```

On Apple Silicon, `python:3.12-slim` resolves to `linux/arm64` — use a
multi-arch base image or pin `--platform linux/amd64` if you only have
x86_64 wheels in your environment.

## Health checks and reverse proxies

trpc-rs-py has no built-in health endpoint. Add a trivial query and point
your orchestrator at it:

```python
@router.query
def health() -> bool:
    return True
# GET /trpc/health?input={} → {"result":{"data":true}}
```

Or expose one through `mount_rest`'s OpenAPI surface if you already use REST
routes.

**Reverse proxy notes:**

* Terminate TLS at the proxy (nginx, Caddy, ALB, ...). All adapters speak
  plain HTTP.
* **Disable response buffering for SSE subscriptions.** The tRPC SSE stream
  is long-lived; proxies that buffer will hold events until the stream
  closes. For nginx use `proxy_buffering off;` on the tRPC location (the AI
  SDK endpoint additionally sends `x-accel-buffering: no`). Set
  `sse_ping_interval` (e.g. `10.0`) so idle streams emit keep-alives that
  also defeat idle timeouts.
* Ensure `proxy_read_timeout` exceeds your longest-lived subscriptions, or
  rely on client reconnect/resume (`tracked()` event ids).
* Forward `Host` and the client IP (`X-Forwarded-For` handling is your
  proxy's job; `RequestContext.client` reflects what the server reports).

## Related

* [Limits](limits.md) — body size, batch size, JSON depth, procedure timeout
* [CORS](cors.md) / [CSRF](csrf.md) — browser-facing hardening
* [Middleware](middleware.md) — rate limiting, auth gates, logging
* [Security threat model](security/threat-model.md)
* [Production readiness](production-readiness.md) — what gates 1.0
