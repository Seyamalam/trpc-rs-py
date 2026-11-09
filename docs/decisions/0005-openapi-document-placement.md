# ADR 0005: OpenAPI document placement for FastAPI apps

Status: Accepted
Date: 2026-11-09

## Context

MISSION.md section 2.4 requires the same router to serve REST endpoints and an
OpenAPI 3.1 document alongside tRPC, and asks for an explicit decision on
whether generated REST routes appear in a FastAPI host app's existing
`/openapi.json`. Our adapter is a pure-ASGI sub-app mounted under a prefix;
FastAPI builds its `/openapi.json` only from routes it registered itself, so
mounted sub-app routes are invisible to it.

## Options

1. **Merge into the host app's `/openapi.json` by default** — wrap
   `app.openapi()` so the generated REST paths appear in the app's existing
   document and Swagger UI. Most "integrated" but mutates the host app without
   being asked, creates two divergent OpenAPI surfaces (merged doc and the
   mounted doc), and couples us to FastAPI-specific internals for Starlette
   and other ASGI apps that have no such hook.
2. **Standalone document only** — serve `openapi.json` plus a docs UI under
   the REST mount root. Honest and uniform across frameworks, but FastAPI
   users end up with two OpenAPI documents and the generated routes never
   appear in their app's own docs.
3. **Standalone by default, opt-in merge for FastAPI** — the mounted document
   is authoritative; a `merge_openapi(app, router)` helper wraps the
   officially documented `app.openapi` customization point for users who want
   one document.

## Decision

Option 3. `mount_rest(router)` (and `attach(..., rest_path=...)`) serves REST
endpoints, `openapi.json`, and a built-in docs UI under its own prefix; the
mounted `openapi.json` is the single authoritative generated document.
`merge_openapi(app, router)` is available for FastAPI apps that explicitly
want the paths inside their own `/openapi.json` — it uses FastAPI's documented
`app.openapi` override mechanism rather than internal APIs.

## Consequences

- FastAPI users who want both tRPC and REST mount two sub-apps (or use
  `attach` with `rest_path`); this is one extra line in the host app and is
  documented.
- The docs UI is a small vendored page with no CDN dependency rather than a
  bundled Scalar/Swagger UI; `openapi.json` remains compatible with both and
  vendoring Scalar can be revisited if users ask.
- OpenAPI output validation runs through the OpenAPI 3.1 spec validator in
  tests (`openapi-spec-validator` dev dependency).
