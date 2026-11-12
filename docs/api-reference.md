# API reference

Public API of the `trpc_rs_py` package, generated from docstrings with
[mkdocstrings](https://mkdocstrings.github.io). Everything reachable from
`import trpc_rs_py` is listed below, grouped by module; `trpc_rs_py._core`
(the Rust extension) is private and intentionally undocumented here.

## Package root

The names re-exported by `trpc_rs_py/__init__.py` — the surface most users
need:

::: trpc_rs_py

## Routers and procedures

::: trpc_rs_py.router

## ASGI adapters

`mount`/`attach` for FastAPI, Starlette, and any ASGI app:

::: trpc_rs_py.asgi

## WSGI adapters

`mount_wsgi`/`attach_flask`/`wsgi_from_asgi` for Flask and other WSGI apps:

::: trpc_rs_py.wsgi

## Django views

Sync (`trpc_view`, WSGI) and async (`trpc_view_async`, ASGI) views:

::: trpc_rs_py.django

## Standalone server

::: trpc_rs_py.serve

## REST + OpenAPI

`mount_rest` serves `RestRoute` procedures as REST endpoints with an
OpenAPI 3.1 document; `generate_openapi`/`merge_openapi` produce the
document for embedding in an existing spec:

::: trpc_rs_py.rest
::: trpc_rs_py.openapi

## Errors

`TRPCError`, the official v11 `TRPCErrorCode` enum, and the helpers behind
typed errors:

::: trpc_rs_py.errors

## Context

`RequestContext`, `ContextFactory`, and the `ctx` injection helpers:

::: trpc_rs_py.context

## Middleware

`Call`/`Next`/`Middleware` plus the built-in `logging_middleware`,
`timing_middleware`, `tracing_middleware`, `rate_limit`, and
`require_auth`:

::: trpc_rs_py.middleware

## CORS and CSRF

::: trpc_rs_py.cors
::: trpc_rs_py.csrf

## Streaming and AI helpers

`tracked` SSE event ids and the Vercel AI SDK v5 helpers
(`ai_text_stream`, `ai_message_stream`, `ai_data_part`, `ai_sdk_endpoint`):

::: trpc_rs_py.streaming

## Testing

`test_client`, `flask_test_client`, `django_test_client`, and the
`TestClient` API:

::: trpc_rs_py.testing

## Python client

Sync `Client`/`create_client` (stdlib only) and async `AsyncClient`/
`create_async_client` (requires `httpx`):

::: trpc_rs_py.client

## Code generation

Deterministic generators — all raise `TypeError` rather than emit `any`:

* `generate_types(router)` → `AppRouter` `.d.ts` for the official client
* `generate_zod(router)` → zod v4 input schemas
* `generate_python_client(router)` → `AppRouterClient` module
* `generate_openapi`/`merge_openapi` → OpenAPI 3.1 document

::: trpc_rs_py.codegen
::: trpc_rs_py.zodgen
::: trpc_rs_py.pyclient

## Validation helpers

::: trpc_rs_py.validation

## Dev tooling and panel

::: trpc_rs_py.dev
::: trpc_rs_py.panel
