# CSRF protection

`csrf_protect` is an adapter-level request filter (ADR 0011) — an ASGI
app wrapper, not procedure middleware — so one wrap protects tRPC
procedures, REST routes, and anything else served by the same app. It
runs **before** dispatch: rejected requests never reach a handler.

```python
from trpc_rs_py import Router, mount
from trpc_rs_py.csrf import csrf_protect

router = Router()
app = csrf_protect(mount(router), allowed_origins=["https://app.example.com"])
```

## Why GET queries need this

Mutations are POST + `application/json`, which a cross-site page cannot
send without a CORS preflight — with deny-by-default CORS they are safe
already. Queries run over GET, and a cross-site `<img>`, `<form>`, or
navigation GET carries ambient cookies. If a query reads session state
(see [context](context.md) — `ctx` injection exists precisely so it can),
a forged GET executes it with the victim's credentials. The check
therefore keys on **credentials**, not method: a request is checked when
it carries a `Cookie` or `Authorization` header, whatever the HTTP
method. Uncredentialed requests pass through untouched — there is no
session to abuse.

## Decision rules

For a checked (credentialed) request, in order:

| `Sec-Fetch-Site` | `Origin` | Verdict |
| --- | --- | --- |
| `same-origin`, `same-site`, `none` | any | allow |
| `cross-site` | exactly in `allowed_origins` | allow |
| `cross-site` | anything else (incl. absent, `"*"` alone) | **403** |
| absent / unrecognized | in `allowed_origins`, or any value when `"*"` configured | allow |
| absent / unrecognized | absent | `allow_missing_origin` decides (default **403**) |
| absent / unrecognized | present, not allowlisted | **403** |

Rejections return `403` with the fixed body
`{"error":{"code":"FORBIDDEN","message":"CSRF check failed"}}` — the same
shape as [REST errors](rest.md). Fetch-metadata values compare
case-insensitively; `Origin` is an exact string match — scheme, host, and
port all matter (`http://app.example.com` ≠ `https://app.example.com`).

## Options

| Parameter | Default | Effect |
| --- | --- | --- |
| `allowed_origins` | `()` | Exact `Origin` values allowed for checked requests. `"*"` accepts any *present* `Origin`, but never rescues a request declaring `Sec-Fetch-Site: cross-site` and never substitutes for a missing `Origin` — wildcard keeps the fetch-metadata protection instead of disabling it. |
| `allow_missing_origin` | `False` | Pass checked requests carrying neither `Sec-Fetch-Site` nor `Origin` — the posture of older browsers and non-browser API clients. Weakens the check on exactly the header-less requests a simple forged `<img>`/form produces on legacy clients; prefer listing `allowed_origins`. Never applies to declared `cross-site` requests. |

Non-`http` scopes (`websocket`, `lifespan`) pass through untouched.

## Every adapter

```python
# snippet: skip — excerpt; needs app, router, ORIGINS and the imports
# ASGI (FastAPI, Starlette, ...)
app.mount("/trpc", csrf_protect(mount(router), allowed_origins=ORIGINS))

# WSGI (Flask, any WSGI server)
from trpc_rs_py import wsgi_from_asgi
trpc_wsgi = wsgi_from_asgi(csrf_protect(mount(router), allowed_origins=ORIGINS))

# REST + OpenAPI
rest_app = csrf_protect(mount_rest(router), allowed_origins=ORIGINS)

# Django
from trpc_rs_py.django import asgi_view
path("trpc/<path:path>", asgi_view(csrf_protect(mount(router, path=""), allowed_origins=ORIGINS)))
```

## Ordering with `cors()`

Wrap CSRF first, then CORS — `cors(csrf_protect(mount(...)), ...)`:

```python
# snippet: skip — excerpt; needs router and the cors/csrf_protect imports
app = cors(
    csrf_protect(mount(router), allowed_origins=["https://app.example.com"]),
    allow_origins=["https://app.example.com"],
)
```

CORS must be outermost: preflight `OPTIONS` requests are uncredentialed
and must reach the CORS handler, and every response — including CSRF
rejections — should carry `Access-Control-*` headers so a browser reports
the real 403 instead of a secondary CORS error. See [CORS](cors.md) for
the CORS helper's own options.

## Non-browser clients

API clients send `Authorization` but rarely `Origin` or fetch metadata —
a header-less credentialed request is rejected by default. Browser apps
should send an `Origin` (browsers do automatically on cross-site
requests) listed in `allowed_origins`. Server-to-server and CLI clients
behind the same endpoints can set `allow_missing_origin=True`, or simply
omit credentials in favor of per-request tokens on endpoints that do not
use ambient auth.

## Production guidance

Enable `csrf_protect` whenever queries or mutations read ambient
credentials (cookies, basic auth). Keep `allowed_origins` to the exact
deployed frontend origins — `"*"` is a debugging posture, not a
deployment one. This helper complements, not replaces, deny-by-default
CORS: CORS governs what a browser may *read*, CSRF protection governs
what ambient credentials may *do*.
