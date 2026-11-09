# trpc-rs-py

**The Python backend for your tRPC app.** Define procedures in Python (FastAPI,
Starlette, Flask, Django, or a standalone Rust server), call them from
TypeScript with the official `@trpc/client` — full end-to-end types, live as
you edit Python. The protocol handling lives in a Rust core built with PyO3,
so the adapter adds near-zero overhead over a plain JSON endpoint.

## Status

Version 0.2.0 is released; 0.3.0 (the launch milestone) is in development.
See
[MEMORY.md](https://github.com/Seyamalam/trpc-rs-py/blob/main/MEMORY.md) for
what is done and what comes next, the
[feature matrix](feature-matrix.md) for feature status per adapter, and the
[wire protocol](protocol.md) page for the exact tRPC v11 wire format and its
runtime verification against the official client.

## Examples

Runnable examples live in `examples/`, each with a `smoke.sh` exercised by
the gate:

- `fastapi-basic` — queries, mutations, batching, and error envelopes.
- `ai-chat` — an LLM-style streamed `chat` subscription with tracked,
  resumable SSE events, plus the same router serving REST + OpenAPI.
- `live-types` — `trpc-py dev` regenerating `app-router.d.ts` on every save.

## TypeScript types from your Python router

`generate_types(router)` emits an `AppRouter` declaration file that the
official `@trpc/client` v11 consumes directly: literal procedure paths, exact
input/output types from Python annotations and Pydantic v2 models, and a hard
`TypeError` instead of `any` for anything unmappable. See
[TypeScript codegen](codegen.md) for the usage, the type-mapping table, and
the tested limits.

## Why not FastAPI + OpenAPI codegen?

Planned comparison — see the roadmap in `MISSION.md` section 2 and
`docs/feature-matrix.md` once the comparison is published.
