# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-11-10

### Added

- WSGI and Django adapters (ADR 0010): one dispatch path everywhere — a
  per-request pumped loop drives the same ASGI app from sync code.
  * `mount_wsgi(router)` — WSGI callable for any WSGI server; SSE
    subscriptions stream through the response iterable.
  * `attach_flask(app, router)` — one-call Flask mount via
    `DispatcherMiddleware`.
  * `wsgi_from_asgi(asgi_app)` — wraps any ASGI `http` app (for example
    `mount_rest`) as a WSGI callable.
  * `trpc_rs_py.django`: `trpc_view` / `trpc_view_async` Django views,
    `trpc_urlpatterns` URL helper (`asynchronous=True` picks the async
    view), and generic `asgi_view` / `asgi_view_async` for mounting any
    ASGI app (REST, OpenAPI doc) in a URLconf.
- `docs/adapters.md` — adapter guide with the WSGI streaming limitations:
  one worker thread per open SSE stream, disconnects noticed on write,
  `sse_ping_interval` guidance.
- Compat fixtures for Flask, Django sync, and Django async — the official
  `@trpc/client` runtime suite now runs every protocol test against all
  five adapters (102 tests).
- New dev dependencies: `flask`, `django`, `django-stubs`,
  `django-stubs-ext` (stubtest coverage).
- `examples/starlette-basic`, `examples/flask-basic`,
  `examples/django-sync`, `examples/django-async` — one runnable, smoke
  -tested example per adapter, each serving tRPC + REST + OpenAPI.
- `trpc-py init` backend-only templates `fastapi-only`, `flask-only`,
  `django-only` (MISSION §2.8), each smoke-tested end to end in the gate.
- Adapter micro-benchmarks for `mount_wsgi` and `trpc_view`.

### Fixed

- The sync↔ASGI bridge could hang process exit: `asyncio.to_thread`
  executor threads (non-daemon) blocked forever on the bounded send queue
  or the cancel event. Replaced with cancellable `put_nowait` polling —
  real backpressure, no executor threads.
- Empty `Content-Length` headers (sent by wsgiref on GETs) reached the
  protocol layer through Django scopes and were rejected as BAD_REQUEST;
  they are now stripped like the WSGI environ path.
- SSE responses emitted `connection: keep-alive`, a hop-by-hop header WSGI
  forbids — wsgiref asserted and returned 500. The bridge now strips RFC
  7230 hop-by-hop headers (including headers named by the `Connection`
  token) on both the sync and async Django paths.

## [0.4.0] - 2026-11-10

### Added

- `trpc-py codegen zod` (MISSION §2.5): zod v4 schemas for procedure inputs —
  string lengths, regex, numeric ranges, multiple-of, enums, literals,
  optional/nullable, defaults, unions, and `z.discriminatedUnion` for models
  sharing a `Literal` field. Unmappable constraints emit `// dropped:`
  comments; the refusal set matches `generate_types`. Parity verified end to
  end: hypothesis-generated cases (`fixtures/zod-cases.json`, Pydantic
  verdicts) asserted against `safeParse` verdicts in
  `src/zod-parity.test.ts`, plus `z.input`/`z.output` assignability to the
  generated router types. `zod` added as a ts-tests dev dependency.
- Typed errors (ADR 0009): procedures declare `errors=[PostNotFound,
  RateLimited]` — `TRPCError` subclasses with a `code` class attribute and an
  optional Pydantic `data_model`. Declared payloads merge into `error.data`
  on the wire (same shape as a Node `errorFormatter`), and generated
  TypeScript emits `AppRouterErrorShape` — a discriminated union over
  `error.data.code` with per-code model fields, verified end-to-end against
  `TRPCClientError<AppRouter>` narrowing. Reserved keys (`code`,
  `httpStatus`, `path`, `stack`) cannot be overridden. Propagation covers
  JSON envelopes, SSE `serialized-error` frames, and REST error bodies.
- `error.data.path` on procedure-level error envelopes, matching the
  official `getErrorShape` (previously absent).
- `validate_error_class` public helper; `error_data_payload` payload
  extraction shared by the ASGI adapter, SSE frames, and REST.
- `@trpc-rs-py/link` npm package (ADR 0008): `migrationLink` routes
  operations to Python when the path is under the `py.` prefix or in an
  explicit `procedures` list — subscriptions included (SSE, no Node hop) —
  and `MergedAppRouter<TNode, TPy>` deep-merges the Node and generated
  Python router records with Python winning conflicts and `errorShape`
  widened to a union. Paths are never rewritten.
- `examples/migrate-from-node`: a real Node `@trpc/server` app, a FastAPI
  app holding the moved procedures (`posts.byId` at its original path,
  `py.posts_search` under the prefix), and a merged `createTRPCClient`
  smoke-tested end to end over real HTTP.

### Fixed

- Error-code table now mirrors all 21 official tRPC v11 codes
  (`@trpc/server` 11.25.0), adding `NOT_IMPLEMENTED`, `BAD_GATEWAY`,
  `SERVICE_UNAVAILABLE`, `GATEWAY_TIMEOUT`, `PAYMENT_REQUIRED`,
  `UNSUPPORTED_MEDIA_TYPE`, `UNPROCESSABLE_CONTENT`, `PRECONDITION_REQUIRED`,
  and `TOO_MANY_REQUESTS` — and correcting three wrong data codes:
  `BAD_REQUEST` -33600→-32600, `PAYLOAD_TOO_LARGE` -32007→-32013,
  `CLIENT_CLOSED_REQUEST` -32012→-32099.

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
