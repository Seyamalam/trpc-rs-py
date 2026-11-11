# MEMORY.md — trpc-rs-py working memory

> Read at the start of every session. Update in every commit that changes
> project state.

## Current version and milestone

- Version: **0.6.0 released** (`v0.6.0` tag, release commit + benchmark
  artifacts on `main`); prior: 0.5.0 (`v0.5.0`, artifacts `0e91126`).
  All 0.6.0 scope shipped — context, middleware chain, security
  wrappers, tracing, testing clients, playground panel, AI SDK
  helpers, CLI `--json`.
- Repo: https://github.com/Seyamalam/trpc-rs-py (public), branch `main`, no hosted CI.
- 0.6.0 detail: ADR 0011 landed as two commits — context `1d25ce9`
  (`RequestContext`, `context_factory` on all 5 adapters, leading `ctx`
  injected everywhere and excluded from input/codegen/zod/OpenAPI;
  `docs/context.md`; 40 tests, 401 total) and middleware (this commit:
  `Call`/`Next`/`run_middleware` onion chain in `middleware.py` —
  router-level `Router(middleware=[...])` + `router.use()` snapshotted at
  registration, mount-level `middleware=` on all 8 entry points
  outermost-first; input validation → chain → output validation;
  subscriptions run the chain once at start, REST runs the same chain;
  `logging_middleware`/`timing_middleware`; `docs/middleware.md`; 33
  tests, 434 total; ~0 hot-path overhead). Built-in helpers shipped:
  `rate_limit` (per-key token bucket → TOO_MANY_REQUESTS, threading.Lock
  for WSGI threads, lazy idle eviction) and `require_auth` (sync/async
  ctx predicate → UNAUTHORIZED); `cors()` (deny-by-default ASGI wrapper,
  preflight 204/403, `*`-with-credentials reflects concrete origin) and
  `csrf_protect()` (pre-dispatch filter on credentialed requests —
  Sec-Fetch-Site safe values allow, cross-site requires exact Origin,
  fixed 403 body) — both work on all adapters via the ADR 0010 bridge.
  Developer tooling shipped: `tracing_middleware` (optional
  `opentelemetry-api`, hard-fail RuntimeError when missing, one
  `trpc.<path>` span per call w/ path+kind attrs); `trpc_rs_py.testing`
  (dependency-free in-process `test_client` driving real `mount()` via
  asyncio.run — `query`/`mutation`/`batch`/`subscribe` +
  `TrpcTestClientError`, plus lazy `flask_test_client`/`django_test_client`
  wrappers); `/trpc/panel` playground (`panel=` on mount/attach/WSGI/
  Django — dev-default on, prod off unless explicit; introspection JSON
  at `/panel/schema`, vendored HTML/JS, no CDN); Vercel AI SDK helpers
  in `streaming.py` (ADR 0012 chose a dedicated `ai_sdk_endpoint` ASGI
  app over a custom tRPC transport — `useChat` needs zero custom TS;
  `ai_text_stream`/`ai_message_stream`/`ai_data_part` emit UI-message-
  stream v1 chunks yieldable from subscriptions too); `check --json`
  + enriched `routes --json` (input/output/errors keys). 595 total
  tests. Gotchas: `mypy.stubtest` flattens function-local class defs
  into the module export view (`_TestClient@694` phantom exports) —
  patch instances or hoist classes instead (same class of issue as
  the `@lru_cache` signature mangling noted in the context work).
  Panel introspection uses the full-parameter OBJECT schema for
  inputs (a scalar first-param schema would generate wire-invalid
  forms). HANDOFF.md deleted at release.
