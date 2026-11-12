# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-beta.1] - 2026-11-12

First beta. The public API is now frozen under `docs/api-freeze.md` — changes
to listed symbols require a deprecation cycle through the 1.0.0 release. All
twelve `docs/production-readiness.md` items are verified: official-client
protocol compatibility, live type sync, subscriptions, streaming helpers,
security limits/audits, a one-hour-per-target fuzz soak (~424M executions, zero
crashes), 93% Python / 90% Rust shipped-code coverage, cross-platform abi3
wheels (CPython 3.10–3.14), and 25 example smoke tests gated in `just ci`.

No code changes since 0.9.0; this tag marks the API freeze point.

## [0.9.0] - 2026-11-12

### Fixed

- `nest()` now rewrites `Procedure.path` to the full dotted path, so
  `error.data.path`, middleware `Call.path`, and timeout messages report
  `posts.by_id` instead of `by_id` — matching the official `getErrorShape`.
- Feature matrix: stripped two phantom trailing cells per row.

### Added

- Combination + feature examples (MISSION §9): `fastapi-react-vite`
  (`@trpc/react-query`), `flask-nextjs`, `django-vanilla-ts`,
  `react-zod-form`, `feature-auth-context`, `feature-batching`,
  `feature-error-handling`, `feature-typed-errors`, `feature-pydantic-vs`,
  `standalone-server`, `python-client`, `rest-and-trpc`,
  `merged-routers`, `shared-router` — all smoke-tested in `just ci`.
- `scripts/wheels.sh` — cross-platform wheel matrix (abi3, CPython
  3.10–3.14): 7 targets, 6 install-verified (`docs/wheels.md`).
- `scripts/coverage.sh` — unit (`cargo llvm-cov`) + integration
  (instrumented `.so` driven by pytest) coverage; `docs/coverage.md`.
- `scripts/check_matrix.py` — feature-matrix drift guard wired into
  `ci-docs` (27 rows, 215 test links).
- TypeDoc for `packages/link` and `cargo doc` in the docs gate.
- Docs: getting-started, core concepts, starter, per-framework guides,
  TypeScript client guide, live-type-sync, AI streaming, Zod codegen,
  deployment, troubleshooting, versioning, API reference (mkdocstrings),
  API freeze inventory, plus architecture diagrams (FFI, batching,
  subscription/cancellation, type sync, migration routing).
- README demo GIF (`live-types.gif`, recorded via
  `vhs docs/launch/live-types.tape`).

## [0.8.0] - 2026-11-11

### Added

- `Limits(procedure_timeout=)` (ADR 0015): each handler invocation is
  bounded by `asyncio.wait_for`; expiry produces the `TIMEOUT` envelope
  (-32008 / HTTP 408) per call, including inside batches. Subscriptions
  are excluded; middleware time is not counted. Negative/non-finite
  values fail at construction. (Not enforced on `mount_rest`.)
- `SECURITY.md` — vulnerability reporting policy, supported versions,
  and a deployment hardening checklist.
- `docs/security/threat-model.md` — six threat classes mapped to
  concrete mitigations, plus documented residual risks and the
  structurally-bounded limits (subscriptions-per-connection, per-client
  SSE buffering).
- `fuzz/` — four cargo-fuzz targets over the protocol attack surface
  (GET query parse, POST body parse, batch parse, SSE encode) with a
  shared fuzzed-`Limits` helper; `just fuzz-smoke [seconds]` runs them
  bounded and `just release-check` invokes a 60-second smoke.
- `scripts/audit.sh` + `deny.toml` — dependency audits across
  cargo-deny (advisories/bans/licenses/sources), pip-audit, and pnpm;
  wired into `release-check`. Report: `docs/security/audit-report.md`.
- `docs/limits.md` — every limit knob, its default, enforcement point,
  and the sync-handler caveat.

### Fixed

- `trpc-core` path dependency now declares `version = ">=0.7.0"` —
  cargo-deny's wildcard check passes.

## [0.7.0] - 2026-11-11

### Added

- Standalone Rust server (ADR 0013): `_core.serve` binds hyper 1.x on a
  multi-thread Tokio runtime, synthesizes full ASGI HTTP scopes, and
  drives the existing `mount()` application — so validation, middleware,
  context, CORS/CSRF wrappers, the panel, and SSE cancellation behave
  identically without a second dispatch implementation. Bounded
  backpressure via a 16-chunk response channel; client disconnect wakes
  `receive()`/`send()` so subscription cleanup runs; request bodies are
  capped by `Limits.max_body_size` with the same `PAYLOAD_TOO_LARGE`
  envelope. Blocks GIL-free until Ctrl-C or the asyncio loop stops.
