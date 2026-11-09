# MEMORY.md — trpc-rs-py working memory

> Read at the start of every session. Update in every commit that changes
> project state.

## Current version and milestone

- Version: **0.1.0** (workspace `Cargo.toml`), milestone **0.1.0 Foundation** — in progress.
- Repo: https://github.com/Seyamalam/trpc-rs-py (public), branch `main`, no hosted CI.
- Roadmap: 0.2.0 Core on FastAPI → **0.3.0 Launch version → STOP and ask user** → 0.4.0+ per user-approved plan.

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
- Next: commit + push Task G and the bug fixes (full `just ci` green), run
  the first benchmark suite, then 0.2.0 bump + annotated tag. NOTE: no git
  tags exist yet even though CHANGELOG has a 0.1.0 entry — resolve the tag
  gap at release time.

## What was in progress (superseded)

- Milestone 0.1.0: commit/push sequence for the foundation, benchmark harness
  skeleton, docs skeleton (done), git hooks installation.

## Next tasks in priority order

1. Finish 0.1.0: benchmark harness skeleton (`benchmarks/README.md`, `plot.py`),
   git hooks installed, docs skeleton, feature matrix skeleton — done as part
   of the foundation commits.
2. **0.2.0 Core on FastAPI:** tasks A–G complete (Rust protocol,
   Python Router/errors, PyO3 batch boundary, ASGI adapter, Pydantic v2
   validation, `generate_types` codegen, official-client runtime suite +
   fastapi-basic example). First benchmark suite run and recorded
   (results/plots/headline numbers committed). Remaining:
   a. Commit + push the working tree after full gate green.
   b. Bump to 0.2.0, `chore(release): v0.2.0`, annotated tag, push.
3. Then begin 0.3.0 launch work (live types, AI streaming, OpenAPI/REST,
   starter); stop at launch and ask user to publish/report feedback.

## Open questions

- Pydantic as hard dependency vs optional extra (ADR 0003 keeps it core for now).
- superjson/transformer support order (not scheduled before 0.3.0).
- OpenAPI-in-FastAPI-app integration decision (needed for 0.3.0; ADR planned).
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

- **0.2.0 run** (M5 Pro, 16 conns, 5s; method + raw JSON in
  `benchmarks/results/0.2.0/`, charts in `docs/benchmarks/`): trpc-fastapi
  16,233 req/s single query (p99 1.35 ms) vs plain FastAPI 11,686 req/s
  (p99 2.05 ms) — the mount is faster than a plain JSON view, ~2x on 100 KB
  payloads (6,440 vs 3,214 req/s). Node `@trpc/server` standalone is ~2.2x
  faster on small requests (35,634 req/s) — Python dispatch dominates, as
  expected. Adapter micros are within noise of the baseline (~498 µs both).
  `generate_types`: 128 µs @10, 627 µs @50, 2,520 µs @200 procedures. Rust
  micros: route lookup 7.4 ns, single-call parse 164–228 ns.
- SSE/streaming and live-type-sync latency benches land with 0.3.0.

## User feedback from the launch

- Not yet launched. Per MISSION §19, after 0.3.0 work stops and the user is
  asked to publish + report feedback; record it here.