- **0.7.0 in progress** (uncommitted→commits on main): ADR 0013 —
  standalone server is a Rust ASGI server in-process (hyper 1.x →
  synthesized ASGI scope → drives `mount()` coroutine via
  `asyncio.run_coroutine_threadsafe`; NO pyo3-async-runtimes — stdlib
  asyncio bridge is identical semantics with one fewer dep). GIL-free
  block via `py.detach`; shutdown on Ctrl-C OR asyncio-loop stop
  (`loop_watch` = signal-free deterministic test hook). 16-chunk mpsc
  backpressure bridge `_AsgiBridge` (arm/start/offer/finish/done);
  `Drop` on response channel wakes both receive() (http.disconnect) and
  send() so subscription cancellation runs; `Limits.max_body_size`
  enforced at transport level with same PAYLOAD_TOO_LARGE envelope.
  `serve.py::serve(router=None, *, app=None, ...)` — router XOR app,
  asyncio loop on `trpc-rs-py-serve` daemon thread, returns 0.
  `trpc-py serve --app M:A [--host --port --production]`.
  ADR 0014 — `client.py`: `create_client` (stdlib urllib) /
  `create_async_client` (lazy httpx w/ install hint), query/mutation/
  batch/subscribe, `TrpcClientError` (.code/.message/.data/.http_status),
  frozen `SubscriptionEvent(data,id)`; batch reads body on HTTP-error
  status (mixed per-call results); async subscribe must `aread()` before
  `.json()` (ResponseNotRead). `pyclient.py::generate_python_client`
  emits `AppRouterClient` copying handler annotations (ctx/lastEventId
  excluded, cast() bodies for strict mypy); CLI `codegen py-client
  --app M:A --out F`. 651 tests (595+56). benchmarks/run.py gained
  `trpc-standalone` server entry (apps.py `__main__` spawns it).
- Roadmap: 0.2.0 (done) → 0.3.0 (done) → 0.4.0 (done) → 0.5.0 (done) →
  0.6.0 (done) → **0.7.0 in progress** (client+standalone landed;
  performance pass + release remaining).

## What is done

