# trpc-rs-py

**The Python backend for your tRPC app.** Define procedures in Python (FastAPI,
Starlette, Flask, Django, or a standalone Rust server), call them from
TypeScript with the official `@trpc/client` — full end-to-end types, live as
you edit Python. The protocol handling lives in a Rust core built with PyO3,
so the adapter adds near-zero overhead over a plain JSON endpoint.

## Status

The project is at version 0.1.0 (foundation milestone). See
[MEMORY.md](https://github.com/Seyamalam/trpc-rs-py/blob/main/MEMORY.md) for
what is done and what comes next, and the
[feature matrix](feature-matrix.md) for feature status per adapter.

## Why not FastAPI + OpenAPI codegen?

Planned comparison — see the roadmap in `MISSION.md` section 2 and
`docs/feature-matrix.md` once the comparison is published.
