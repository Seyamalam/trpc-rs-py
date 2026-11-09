# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
