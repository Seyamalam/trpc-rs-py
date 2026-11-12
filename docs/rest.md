# REST + OpenAPI output

The same router can serve plain REST endpoints and an OpenAPI 3.1 document
alongside tRPC, so adopting trpc-rs-py carries no lock-in: TypeScript
frontends use the official `@trpc/client`, and every other client (mobile,
partners, `curl`) uses REST. See
[ADR 0005](decisions/0005-openapi-document-placement.md) for the document
placement decision.

## Opting a procedure in

REST output is opt-in per procedure with `rest=RestRoute(method, path)` on the
`query` and `mutation` decorators:

```python
from trpc_rs_py import RestRoute, Router

router = Router()

@router.query(rest=RestRoute("GET", "/posts/{post_id}"))
def by_id(post_id: int, verbose: bool = False) -> dict:
    return {"id": post_id, "verbose": verbose}

@router.mutation(rest=RestRoute("POST", "/posts"))
def create(title: str) -> dict:
    return {"title": title}
```

`{name}` path segments bind to same-named handler parameters and must exist in
the handler signature (validated at registration). Remaining parameters come
from the query string for GET/DELETE routes, or the JSON request body for
POST/PUT/PATCH. Values pass through the same Pydantic input/output validators
as tRPC calls — `GET /posts/7?verbose=true` validates `post_id` as `int` and
`verbose` as `bool`.

Subscriptions cannot be served over REST.

## Mounting

`mount_rest(router)` returns a pure-ASGI app; mount it under any prefix:

```python
# snippet: skip — excerpt; needs app, mount, mount_rest and router
app.mount("/trpc", mount(router))
app.mount("/api", mount_rest(router, title="Blog API", version="1.0.0"))
```

This serves:

| Endpoint | What |
| --- | --- |
| `GET/POST/... /api/<route>` | One endpoint per `RestRoute` procedure |
| `GET /api/openapi.json` | The OpenAPI 3.1 document |
| `GET /api/docs` | Vendored docs UI with try-it forms (no CDN) |

For `attach` (middleware) setups, pass `rest_path`:

```python
# snippet: skip — excerpt; needs attach, FastAPI and router imported
app = attach(FastAPI(), router, path="/trpc", rest_path="/api")
```

## Responses and errors

Success responses carry the handler's return value directly (no tRPC
envelope). Errors use a fixed shape with the HTTP status mapped from the
`TRPCError` code:

```json
{"error": {"code": "NOT_FOUND", "message": "nothing here"}}
```

Production mode (`production=True`) applies the same redaction policy as the
tRPC adapter — internal details never leak — and disables `/docs` and
`/openapi.json` unless explicitly enabled with `docs=True`.

## OpenAPI generation

`generate_openapi(router, title=..., version=...)` builds the document from
the same annotations that drive validation; nested Pydantic models hoist into
`components.schemas`. The document is validated against
`openapi-spec-validator` in tests, and an `openapi-typescript`-generated
client is exercised against the live servers in the compatibility suite.

Write a document from the CLI:

```bash
trpc-py codegen openapi --app myapp.router:router --out openapi.json \
    --title "Blog API" --version 1.0.0
```

### Merging into a FastAPI app's own `/openapi.json`

The mounted document is authoritative, but FastAPI apps that want one merged
surface can opt in — it uses FastAPI's documented `app.openapi` customization
point:

```python
# snippet: skip — excerpt; needs FastAPI and router imported
from trpc_rs_py import merge_openapi

app = FastAPI()
merge_openapi(app, router)  # /posts/* now appears in app.openapi()
```

## Wire notes

- Duplicate `(method, path)` declarations are rejected at mount time.
- A request matching a route's path but not its method returns 405 with the
  `METHOD_NOT_SUPPORTED` code.
- Query parameters whose names collide with path placeholders are ignored
  (the URL wins); list-typed handler parameters accept repeated query keys.