- Inspected the fresh maturin template; removed `sum_as_string` and `.github/`.
- Verified both required subagent models are available: **GPT 6 Luna** and
  **GLM 5.3 Flash** (`deepai` provider; native subagent tools are Claude/Codex
  only, so delegation must go through the app's `delegate_task`).
- Restructured into the MISSION §4 layout: `crates/trpc-core` (pure Rust,
  `#![deny(unsafe_code)]`, clippy pedantic), `crates/trpc-py` (PyO3, `ffi`
  module isolates unsafe, module init `_core`), `python/trpc_rs_py` (+`py.typed`,
  `_core.pyi`), plus `packages/ templates/ examples/ benchmarks/ tests/
  clients/ts-tests/ docs/ scripts/`.
- Workspace lints: `unsafe_code = deny`, `clippy::pedantic` warn (gate uses
  `-D warnings`); release profile has LTO + `codegen-units = 1`.
- `rust-toolchain.toml` pinned to 1.96.0; `.python-version` pinned to 3.14;
  `uv.lock` committed.
- Core skeleton: `trpc_core::error::ErrorCode` with the official tRPC v11
  codes, data codes, HTTP status mapping, and error envelope serialization
  (unit-tested). Note: tRPC maps both `PRECONDITION_FAILED` and
  `CLIENT_CLOSED_REQUEST` to data code `-32012`; we mirror that quirk.
- Gates verified green at foundation scale: cargo fmt/clippy/test, ruff
  format+check, mypy --strict, pyright strict, mypy.stubtest, pytest,
  maturin develop --release (module imports; version 0.1.0 visible).

## What is in progress

- Milestone 0.2.0 task A done (commits 29eccc5, 9ff0cac): tRPC v11 parsing,
  envelopes, limits, allocation-free Router in Rust; two GLM 5.3 Flash review
  rounds, all findings fixed and verified.
- Wire-format facts verified against installed @trpc/client v11 source:
  default transformer is identity (raw input JSON, no `{"json":...}` wrap);
  batched GET input is a single `input` param `{"0":..,"1":..}`; POST batched
  body is the same dict with `?batch=1`; mutation with undefined input sends
  NO body; client percent-encodes `+` as `%2B`; batch response HTTP status is
  ignored by the client (per-call envelopes drive errors).
- RESOLVED DECISION: the official client supports `methodOverride: "POST"`
  sending QUERIES via POST. Policy now: queries accepted over GET and POST;
  mutations only over POST (METHOD_NOT_SUPPORTED over GET).
- Task B done (commit 567a19b): Python Router/procedure API (typed
  decorators, merge/nest, charset validation) and TRPCError + data-code
  table; reviewed APPROVE by GPT 6 Luna; follow-ups (self-nest snapshot,
  data_code_for export) applied by the orchestrator with tests.
- Task C done (this commit): PyO3 whole-batch FFI boundary — `Limits`,
  `parse_request`, `ParsedCall`/`ParsedRequest`, `CallResult.ok/error`,
  `build_response`, `TrpcProtocolError` (.message/.code/.data_code/
  .http_status); GIL released around parse and serialize; `ErrorCode::
  from_name` added to core (single source of truth for code names);
  documented big-int input degradation. 41 Rust + 72 Python tests. Two GLM
  5.3 Flash review rounds: round 1 REQUEST_CHANGES (9 findings) — all
  fixed; round 2 REQUEST_CHANGES on two mechanical ruff failures only,
  applied by the orchestrator; reviewer pre-approved the outcome.
- DESIGN NOTE (record here, ADR pending if it grows): for framework adapters
  the Python Router dict is the runtime dispatch table; the Rust Router table
  is used by the standalone server (0.7.0). Rationale: handler lookup is a
  Python dict hit either way; cross-boundary id indirection adds FFI cost.
- Task D done (this commit): pure-ASGI adapter — `mount()` sub-app and
  `attach()` middleware helpers, both Starlette scope conventions; body-size
  limit enforced before reading; application/json enforced on POST (missing
  header rejected); mid-body disconnect aborts without dispatching; websocket
  closed with 1008; production error policy: TRPCError messages pass through
  except INTERNAL_SERVER_ERROR (fixed "Internal server error"); non-TRPCError
  exceptions always redacted in production; signature-bind pre-check
  separates argument errors (400) from handler bugs (500); sync handlers via
  asyncio.to_thread. 101 Python tests. Two GPT 6 Luna review rounds; round 1
  REQUEST_CHANGES (blocker + 4 majors) all fixed; round 2 APPROVE.
- Task E done (this commit): Pydantic v2 input/output validation from handler
  signatures, built once at adapter construction; value-safe input errors,
  output serialization failures normalized; Annotated constraints preserved;
  positional-only, required unannotated, *args/**kwargs invocation supported;
  validation docs, matrix and architecture updated. 130 Python tests. Review
  round 1 found and fixed input-value leakage, lost Annotated metadata and
  output serializer error leaks; round 2 found/fixed Router metadata and
  signature edge cases; round 3 found/fixed stale docs; final round APPROVE.
  GLM 5.3 Flash review endpoint repeatedly failed (502); user authorized
  same-model GPT 6 Luna review. Independence is weaker than the preferred
  cross-model review; this deviation is recorded transparently.
- Task F done (this commit): `generate_types(router) -> str` in
  `python/trpc_rs_py/codegen.py`, exported from the package root, emitting a
  `.d.ts` AppRouter the official `@trpc/client` v11 consumes. Type strategy
  verified against the installed packages: procedure interfaces extend
  official `QueryProcedure`/`MutationProcedure`, `AppRouter` extends official
  `Router<RootTypes, Record>` from `@trpc/server/unstable-core-do-not-import`
  (the officially documented inference import), records are nested type
  aliases, root types declare `transformer: false` (identity JSON) +
  `DefaultErrorShape`. Type mapping: primitives, unions/Optional, Literal,
  Enum (value literal unions), list/Sequence/set/frozenset, fixed + variadic
  tuples, string-keyed dict/Mapping, datetime/date/time/UUID → string,
  Pydantic v2 models (nested, recursive, defaults → `?`, validation-only
  Annotated metadata). Runtime-backed alias handling emits a shared key only
  when accepted at input and emitted at output; asymmetric aliases and
  AliasPath/AliasChoices fail clearly. RootModel, custom serializers/encoders,
  computed/excluded fields, extra=allow, non-ISO temporal settings, and
  Annotated serializer metadata fail instead of producing an inaccurate type.
  Hard `TypeError` naming procedure+annotation for unsupported types; never
  emits `any`. Deterministic; TS keywords, globals, generated imports sanitized
  with collision handling; keys JSON-quoted (injection-safe). Verified by the
  codegen test suite + committed TS fixture: `tsc --noEmit` with official
  `@trpc/client` 11.25.0 passes exact types and load-bearing `@ts-expect-error`
  negatives (control: removing a directive makes tsc fail). `@trpc/server@11.25.0` added as a
  pinned devDependency of ts-tests only (not a runtime dep of the Python
  package). `trpc-py codegen ts` CLI deferred to the 0.6.0 Rust CLI milestone
  per MISSION §8 (no CLI exists yet; nothing faked). Docs: docs/codegen.md,
  index/nav, feature matrix TS codegen row = supported for FastAPI/
  Starlette with test links. 152 Python tests total. GPT 6 Luna review round
  1: REQUEST_CHANGES (3 findings) — alias contract mismatch (codegen emitted
  field-name types while runtime input takes the alias), missing TS
  reserved-word sanitization, stale test count in this file; all three fixed
  and verified with `just ci`. Review round 2 (GPT 6 Luna): REQUEST_CHANGES
  (3 findings), all fixed and verified with `just ci`: (1) alias resolution
  now models the full runtime Pydantic config — input keys come from
  `validation_alias`/`alias` plus the field name under `populate_by_name`,
  output keys from `serialization_alias`/`alias` under `serialize_by_alias`
  (verified empirically against the runtime's `TypeAdapter.dump_python` path);
  asymmetric input/output wire names are rejected with both names in the
  message, `AliasPath`/`AliasChoices` validation aliases are always rejected,
  and a symmetric `serialize_by_alias` alias is emitted using the alias as
  the property key; (2) `Field(exclude=True)` fields are rejected (the
  runtime omits them from serialized output, so the interface would declare a
  property that is never sent); (3) `never` and `undefined` added to the
  reserved-word set (tsc TS2427 rejects them as interface names), keyword
  models + a symmetric `serialize_by_alias` model added to the ts-tests
  fixture, d.ts regenerated, `tsc --noEmit` green with new positive cases;
  docs/codegen.md alias rules rewritten to match. Review round 3 (GPT 6
  Luna): REQUEST_CHANGES (4 findings), all fixed and verified with `just ci`:
  (1) alias resolution now reads the model's effective Pydantic config flags —
  `validate_by_alias` (default True) and `validate_by_name` (default False,
  falling back to legacy `populate_by_name`), verified empirically against
  the installed Pydantic 2.14.3 nested-model validation (`validate_by_alias`
  is not merely "always on": `validate_by_alias=False, validate_by_name=True,
  serialize_by_alias=True` accepts only the field name at input while output
  emits the alias, and the generator now rejects that instead of emitting the
  alias); a matrix test probes accepted/rejected input keys and emitted
  output keys through `build_input_validator`/`build_output_validator` and
  asserts the generator agrees for 11 alias configurations; (2) shape-changing
  serialization features are rejected: `RootModel` (bare scalar wire shape),
  `@field_serializer`/`@model_serializer` (including inherited ones via
  `__pydantic_decorators__`, which aggregates the MRO), `@computed_field`
  (output-only property), and `Field(exclude_if=...)` (exists in 2.14.3;
  conditionally omits output); each rejection backed by a test that first
  shows the runtime wire effect via `TypeAdapter.dump_python`; (3) the
  TypeScript reserved-name set now also covers the built-in generics the
  generated annotations use (`Array`, `Record`, `ReadonlyArray`, ...) plus
  common utility types (`Partial`, `Pick`, `Omit`, `NonNullable`,
  `ReturnType`, ...) and type-context keyword tokens (`keyof`, `infer`,
  `readonly`, `unique`, `is`, `asserts`, `satisfies`, `using`, `accessor`) —
  shadowing `Array`/`Record` would silently capture every emitted
  `Array<T>`/`Record<K, V>`; models named `Array`/`Record`/`infer`/`keyof`/
  `readonly`/`unique` added to the ts-tests fixture (with a real `_Array`
  collision test → `_Array_2`), d.ts regenerated, `tsc --noEmit` green;
  (4) docs/codegen.md alias section and hard-failure list rewritten to the
  exact supported/rejected cases (not "all serialization aliases rejected").
  Review round 4 (GPT 6 Luna) found serializer metadata/config, extra=allow,
  and generated-helper name shadowing gaps. Orchestrator fixes: reject
  Annotated PlainSerializer/WrapSerializer/SerializeAsAny, nonempty
  json_encoders, non-ISO ser_json_temporal for temporal fields, and
  extra=allow; reserve inferRouterInputs/inferRouterOutputs; add runtime-backed
  tests and TS fixture. Final round-4 review by GPT-6 Luna APPROVE (cross-
  model); no blocking findings. `just ci` passed; 173 Python tests.
  Additional runtime-backed tests pin Annotated PlainSerializer output
  (int→string), SerializeAsAny subclass fields, model json_encoders,
  ser_json_temporal seconds output (datetime→number), extra=allow preserving
  unknown fields, and model names colliding with inference-helper imports.
  Docs now list these new hard-failure cases.
- Task G done: the official
  `@trpc/client` 11.25.0 runtime compatibility suite
  (`clients/ts-tests/src/runtime.test.ts`, run via `tsx --test` from
  `scripts/ts-tests.sh`) spawns live FastAPI and Starlette fixture servers
  (`clients/ts-tests/fixtures/{fastapi,starlette}_app.py` sharing
  `fixtures/router.py`) on free localhost ports and calls them with the real
  official client. Per adapter it verifies: typed query, Pydantic-validated
  query, typed mutation, two-query batching in exactly ONE HTTP request
  (request-counting fetch wrapper) with order preserved, query+mutation split
  into separate batches (official batching is per operation type),
  `methodOverride: "POST"` queries, and GET-on-mutation → HTTP 405.
  All 28 runtime tests pass (14 cases × 2 adapters); the suite asserts the
  official wire contract verified against a real `@trpc/server` 11.25.0
  reference server (error envelope captured verbatim) and its installed
  source. `fastapi-basic` example added
  (app.py, generate_types.py, committed app-router.d.ts, README with exact
  clean-checkout commands, smoke.sh green in `scripts/examples-smoke.sh`).
  New dev deps: `uvicorn` (Python), `tsx` + `@types/node` (ts-tests);
  generated `.d.ts` files excluded from prettier via
  `clients/ts-tests/.prettierignore`; both committed d.ts fixtures are
  drift-checked in `scripts/ts-tests.sh`. docs/protocol.md (new) documents
  the wire format, verified envelopes, and the open deviations; feature
  matrix rows updated with runtime-test links; mkdocs nav updated. All other
  gates green (cargo fmt/clippy/test 41, maturin, mypy/pyright/stubtest,
  pytest 173, prettier/tsc, docs strict, example smoke).
- FIXED BUG A (was blocker, Rust core): error envelope now emits the flat
  official v11 shape `{"error":{"message","code":<number>,"data":{"code",
  "httpStatus"}}}` with NO `json` wrapper (`crates/trpc-core/src/json.rs`,
  `error.rs`); `tests/test_asgi.py` and `tests/test_core_boundary.py` updated
  to pin the official shape. Verified by the runtime suite: TRPCError,
  unknown procedure, validation and method-enforcement errors all surface
  with code/shape/data in the official client.
- FIXED BUG B (was blocker, Rust core batch parsing): missing batch index
  keys now mean no-input (`null`) for that call, and an all-undefined batch
  (`input={}`) is accepted; present indexes must still be numeric and within
  the batch (`crates/trpc-core/src/protocol.rs batch_inputs`). Verified by
  the runtime suite: no-input queries and mutations work through
  `httpBatchLink`.
- FIXED (test-side): the batched GET-on-mutation runtime case expected a
  single envelope; the official server enforces the method PER CALL (inside
  its `calls.map` loop, `resolveResponse` dist line ~1819), so a batch array
  is correct. Official batch status rule verified in `getHTTPStatusCode`:
  uniform per-call status → that status; differing statuses → 207. Test now
  asserts the array shape; 28/28 green.
- Cosmetic deviation (non-blocking, documented): a batched response whose
  per-call HTTP statuses differ returns the first error's status instead of
  the official 207; the official client ignores the batch status and uses
  per-call envelopes, so official-client traffic is unaffected. Optional
  alignment: compute the status set in `build_response` (uniform → that
  status, differing → 207).
- SESSION 2026-11-09: GPT 6 Luna / GLM 5.3 Flash delegation is unavailable
  in this environment (no `delegate_task` tool; `list_agents` shows only the
  default Qoder agent, no teams). Per the user's explicit instruction to
  proceed and finish the project, the orchestrator implements and verifies
  directly (precedent: orchestrator-applied fixes in tasks B/C/E/F); NO
  other-model subagent is substituted. Deviation from the MISSION §17
  cross-model review rule recorded transparently; re-enable cross-model
  review when the two models are reachable again.
- RE-CHECKED 2026-11-09 (post-0.2.0): still unavailable — subagent tools
  expose only Claude profiles (`subagent_explore`/`subagent_general`) and no
  MCP server provides `delegate_task`. Continuing direct implementation
  under the same recorded deviation.
- RULE CHANGE 2026-11-10: the user removed the mandated cross-model
  (GPT 6 Luna / GLM 5.3 Flash) subagent review. MISSION §17 and AGENTS.md
  updated — subagent delegation is now optional; the orchestrator
  implements and verifies directly. The deviations recorded above are
  closed; no model-availability re-checks needed.
- Next: commit + push Task G and the bug fixes (full `just ci` green), run
  the first benchmark suite, then 0.2.0 bump + annotated tag. NOTE: no git
  tags exist yet even though CHANGELOG has a 0.1.0 entry — resolve the tag
  gap at release time.

## What was in progress (superseded)

- Milestone 0.1.0: commit/push sequence for the foundation, benchmark harness
  skeleton, docs skeleton (done), git hooks installation.

## Next tasks in priority order

1. **0.3.0 released** (`v0.3.0`, commit `dcbc7c9`). Landed: SSE subscriptions
   end to end (Rust `SseEncoder`, `@router.subscription` + `tracked`,
   disconnect cancellation, codegen `SubscriptionProcedure`, official-client
   runtime tests), `trpc-py dev` live type sync + argparse CLI (`dev`,
   `codegen ts`, `codegen openapi`, `routes`, `check`, `doctor`), REST/OpenAPI
   output (`RestRoute` opt-in, `mount_rest`, `generate_openapi`,
   `merge_openapi`, `attach(rest_path)`, openapi-typescript client verified
   live), `trpc-py init` Next.js+FastAPI template, `ai-chat`/`live-types`
   examples, `docs/launch/` materials, 0.3.0 benchmark run, and a
   SIGINT-responsiveness fix in the dev watcher loop.
2. **Launch handoff (MISSION §19):** user publishes the 0.3.0 release + draft
   announcement (`docs/launch/announcement.md`) and reports feedback.
   (0.4.0 proceeded anyway on an explicit "continue".)
3. **0.4.0 released.** Done: ADR 0008 + ADR 0009 `1f0dc87`; typed
   errors + wire-table fix `6572c0a` — `errors=[...]` declarations,
   `TRPCError` subclass `code`/`data_model`, payload merge into `error.data`
   with reserved-key immunity across JSON/SSE/REST, `data.path` on procedure
   envelopes, full 21-code official table (3 wrong data codes fixed),
   `AppRouterErrorShape` discriminated union verified against
   `TRPCClientError<AppRouter>` live. Zod codegen `3b4cadc` —
   `codegen zod` (zod v4 input schemas, `// dropped:` comments for unmappable
   constraints), `--zod-out` on `trpc-py dev`, hypothesis parity suite.
   Migration link `e675072` — `packages/link` (`@trpc-rs-py/link`):
   `migrationLink` (prefix `py.` + explicit `procedures`, subscriptions over
   SSE, no path rewriting, `dist/` ESM build) + `MergedAppRouter` (deep
   record merge, Python wins, `errorShape` union); `examples/migrate-from-node`
   smoke-tested live. Released as `v0.4.0` (`cdf048c`); no hot-path changes, so
   the 0.3.0 benchmark numbers stand. Next: agree 0.5.0 scope with the user
   (MISSION leftovers — Flask/Django adapters, standalone server, JSONL
   streams, more npm packages).
- 0.5.0 released (`v0.5.0`, release commit `f44ddc7`; benchmark artifacts
  `0e91126`). ADR 0010 settled the design: ONE dispatch path — sync contexts
  drive the ASGI app on a per-request pumped event loop in a daemon thread
  (`_bridge.serve_asgi_sync`, bounded Queue(maxsize=1) for real backpressure,
  `close()` cancels). Shipped: `mount_wsgi`/`attach_flask`/`wsgi_from_asgi`
  (WSGI + Flask), `trpc_rs_py.django` (`trpc_view`/`trpc_view_async`/
  `trpc_urlpatterns` + generic `asgi_view`/`asgi_view_async` for REST mounts —
  converter name `path`). Compat suite runs all 102 official-client tests
  against five adapters (fastapi, starlette, flask, django_sync,
  django_async); 361 pytest; 7 example smokes + 3 template smokes in the
  gate. Bugs the new smokes caught: (1) `asyncio.to_thread` executor threads
  (non-daemon) blocked forever on the full send queue → hung pytest/push;
  replaced with cancellable `put_nowait` polling. (2) wsgiref sends
  `Content-Length: ""` on GETs → stripped in `_scope_from_request`. (3) SSE
  `connection: keep-alive` is hop-by-hop → `strip_hop_by_hop` in the bridge
  (wsgiref asserted otherwise). Templates `fastapi-only`/`flask-only`/
  `django-only` scaffold via `trpc-py init --template`; each has a gate smoke
  (scaffold→serve→curl tRPC+REST+SSE+OpenAPI).

## Open questions

- Pydantic as hard dependency vs optional extra (ADR 0003 keeps it core for now).
- superjson/transformer support order (not scheduled before 0.3.0).
- JSONL stream calls (`trpc-accept: application/jsonl`) recognized but JSONL
  responses unimplemented — batched stream calls get JSON envelopes.
- Subagent-model rule: removed 2026-11-10 at user request (MISSION §17 +
  AGENTS.md rewritten). Delegation is optional on any available model;
  direct implementation is the default.
- serde_json silently degrades input integer literals beyond the u64 range to
  floats (documented on `ParsedCall.input`); follow up with
  `arbitrary_precision` or core-level rejection before 1.0 (filed from task C
  review round 1).
- Hot-path allocations noted for the benchmark pass (0.2.0/0.7.0):\  body
  `to_vec()` copy in `parse_request` and batch `Value` deep-clone in
  `build_response` under the GIL (unavoidable with frozen pyclasses until
  profiled).

## Known issues and gotchas

- `uv sync` must be called as `uv sync --no-install-project`; plain `uv sync`
  tries to build the maturin project and fails on the virtual workspace.
- maturin needs `[tool.maturin] manifest-path = "crates/trpc-py/Cargo.toml"`
  because the root `Cargo.toml` is a virtual (workspace-only) manifest.
- The PyO3 module init function must be named `_core` (leaf of
  `trpc_rs_py._core`); `PyInit__core` is what maturin expects in mixed layout.
- `#[pymodule]` macro expansion contains `unsafe`; `deny(unsafe_code)` only
  works with the `ffi` submodule carrying `#![allow(unsafe_code)]` + SAFETY docs.
- clippy `doc_markdown` fires on `PyO3`, `CPython`, `SCREAMING_SNAKE_CASE` in
  rustdoc — backtick them.
- mypy wants `assert_type` from `typing_extensions` even on 3.14.
- Git hooks live in `.githooks/` via `core.hooksPath` (NOT `.git/hooks/`);
  `just hooks` (re)installs them.

## Recent decisions (ADRs)

- ADR 0001: Python >= 3.10; wheels for 3.10–3.14. (`docs/decisions/0001-python-version-support.md`)
- ADR 0002: `serde_json` now; isolation module; revisit with 0.2.0 benchmarks. (`docs/decisions/0002-json-library.md`)
- ADR 0003: Pydantic v2 first-class; internal `SchemaProvider` protocol for later libs. (`docs/decisions/0003-schema-libraries.md`)
- ADR 0004: standalone server on hyper 1.x + http-body-util, no axum. (`docs/decisions/0004-standalone-server-framework.md`)
- ADR 0005: standalone OpenAPI doc under the REST mount + opt-in `merge_openapi`
  for FastAPI's own `/openapi.json`. (`docs/decisions/0005-openapi-document-placement.md`)
- ADR 0006: Python `argparse` CLI now (`dev`/`init`/`codegen`/`routes`/`check`/
  `doctor`); clap port re-evaluated at 0.6.0. (`docs/decisions/0006-cli-implementation-language.md`)

## Latest benchmark headline numbers

- **0.5.0 run** (M5 Pro, 16 conns, 5s; method + raw JSON in
  `benchmarks/results/0.5.0/`, charts in `docs/benchmarks/`): trpc-fastapi
  15,079 req/s single query (p99 2.13 ms) vs plain FastAPI 11,808 req/s
  (p99 1.89 ms) — the mount is ~28% faster than a plain JSON view, ~2x on
  100 KB payloads (6,522 vs 3,161 req/s). Node `@trpc/server` standalone is
  ~2.3x faster on small requests (35,117 req/s) — Python dispatch dominates,
  as expected. ASGI adapter micro ~512 µs (within noise of the ~556 µs
  baseline); WSGI ~1,536 µs and Django sync ~1,604 µs — the ~1 ms pumped-loop
  + worker-thread bridge cost, documented in docs/adapters.md.
  `generate_types`: 139 µs @10, 686 µs @50, 2,795 µs @200 procedures. Rust
  micros: route lookup 7.7 ns, single-call parse 179–238 ns.
- SSE/streaming load benchmarks still deferred (functional coverage shipped
  with 0.3.0); WSGI/Django HTTP-load (oha) baselines deferred to 0.7.0.

## User feedback from the launch

- v0.3.0 released 2026-11-09; the user has been asked to publish the release
  + announcement and report feedback. Not yet received — record it here when
  it arrives.
