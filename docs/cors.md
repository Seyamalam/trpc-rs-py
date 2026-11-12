# CORS

Cross-Origin Resource Sharing is handled by `trpc_rs_py.cors.cors` — an
ASGI middleware *wrapper* you put around the mounted app:

```python
from trpc_rs_py import Router, mount
from trpc_rs_py.cors import cors

router = Router()
app = cors(mount(router), allow_origins=["https://app.example.com"])
```

CORS is **deny by default** (MISSION section 7): with no explicit
allowlist the middleware emits no `Access-Control-*` headers at all and
refuses every preflight with `403`. Browsers enforce the denial — a
disallowed origin gets the app's response unchanged, so nothing sensitive
is hidden; the browser simply refuses to expose it to the page.

Because it wraps the ASGI app rather than a procedure, one implementation
covers every adapter (ADR 0010 — WSGI and Django run through the same
ASGI app):

```python
# snippet: skip — excerpt; needs starlette_app, router and path from your app
from trpc_rs_py import Router, attach, mount, mount_rest, wsgi_from_asgi
from trpc_rs_py.django import asgi_view
from trpc_rs_py.cors import cors

app = cors(attach(starlette_app, router), allow_origins=["https://app.example.com"])
wsgi_app = wsgi_from_asgi(cors(mount_rest(router), allow_origins=["https://app.example.com"]))
urlpatterns = [path("api/<path:path>", asgi_view(cors(mount(router), allow_origins=[...])))]
```

The `middleware=` option on `mount`/`attach` is the **wrong layer** for
CORS: chain middleware runs per procedure inside dispatch, so it cannot
answer a preflight `OPTIONS` (which never reaches a procedure) and does
not cover `mount_rest` routes. Wrap the app instead.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `allow_origins` | `()` | Exact origins allowed (e.g. `"https://app.example.com"`). `"*"` allows any origin. Empty = deny all. |
| `allow_methods` | `("GET", "POST", "OPTIONS")` | `Access-Control-Allow-Methods` value on preflights |
| `allow_headers` | `("content-type",)` | `Access-Control-Allow-Headers` value; `"*"` echoes the requested headers |
| `expose_headers` | `()` | `Access-Control-Expose-Headers` on actual responses |
| `allow_credentials` | `False` | Emit `Access-Control-Allow-Credentials: true` |
| `max_age` | `600` | `Access-Control-Max-Age` seconds on preflights |

A bare string is accepted for `allow_origins` and treated as one origin
(`allow_origins="https://app.example.com"`). Matching is an **exact
string comparison** — `https://a.com` and `http://a.com` are different
origins, and subdomains are not implied.

## Preflights

A request is a preflight when it is an `OPTIONS` carrying both `Origin`
and `Access-Control-Request-Method`. The middleware answers it directly —
the wrapped app never sees it:

* allowed origin → `204` with `Access-Control-Allow-Origin` (the
  reflected origin, or `*` in wildcard mode without credentials),
  `-Allow-Methods`, `-Allow-Headers`, `-Max-Age`, and
  `Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers`
* disallowed origin → `403` with no CORS headers

An `OPTIONS` without `Access-Control-Request-Method` is not a preflight:
it passes through to the app untouched.

## Actual requests

Actual requests pass through to the app; bodies are never buffered, so
SSE subscriptions stream normally. When the `Origin` is allowed, the
response gains `Access-Control-Allow-Origin`, `Vary: Origin` (merged with
any existing `Vary`), `Access-Control-Allow-Credentials` when
`allow_credentials=True`, and `Access-Control-Expose-Headers` when
configured. Requests without an `Origin` header pass through untouched,
as do `websocket` and `lifespan` scopes.

## Credentials and the wildcard

The CORS spec forbids `Access-Control-Allow-Origin: *` together with
credentials. With `allow_origins=["*"]` and `allow_credentials=True` the
middleware therefore reflects the request's concrete `Origin` instead of
emitting `*`.
