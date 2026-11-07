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

- Milestone 0.2.0 (Core on FastAPI), task A complete and committed: tRPC v11
  request parsing (single+batched GET/POST), result/error envelopes, HTTP
  status selection, `Limits` (1 MiB body / 100 batch / depth 32 enforced
  before unbounded work), allocation-free `Router` with merge/nest, JSON
  isolated in `json.rs` (ADR 0002). 32 tests incl. proptests. Two review
  rounds (GLM 5.3 Flash) — round 1 APPROVE with 9 findings, all fixed;
  round 2 APPROVE, no regressions.
- Wire-format facts verified against installed @trpc/client v11 source:
  default transformer is identity (raw input JSON, no `{"json":...}` wrap);
  batched GET input is a single `input` param `{"0":..,"1":..}`; POST batched
  body is the same dict with `?batch=1`; mutation with undefined input sends
  NO body; client percent-encodes `+` as `%2B`; batch response HTTP status is
  ignored by the client (per-call envelopes drive errors).
- RESOLVED DECISION: the official client supports `methodOverride: "POST"`
  sending QUERIES via POST. Policy now: queries accepted over GET and POST;
  mutations only over POST (METHOD_NOT_SUPPORTED over GET).
- Next: task B — Python `Router`/`procedure` API + `TRPCError` (GLM 5.3 Flash
  implementer), then task C — PyO3 whole-batch boundary, task D — ASGI
  adapter + limits wiring, task E — `codegen ts`, task F — official-client
  compat tests + `fastapi-basic` example.

## What was in progress (superseded)

- Milestone 0.1.0: commit/push sequence for the foundation, benchmark harness
  skeleton, docs skeleton (done), git hooks installation.

## Next tasks in priority order

1. Finish 0.1.0: benchmark harness skeleton (`benchmarks/README.md`, `plot.py`),
   git hooks installed, docs skeleton, feature matrix skeleton — done as part
   of the foundation commits.
2. Start **0.2.0 Core on FastAPI** through subagents (GPT 6 Luna implementer /
   GLM 5.3 Flash reviewer and vice versa):
   a. Rust: tRPC v11 GET query parsing (batched), POST mutation parsing, result
      envelope, error envelope, HTTP status mapping (unit + proptest).
   b. Rust: router table (path lookup, no hot-path allocation).
   c. Python: `Router`, `procedure` decorators, `TRPCError`, context.
   d. PyO3: whole-batch request parse + whole-batch response serialize, GIL
      released around parse/serialize.
   e. ASGI adapter (FastAPI + Starlette), request limits (body size, batch
      size, JSON depth).
   f. `codegen ts` (AppRouter .d.ts) + clients/ts-tests official client compat.
   g. `fastapi-basic` example + docs/feature-matrix updates.
3. After 0.2.0: version bump to 0.2.0, CHANGELOG, tag, benchmark run.

## Open questions

- Pydantic as hard dependency vs optional extra (ADR 0003 keeps it core for now).
- superjson/transformer support order (not scheduled before 0.3.0).
- OpenAPI-in-FastAPI-app integration decision (needed for 0.3.0; ADR planned).

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

## Recent decisions (ADRs)

- ADR 0001: Python >= 3.10; wheels for 3.10–3.14. (`docs/decisions/0001-python-version-support.md`)
- ADR 0002: `serde_json` now; isolation module; revisit with 0.2.0 benchmarks. (`docs/decisions/0002-json-library.md`)
- ADR 0003: Pydantic v2 first-class; internal `SchemaProvider` protocol for later libs. (`docs/decisions/0003-schema-libraries.md`)
- ADR 0004: standalone server on hyper 1.x + http-body-util, no axum. (`docs/decisions/0004-standalone-server-framework.md`)

## Latest benchmark headline numbers

- None yet. First full run due with milestone 0.2.0; harness skeleton in
  `benchmarks/` (method documented in `benchmarks/README.md`).

## User feedback from the launch

- Not yet launched. Per MISSION §19, after 0.3.0 work stops and the user is
  asked to publish + report feedback; record it here.
