# Standalone server

`trpc-py` ships an optional standalone server: a Rust ASGI server in-process.
`hyper` handles sockets, HTTP parsing, and response streaming; your app is the
same `mount()` ASGI application used by every adapter, so routing, validation,
context, middleware, error envelopes, the panel, CSRF, CORS, and subscriptions
behave identically — there is no second dispatch implementation to drift.

## CLI

```bash
trpc-py serve --app myapp.server:router --host 127.0.0.1 --port 8000
```

`--app` takes the usual `module:attribute` form and must resolve to a
`Router`. Forwarded mount options such as `--production` apply unchanged.
(The subcommand lands with the CLI; `trpc_rs_py.serve.serve()` below is the
API it wraps.)

## Python API

```python
from trpc_rs_py import Router
from trpc_rs_py.serve import serve

router = Router()

@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"

serve(router, host="127.0.0.1", port=8000)
# GET /trpc/greeting?input={"name":"Ada"}
```

`serve()` blocks until shutdown, then returns `0`. Every `mount()` keyword
(`production`, `limits`, `context_factory`, `middleware`, `sse_ping_interval`,
`panel`, `path`, ...) is forwarded unchanged.

To serve any ASGI app directly — for example a CORS-wrapped mount — pass
`app=` instead of a router:

```python
from trpc_rs_py import cors, mount
from trpc_rs_py.serve import serve

serve(app=cors(mount(router), allow_origins=["https://app.example.com"]))
```

## Threading model

The calling thread blocks inside the Rust server with the GIL released. A
dedicated daemon thread runs one `asyncio` event loop, and each request's
ASGI coroutine is scheduled onto it — the same shape uvicorn uses, with
`hyper` in place of the Python HTTP machinery. Response chunks flow through
a bounded channel into the hyper body stream, so slow clients apply
backpressure without blocking the loop; a client disconnect cancels the
request's coroutine and its subscription generator. See
[ADR 0013](decisions/0013-standalone-server.md).

## Performance

Handlers still execute Python on a single `asyncio` loop, so throughput is
uvicorn-comparable for loop-bound workloads. What the Rust server buys is
HTTP parsing, socket I/O, and response streaming off the interpreter — the
same argument as the Rust protocol core. It is the right default when you do
not already run an ASGI server for other reasons.

## Limitations

- **One `asyncio` loop.** Concurrency is single-loop, like one uvicorn
  worker; scale with multiple processes behind a load balancer.
- **Graceful shutdown on SIGINT.** `serve()` returns after a clean drain.
  There is no in-band reload or multi-signal story.
- **HTTP/1.1.** Hyper's HTTP/2 is not wired in; the app never sees h2
  requests because none arrive.

## Security

The server speaks plain HTTP — there is **no TLS**. Terminate TLS upstream
(nginx, a platform load balancer, a service mesh) exactly as you would for
uvicorn. `production=True` redaction, request limits, and the CSRF panel
guard all apply identically, since the same `mount()` app answers requests.
