# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-09

### Added

- Subscriptions over Server-Sent Events, wire-compatible with the official
  `@trpc/client` v11 `httpSubscriptionLink`: `@router.subscription` async
  generators, `tracked(event_id, data)` resumable event ids, `connected` /
  `data` / `ping` / `restore-id` / `serialized-error` / `return` frames
  encoded byte-for-byte like `@trpc/server` (`SseEncoder` in the Rust core),
  `lastEventId` resume via header, query parameter, or handler argument, and
  keep-alive pings plus a per-frame size cap (`sse_ping_interval`,
  `max_sse_event_size` on `mount()`/`attach()`).
- Client-disconnect cancellation: `http.disconnect` cancels the pending
  `__anext__` and closes the async generator.
- Codegen emits `SubscriptionProcedure` declarations with
  `AsyncIterable<...>` outputs (`{ id, data }` items for tracked yields).
- Official-client runtime coverage grew to 32 live tests per adapter pair,
  including SSE subscription streaming and async iteration.
- `trpc-py` CLI (argparse per ADR 0006): `dev` live type sync — Rust `notify`
  watcher with debounced batches, fresh-subprocess regeneration, atomic
  changed-only `.d.ts` writes, optional `--server` reload, production
  refusal via `TRPC_RS_PY_PRODUCTION` — plus `codegen ts`, `routes`,
  `check`, and `doctor` commands.
- REST + OpenAPI output (ADR 0005): procedures opt in with
  `rest=RestRoute(method, path)` on `@router.query`/`@router.mutation`;
  `mount_rest(router)` serves the REST endpoints, `GET /openapi.json`, and a
  vendored docs UI (off in production unless `docs=True`) under one ASGI
  mount — or `attach(..., rest_path="/api")` serves them through the
  middleware wrapper. Path placeholders bind handler parameters; remaining
  inputs come from the query string (GET/DELETE) or JSON body, through the
  same Pydantic validators as tRPC calls.
- `generate_openapi(router)` emits an OpenAPI 3.1 document with nested
  Pydantic models hoisted into `components.schemas` — spec-validated in
  tests, and exercised end-to-end by an `openapi-typescript`-generated
  client against the live fixture servers; `merge_openapi(app, router)`
  merges the generated paths into a FastAPI app's own `/openapi.json` via
  the documented `app.openapi` customization point.
- `trpc-py codegen openapi --app <module:attr> --out openapi.json` writes the
  document from the CLI.
- `trpc-py init <name>` scaffolds a Next.js + FastAPI starter (bundled
  `nextjs-fastapi` template): a server with tRPC + REST mounts, a typed
  `@trpc/client` Next.js app, and a generated `app-router.d.ts`.
- Examples: `examples/ai-chat` (tracked SSE token streaming from an async
  generator plus REST/OpenAPI on the same router) and `examples/live-types`
  (`trpc-py dev` regenerating `app-router.d.ts` on save), each with a
  `smoke.sh` run by the gate.
- Launch materials in `docs/launch/`: recordable demo scripts with VHS
  tapes (`ai-chat`, `live-types`, `migrate-from-node` marked pending the
  0.4.0 migration link) and a draft announcement for HN/r/Python/Discord.

## [0.2.0] - 2026-11-09

### Added

- tRPC v11 protocol core in Rust: query/mutation parsing, batching, result
  and error envelopes, request limits (body size, batch size, JSON depth),
  allocation-free router lookup (`crates/trpc-core`).
- Python `Router` / `@router.query` / `@router.mutation` API with merge and
  nest, and `TRPCError` with the official code table.
- PyO3 whole-batch FFI boundary (`parse_request`, `build_response`,
  `CallResult`) with the GIL released during parse and serialize.
- ASGI adapter: `mount()` sub-app and `attach()` middleware for FastAPI and
  Starlette, body-size enforcement, disconnect handling, production error
  redaction, sync handlers via worker threads.
- Pydantic v2 input/output validation built from handler signatures.
- `generate_types(router)` emitting an `AppRouter` `.d.ts` consumed by the
  official `@trpc/client` v11 (verified with `tsc --noEmit`).
- Official-client runtime compatibility suite: 28 live HTTP tests
  (FastAPI + Starlette) covering queries, mutations, batching, errors,
  method override and POST-only enforcement.
- `fastapi-basic` example with README and CI smoke test.
- `docs/protocol.md`: the exact wire format with verified deviations.
- Benchmark suite: criterion Rust micros, pytest-benchmark adapter/codegen
  micros, oha HTTP load vs a plain FastAPI baseline and the official Node
  `@trpc/server` standalone adapter; SVG charts via `benchmarks/plot.py`.
- `scripts/release.sh`: bump, changelog check, gate, wheel, tag, push.

### Fixed

- Error envelope now uses the official flat v11 shape
  (`error.message`/`error.code`/`error.data`), so the official client
  surfaces server errors with code and data instead of "Unable to
  transform response from server".
- Batched inputs tolerate missing index keys (the official client omits
  undefined inputs); an all-no-input batch sends `input={}`.

## [0.1.0] - 2026-11-08

### Added

- Workspace layout: `crates/trpc-core` (pure Rust protocol core),
  `crates/trpc-py` (PyO3 bindings, `trpc_rs_py._core`),
  `python/trpc_rs_py` package with `py.typed` and `_core.pyi` stub.
- tRPC v11 error codes, data codes, HTTP status mapping, and the error
  envelope (`trpc_core::error`), unit-tested.
- Local CI gate (`justfile`): fmt, clippy pedantic, cargo test, maturin,
  mypy/pyright strict, stubtest, pytest, TypeScript tests, example smoke
  tests, mkdocs --strict.
- Git hooks (`pre-commit` → `just ci-fast`, `pre-push` → `just ci`).
- ADRs 0001–0004 (Python versions, JSON library, schema libraries,
  standalone server framework).
- Docs skeleton with feature matrix and production readiness checklist.
- Pinned toolchains: `rust-toolchain.toml` (1.96.0), `.python-version` (3.14),
  `uv.lock`, `pnpm-lock.yaml`.
