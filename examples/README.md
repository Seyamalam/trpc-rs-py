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
- `standalone-server` — planned (the standalone Rust server).

Each serves the same router — queries, a mutation, REST + OpenAPI under
`/api` — so the differences between adapters are visible at a glance.

## Combinations

FastAPI + React (Vite) with `@trpc/react-query`; Flask + Next.js; Django +
vanilla TypeScript client; shared router module across FastAPI and Flask;
merged/nested routers; one router serving tRPC and REST + OpenAPI; Python
server + Python client.

## Features

Auth middleware with context, batching, typed errors, Zod form validation,
error handling, file upload (if supported), Pydantic vs. other schema libraries.
