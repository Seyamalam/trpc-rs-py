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

Still planned: FastAPI + React (Vite) with `@trpc/react-query`; Flask +
Next.js; Django + vanilla TypeScript client.

## Features

Auth middleware with context, batching, typed errors, Zod form validation,
error handling, file upload (if supported), Pydantic vs. other schema libraries.