- `trpc_rs_py.serve.serve()` — Python entry point taking a router
  (mounted with the usual options) or an arbitrary ASGI `app=`, plus
  `trpc-py serve --app module:attribute [--host --port --production]`.
- Python client (ADR 0014): `trpc_rs_py.client` —
  `create_client` (stdlib `urllib`) and `create_async_client` (lazy
  `httpx` with an install hint) exposing `query`, `mutation`, `batch`,
  and SSE `subscribe` with `lastEventId` resume, `tracked()` event ids,
  per-call batch errors, and `TrpcClientError` carrying code, message,
  data, and HTTP status.
- `trpc_rs_py.pyclient.generate_python_client` + `trpc-py codegen
  py-client --app M:A --out F` — emits a typed `AppRouterClient` copying
  handler annotations (`ctx`/`lastEventId` excluded), so generated
  clients are strict-mypy clean.
- `docs/performance.md` — first profiling findings: the standalone
  server is ~43% faster than trpc-fastapi on single queries and beats
  plain FastAPI on query/mutation workloads; batch remains
  dispatch-bound.
- Benchmark harness gained a `trpc-standalone` HTTP-load entry.

### Fixed

- Async client `subscribe` reads the response body before parsing error
  envelopes on non-2xx, avoiding `ResponseNotRead` leaks.

## [0.6.0] - 2026-11-10

### Added

- Request context (ADR 0011): `context_factory=` on every adapter entry
  point builds one handler-visible `ctx` per request; a leading `ctx`
  parameter on the handler opts into injection and is excluded from
  input validation, TypeScript/Zod codegen, and OpenAPI.
  `RequestContext` carries method, path, headers, client, and the
  framework-native request object.
- tRPC-style middleware (ADR 0011): `Call`/`Next`/`run_middleware` onion
  chain in `trpc_rs_py.middleware`. Router-level `Router(middleware=[...])`
  and `router.use()` (snapshotted at registration, travels through
  merge/nest); mount-level `middleware=` on all eight entry points runs
  outermost-first. Input validation → chain → output validation;
  subscriptions run the chain once at start; REST shares it. `next(ctx=)`
  replaces the downstream context.
- Built-in middleware helpers: `logging_middleware`, `timing_middleware`,
  `rate_limit` (per-key token bucket → `TOO_MANY_REQUESTS`, thread-safe,
  lazy idle eviction), `require_auth` (sync/async ctx predicate →
  `UNAUTHORIZED`), and `tracing_middleware` (optional `opentelemetry-api`;
  one `trpc.<path>` span per call, hard-fails when the dep is missing).
- `trpc_rs_py.cors.cors()` — deny-by-default ASGI wrapper: preflight
  204/403, exact-origin allowlist, `*`-with-credentials reflects the
  concrete origin, merged `Vary`, response bodies never buffered (SSE
  safe). Works on every adapter through `wsgi_from_asgi`/`asgi_view`.
- `trpc_rs_py.csrf.csrf_protect()` — adapter-level filter on
  credential-carrying requests (Cookie/Authorization): Fetch Metadata
  safe values pass, `cross-site` requires an exact `Origin` match,
  missing metadata falls back to the allowlist. Fixed 403 envelope.
- `/trpc/panel` playground: `panel=` on `mount`/`attach` (forwarded to
  WSGI and Django helpers) serves a vendored zero-dependency UI —
  procedure list, schema-driven input forms, call buttons, live
  EventSource subscription pane — plus `GET {path}/panel/schema`
  introspection JSON. Enabled by default in development, off in
  production unless explicit.
- `trpc_rs_py.testing` — dependency-free in-process clients:
  `test_client(router)` drives the real `mount()` app without sockets
  (`query`/`mutation`/`batch`/`subscribe`, `TrpcTestClientError`), plus
  `flask_test_client` and `django_test_client` wrappers returning each
  framework's own test client.
- Vercel AI SDK helpers (ADR 0012): `ai_sdk_endpoint` serves the AI SDK
  v5 UI message stream contract so `useChat`'s `DefaultChatTransport`
  works with zero custom frontend code; `ai_text_stream`,
  `ai_message_stream`, and `ai_data_part` emit UI-message-stream chunks
  yieldable from tRPC subscriptions too. Disconnect cancels the source
  iterable; backpressure stays pull-based.
- CLI: `check --json` (`{"ok", "procedures"}` / `{"ok": false, "error"}`)
  and enriched `routes --json` with per-procedure `input`, `output`,
  and `errors` entries.
- Docs: `context.md`, `middleware.md`, `cors.md`, `csrf.md`,
  `testing.md`, `playground.md`, `ai-sdk.md`, and ADRs 0011/0012.
- 194 new tests (401 → 595) covering context, middleware semantics,
  security wrappers, the panel, the test clients, and the AI stream.

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
