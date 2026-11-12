# Examples

Every example is runnable, has its own README with exact commands, and is
smoke-tested by the local CI gate via an `examples/<name>/smoke.sh` script.

## Launch demos (built first, MISSION.md section 9)

- `ai-chat` — Next.js + FastAPI streaming chat with OpenAI or Anthropic,
  streaming helpers, cancellation on disconnect. Runs against a mock LLM with
  no API key, so the smoke test needs no secrets.
- `live-types` — the Next.js + FastAPI starter showing a Python field rename
  turning into a TypeScript error.
- `migrate-from-node` — an existing Node tRPC app with two procedures moved to
  Python using the migration link.

## One per framework

- `fastapi-basic` — FastAPI + `mount()`.
- `starlette-basic` — Starlette routes + `mount()`/`mount_rest()`.
- `flask-basic` — Flask + `mount_wsgi()`/`wsgi_from_asgi()` through
  `DispatcherMiddleware`.
- `django-sync` — Django (WSGI) + `trpc_view`/`asgi_view`.
- `django-async` — Django (ASGI) + `trpc_view_async`/`asgi_view_async`.
- `standalone-server` — `serve()` on the embedded Rust HTTP server
  (production mode), no Python framework.

The `-basic` examples serve the same router — queries, a mutation, REST +
OpenAPI under `/api` — so the differences between adapters are visible at
a glance.

## Combinations

- `rest-and-trpc` — ONE router mounted twice on one app: tRPC under `/trpc`
  plus REST + OpenAPI under `/api`.
- `merged-routers` — sub-routers composed with `merge`/`nest` into
  `posts.*`, `users.*` and two-level `posts.comments.*` paths.
- `shared-router` — one router module served by both a FastAPI (`mount`)
  and a Flask (`mount_wsgi`) app with byte-identical results.
- `python-client` — a uvicorn-served router called by
  `trpc_rs_py.client.create_client` (query/mutation/batch/typed error) and
  the generated `AppRouterClient` stub.
- `fastapi-react-vite` — FastAPI backend + Vite/React frontend using
  `@trpc/react-query` hooks typed by the generated `app-router.d.ts`,
  with CORS allowlisted for the dev origin.
- `flask-nextjs` — Flask backend + Next.js frontend calling the router
  server-side from an async server component (`@trpc/client`, no CORS).
- `django-vanilla-ts` — Django backend + Vite-served vanilla TypeScript
  page using `@trpc/client` through a same-origin dev proxy.

## Features

- `feature-auth-context` — middleware building a request context and
  procedures reading it (auth-style `ctx`).
- `feature-batching` — several calls in one `?batch=1` request with
  ordered per-call envelopes, plus the Python client's `.batch()`.
- `feature-typed-errors` — declared `TRPCError` subclasses with typed
  `data` payloads forming a discriminated union in the generated types.
- `feature-error-handling` — production vs. development error policy:
  client-facing codes pass through, INTERNAL/unexpected errors redact.
- `react-zod-form` — a React form validating with Zod schemas generated
  from the same Pydantic model the server enforces.
- `feature-pydantic-vs` — the same mutation validated by a Pydantic model
  vs. hand-rolled `dict` checks; wire behavior and trade-offs compared.

File upload (if supported) is still planned.
