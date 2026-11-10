# Project Mission: trpc-rs-py — The Python Backend for Your tRPC App

You are a Principal Systems Engineer (Rust, Python, TypeScript) building **trpc-rs-py**: a tRPC server for Python backends (FastAPI, Starlette, Flask, Django) with a Rust core built on PyO3 + maturin.

**The goal in one sentence:** a TypeScript app using the official `@trpc/client` / `@trpc/react-query` can call procedures defined in Python with full end-to-end types that update live as you edit Python, and the adapter adds near-zero overhead over a plain JSON endpoint.

**Positioning:** lead with "the Python backend for your tRPC app", aimed at teams with a TypeScript frontend and a Python backend, especially AI apps. The Rust core is the reason it is fast and ships as one binary. It is not the headline. Every feature must answer the question "why not just use FastAPI + OpenAPI + a TS client generator?"

Definitions used in this document:
* **Wire-compatible** means: the official tRPC v11 client works against our server unchanged. Prove it with tests that run the real `@trpc/client` against every adapter.
* **Production ready** means: every item in section 12 is checked off, with evidence linked.
* **Done** for any feature means: implemented, tested, documented, listed in the feature matrix, and benchmarked where it touches a hot path.

## 1. Starting Point
The project lives in the current directory (`trpc-rs-py`). It is a fresh `maturin` + PyO3 project: `Cargo.toml` (PyO3 0.29, edition 2024, `cdylib`), `pyproject.toml`, and `src/lib.rs` with the template `sum_as_string` function. Git is initialized on `master` with no commits.

Before writing code, inspect these files, then restructure into the layout in section 4. Remove the template function.

## 2. Differentiators
These features are the reason people will choose this project. Build them to a high standard, and give each one a docs page, an example, and a demo script.

### 2.1 Live type sync (`trpc-py dev`)
* `trpc-py dev --app <module:router> --out <path>` watches Python source files (Rust `notify` crate), reloads the router in a subprocess, and regenerates the TypeScript types (and Zod schemas, section 2.5) on every save.
* Write output atomically and only when the content changed, so the TypeScript language server doesn't reload needlessly.
* Target: under 1 second from save to updated `.d.ts` for a 200-procedure router. Benchmark this and publish the number.
* Import errors and type errors in the Python router are printed clearly in the terminal, and the last good output is kept.
* `dev` can also start the Python server with reload, so one command runs the whole backend side of development.

### 2.2 Gradual migration from Node tRPC
* Teams with an existing Node.js tRPC app can move individual procedures to Python one at a time, without rewriting the frontend.
* Ship an npm package (for example `@trpc-rs-py/link`) that provides:
  * A client link that sends procedures under a chosen prefix (for example `py.*`) to the Python server and everything else to the Node server.
  * A type helper that merges the Node `AppRouter` and the generated Python router type into one type.
* Write an ADR comparing this client-side link with a server-side proxy in the Node router, and support at least one of them fully.
* The npm package version moves in lockstep with the Python package version.

### 2.3 AI streaming as a first-class feature
* A Python `async def` generator procedure becomes a typed tRPC subscription over SSE (tRPC v11 `httpSubscriptionLink`), including tracked events and resume with `lastEventId`.
* When the client disconnects, cancel the generator, so an in-flight LLM call stops and stops costing money. Test this.
* Handle backpressure: a slow client must not make the server buffer without limit.
* Helpers that pass OpenAI and Anthropic SDK streams straight through as typed events (text deltas, tool calls, usage, finish reason).
* Compatibility with the Vercel AI SDK on the frontend (`useChat`). Write an ADR on how: implementing the AI SDK's UI message stream protocol on an endpoint, or a custom transport over the tRPC subscription.

### 2.4 OpenAPI output from the same router
* The same router can also serve REST endpoints and an OpenAPI 3.1 document, so adopting the project carries no lock-in: TypeScript frontends use tRPC, and every other client uses REST.
* Procedures opt in with metadata (HTTP method and path). Serve interactive docs (Scalar or Swagger UI).
* For FastAPI, decide in an ADR whether these routes appear in the app's existing `/openapi.json`.
* Validate the generated document with an OpenAPI validator in tests, and test that a generated client (for example `openapi-typescript`) works against it.

### 2.5 Zod schemas generated from Pydantic
* `trpc-py codegen zod` emits Zod schemas for procedure inputs, so frontend forms validate with the same rules as the backend.
* Map constraints: string lengths, regex patterns, number ranges, enums, literals, optional and nullable fields, unions, and discriminated unions.
* List every Pydantic feature that can't be mapped in the docs, and emit a comment in the output where a constraint was dropped.
* Parity test: generate values with `hypothesis`, run them through both the Pydantic model and the generated Zod schema, and assert they accept and reject the same values.

### 2.6 Typed errors
* Procedures declare the errors they can raise (for example `errors=[NotFound, RateLimited]`, each a Python class with typed data).
* The generated TypeScript gives the client a discriminated union of those errors, so the frontend can `switch` on the error code with full types.
* Stay compatible with the official tRPC error shape, using the error formatter mechanism for the extra data.

### 2.7 Built-in playground
* A small web UI at `/trpc/panel` that lists procedures with their input and output schemas, generates input forms from the schemas, calls procedures, and shows streaming subscriptions live.
* Enabled by default only in development mode and disabled in production mode unless explicitly turned on, with a warning in the docs.
* Ship the UI as static assets inside the wheel. No CDN requests.

### 2.8 One-command starter
* `trpc-py init` scaffolds a working project from a template. The main template is a Next.js + FastAPI monorepo (pnpm + uv) with tRPC, react-query, live type sync, one query, one mutation, and one streaming chat example already wired up.
* Other templates: FastAPI only, Flask only, Django only, and "add to an existing Node tRPC app" (section 2.2).
* Target: from `pip install` to the first typed call working in the browser in under 2 minutes, following the README. Test the starter from a clean directory in the local CI gate.

## 3. Architecture
* **Rust core (`crates/trpc-core`)**, pure Rust with no PyO3 dependency so it can be tested and benchmarked on its own:
  * tRPC v11 HTTP protocol: query (GET) and mutation (POST) handling, request batching (`?batch=1`), the result envelope (`{"result":{"data":...}}`), the error envelope and error codes, and HTTP status mapping.
  * Subscriptions over Server-Sent Events.
  * Router table: procedure path lookup with no allocation on the hot path.
  * JSON parsing and serialization (`serde_json`, or `sonic-rs` / `simd-json` if benchmarks justify it; record the choice in an ADR).
  * Request limits: body size, batch size, JSON nesting depth, timeouts.
  * Code generation: TypeScript types, Zod schemas, OpenAPI documents.
  * The file watcher for `trpc-py dev`.
* **Bindings (`crates/trpc-py`)**: PyO3 module exposed as `trpc_rs_py._core`. Keep it thin. Never cross the Python–Rust boundary per field or per item in a loop. Pass whole requests and whole batches in one call, and release the GIL with `Python::detach` during parsing, serialization, and I/O.
* **Python package (`python/trpc_rs_py`)**: the user-facing API (router, procedure decorators, context, middleware, errors, streaming helpers) and the framework adapters:
  * ASGI adapter (FastAPI, Starlette, any ASGI app).
  * WSGI adapter (Flask, any WSGI app).
  * Django adapter (sync and async views, URL include helper).
  * Optional standalone server: a Rust HTTP server (`hyper` or `axum`) that calls Python handlers, for maximum throughput without a Python web framework.
* **npm packages (`packages/`)**: the migration link and type helpers (section 2.2), and any frontend helpers for AI streaming.
* **Validation:** input and output schemas come from Python type hints. Support Pydantic v2 first. Decide in an ADR whether to also support `msgspec`, dataclasses, and `TypedDict`.
* **Python client (`trpc_rs_py.client`)**: a typed sync and async client for calling a tRPC server from Python, with generated stubs.

Record every significant decision in `docs/decisions/NNNN-title.md` (ADR format: context, options, decision, consequences) before implementing it.

## 4. Repository Layout
```
Cargo.toml                 # workspace; version source of truth
pyproject.toml             # maturin, python-source = "python"
crates/trpc-core/          # protocol, router, codegen, watcher (pure Rust)
crates/trpc-py/            # PyO3 bindings
python/trpc_rs_py/         # Python API, adapters, CLI entry, .pyi stubs, py.typed, panel assets
packages/                  # npm packages (migration link, type helpers)
templates/                 # `trpc-py init` templates
examples/                  # runnable examples (section 9)
benchmarks/                # benchmark harness, results JSON, plot script
tests/                     # Python unit, integration, protocol, typing tests
clients/ts-tests/          # TypeScript tests using the official tRPC client
docs/                      # MkDocs site
scripts/                   # local CI and release scripts
justfile                   # task runner entry points
AGENTS.md  MEMORY.md  README.md  CHANGELOG.md  SECURITY.md  CONTRIBUTING.md
```

## 5. Type Safety
* **Rust:** `#![deny(unsafe_code)]` and `clippy::pedantic` warnings as errors, with documented exceptions. Any `unsafe` needed for FFI is isolated in one module, carries a `// SAFETY:` comment, and is listed in `docs/architecture.md`.
* **Python:** `mypy --strict` and `pyright --strict` pass with zero errors. Ship `py.typed`. Every `#[pyclass]` and `#[pyfunction]` has a `.pyi` stub, checked against the compiled module with `python -m mypy.stubtest`.
* **Procedure types:** the input and output types of a decorated procedure are inferred by mypy and pyright (use `ParamSpec`, generics, and overloads). Prove it with `tests/typing/` files using `assert_type`.
* **End-to-end:** generated TypeScript (`AppRouter`, typed errors, Zod schemas, merged Node + Python router types) type-checks with `tsc --noEmit` in the TypeScript test project, including tests that are expected to fail type-checking (wrong input shape, unknown procedure, unhandled error code).
* **npm packages:** TypeScript `strict` mode, no `any` in public types.
* If full type safety is not possible for some feature, document the limitation in the feature matrix and the docs. Don't hide it.

## 6. Performance
Performance is a primary goal. Treat regressions as bugs.
* Hot paths (request parsing, routing, batching, serialization, error formatting, SSE event encoding) live in Rust.
* Minimize allocations and copies. Profile with `cargo flamegraph` and `py-spy` before optimizing, and record findings in `docs/performance.md`.
* Every commit that touches a hot path includes before/after benchmark numbers in the commit body.
* Build release wheels with LTO and `codegen-units = 1`.

## 7. Security
* Mutations only over POST. Queries over GET must be side-effect free; document the CSRF implications and ship a CSRF helper for cookie-based auth.
* Enforced, configurable limits from the first public release: request body size, batch size, JSON depth, procedure timeout, subscription count per connection, SSE buffer size per client.
* Production mode never leaks stack traces or internal error messages. Error output is a fixed, documented shape.
* CORS is deny-by-default with an explicit allowlist helper.
* The playground (section 2.7) and REST docs are off in production mode unless explicitly enabled.
* `trpc-py dev` is development-only and refuses to run when production mode is set.
* Fuzz the protocol parser with `cargo-fuzz` and run the fuzzer as part of the release gate.
* Dependency audits: `cargo audit`, `cargo deny`, `pip-audit`, `npm audit` for the npm packages and the TS test project.
* Write `docs/security/threat-model.md` and `SECURITY.md` (how to report vulnerabilities).

## 8. CLI, Tools & Helpers
* **CLI:** `trpc-py`, implemented in Rust with `clap` and exposed as a console script (`[project.scripts]`) that calls into the extension module, so it ships inside the same maturin wheel. Commands:
  * `init`: scaffold a project from a template (section 2.8).
  * `dev`: live type sync and dev server (section 2.1).
  * `routes`: list all procedures with input, output, and error types.
  * `codegen ts`, `codegen zod`, `codegen openapi`, `codegen py-client`: one-off code generation.
  * `serve`: run the standalone Rust server.
  * `check`: validate a router (duplicate paths, untyped procedures, missing schemas, errors not declared).
  * `doctor`: print versions and environment info for bug reports.
  * Every command has `--help`, stable exit codes, and `--json` output where useful.
* **Helpers:** context factories, middleware (auth, logging, timing, rate limiting), `TRPCError` with the official codes, typed error classes, CORS and CSRF helpers, OpenAI/Anthropic streaming helpers, OpenTelemetry tracing hooks, and test utilities (an in-process test client for each framework).

## 9. Examples
Every example is runnable, has its own README with exact commands, and is smoke-tested by the local CI gate.
* **Launch demos** (built first, section 19):
  * `ai-chat`: Next.js + FastAPI streaming chat with OpenAI or Anthropic, using the streaming helpers and cancellation on disconnect. Runs against a mock LLM when no API key is set, so the smoke test needs no secrets.
  * `live-types`: the Next.js + FastAPI starter showing a Python field rename turning into a TypeScript error.
  * `migrate-from-node`: an existing Node tRPC app with two procedures moved to Python using the migration link.
* **One per framework:** `fastapi-basic`, `starlette-basic`, `flask-basic`, `django-sync`, `django-async`, `standalone-server`.
* **Combinations:**
  * FastAPI + React (Vite) with `@trpc/react-query`.
  * Flask + Next.js.
  * Django + vanilla TypeScript client.
  * FastAPI and Flask apps sharing one router module.
  * One app with several mounted routers (merged and nested routers).
  * One router serving tRPC and REST + OpenAPI at the same time.
  * Python server + Python client.
* **Features:** auth middleware with context, batching, typed errors, Zod form validation in React, error handling, file upload if supported, Pydantic vs. other schema libraries.

## 10. Benchmarks
* **Rust micro-benchmarks** (`criterion`): parse, route, batch, serialize, error path, SSE encoding, TypeScript/Zod/OpenAPI codegen.
* **Python micro-benchmarks** (`pytest-benchmark`): adapter overhead per framework.
* **HTTP load tests** (`oha` or `wrk`): requests/sec and p50/p95/p99 latency for a single query, a 10-call batch, a large payload, a mutation, and concurrent streaming subscriptions.
* **Live type sync latency:** save-to-updated-`.d.ts` time for routers of 10, 50, and 200 procedures.
* **Comparisons**, same machine and same workload:
  * The same endpoint as a plain FastAPI / Flask / Django JSON view (baseline overhead).
  * A pure-Python implementation of the protocol (to measure what the Rust core buys).
  * The official Node.js `@trpc/server` standalone adapter (reference point).
* Record the method in `benchmarks/README.md`: hardware, OS, Python/Rust/Node versions, worker counts, warmup, durations.
* Raw results go in `benchmarks/results/<version>/` as JSON. `benchmarks/plot.py` generates SVG charts into `docs/benchmarks/`. Never hand-edit numbers.
* Re-run the full suite on every minor version bump and keep a history chart across versions.

## 11. Tests
* Rust unit tests for every protocol and router path, plus property tests (`proptest`) for parsing and serialization round-trips.
* Python unit tests for the public API and each adapter.
* **Protocol compatibility tests:** the TypeScript project in `clients/ts-tests/` runs the official `@trpc/client` (pinned v11) against every adapter, covering queries, mutations, batching, errors, and subscriptions.
* Differentiator tests: live type sync (edit a file, assert the output updates within the target time), migration link against a real Node tRPC server, stream cancellation on disconnect, backpressure, OpenAPI document validation, Zod/Pydantic parity (section 2.5), typed error unions, playground disabled in production mode, and the starter from a clean directory.
* Typing tests (`tests/typing/`) for mypy and pyright, and `tsc` type tests for all generated TypeScript.
* Security tests for every limit in section 7, plus the fuzz targets.
* Coverage: at least 90% line coverage for Rust (`cargo llvm-cov`) and Python (`pytest-cov`).

## 12. Production Readiness Checklist (gate for 1.0.0-beta.1)
Keep this checklist in `docs/production-readiness.md` with evidence links. 1.0.0-beta.1 can only be released when every item is checked:
* [ ] All adapters pass the protocol compatibility tests against the official tRPC client.
* [ ] Every differentiator in section 2 is complete, tested, and documented.
* [ ] Feature matrix has no "partial" items in the core protocol rows.
* [ ] Coverage targets met.
* [ ] Fuzzer ran at least 1 hour per target with no crashes.
* [ ] All dependency audits clean.
* [ ] Threat model written and every listed risk mitigated or documented.
* [ ] Benchmarks published, and adapter overhead over a plain JSON view is documented.
* [ ] Wheels build and install for Linux (x86_64, aarch64, manylinux and musllinux), macOS (x86_64, arm64), and Windows (x86_64). npm packages build and pack cleanly.
* [ ] Every example and template runs from a clean checkout following its README.
* [ ] Docs complete (section 14) with no broken links.
* [ ] Public API reviewed and frozen; any later breaking change requires 2.0.

## 13. Versioning & Releases
* SemVer. The single source of truth is `version` in the workspace `Cargo.toml`; `pyproject.toml` uses `dynamic = ["version"]` so maturin reads it. Maturin converts `1.0.0-beta.1` to the PEP 440 form `1.0.0b1`. The npm packages use the same version, set by the release script.
* Start at `0.1.0`. While in 0.x: bump the minor version for a new feature or a breaking change, and the patch version for fixes.
* Never go to `1.0.0-beta.1` until section 12 is fully checked. Then `1.0.0-beta.N` → `1.0.0-rc.N` → `1.0.0`.
* Each version bump: update `CHANGELOG.md` (Keep a Changelog format), commit as `chore(release): vX.Y.Z`, create an annotated git tag `vX.Y.Z`, and push the tag.
* `scripts/release.sh` does the bump (Cargo, npm), changelog check, local CI gate, wheel and npm package builds, and tag in one step.

## 14. Documentation
MkDocs Material site in `docs/`, built with `--strict` (warnings fail the build). Code examples in docs are tested.
* `README.md`: opens with the "Python backend for your tRPC app" pitch and the live-type-sync demo GIF, then install, the 2-minute `trpc-py init` quickstart, the three differentiator highlights (live types, AI streaming, no lock-in via OpenAPI), feature summary, link to the feature matrix, latest benchmark chart, and a "Why not FastAPI + OpenAPI codegen?" section that compares honestly.
* Getting started, core concepts (routers, procedures, context, middleware, errors, subscriptions).
* One page per differentiator in section 2.
* One guide per framework, plus the TypeScript client guide, the Node tRPC migration guide, the Python client guide, and the AI streaming guide (OpenAI, Anthropic, Vercel AI SDK).
* CLI reference (generated from `clap`).
* API reference generated from docstrings and `.pyi` stubs (`mkdocstrings`), rustdoc for the Rust crates, and TypeDoc for the npm packages.
* `docs/protocol.md`: the exact tRPC wire format we implement, with any deviations.
* `docs/architecture.md` with Mermaid diagrams: crate and package structure, request lifecycle (HTTP → adapter → Rust parse → route → Python handler → Rust serialize → response), FFI boundary, batching flow, subscription and cancellation flow, live type sync flow, migration link routing.
* Security, performance tuning, deployment (gunicorn, uvicorn, Django under ASGI/WSGI, Docker), troubleshooting, FAQ.
* Versioning policy, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, ADRs.
* Every public Python symbol has a docstring with an example. Every public Rust item has rustdoc. Every public npm export has TSDoc.

### Feature matrix (`docs/feature-matrix.md`)
* Rows: queries, mutations, batching, subscriptions, AI streaming helpers, stream cancellation, middleware, context, error codes, typed errors, input validation, output validation, transformers such as superjson, file uploads, CORS, CSRF, rate limiting, tracing, TS codegen, live type sync, Zod codegen, OpenAPI/REST output, playground, Node migration link, Python client.
* Columns: FastAPI, Starlette, Flask, Django sync, Django async, standalone server, plus a column comparing against official `@trpc/server` and one comparing against FastAPI + OpenAPI codegen.
* Each cell: supported / partial / planned / not supported, with a link to the test that proves it. Generate the cells from test results so the matrix can't drift.

### Launch materials (`docs/launch/`)
* Step-by-step scripts for the three launch demos (section 9), written so the user can record them as GIFs or short videos. Use `vhs` for any terminal-only recordings.
* A draft announcement post for Hacker News, r/Python, and the tRPC Discord, focused on the pitch and the demos.

## 15. Local CI/CD (no GitHub Actions)
There is no hosted CI. Do not create `.github/workflows` or any other hosted pipeline. Everything runs locally.
* `just ci` runs the full gate, in this order, stopping on the first failure:
  1. `cargo fmt --check`, `ruff format --check`, and the TypeScript formatter check
  2. `cargo clippy --workspace --all-targets -- -D warnings`, `ruff check`, and the TypeScript linter
  3. `cargo test --workspace`
  4. `maturin develop --release`
  5. `mypy --strict` and `pyright` on `python/` and `tests/typing/`
  6. `python -m mypy.stubtest trpc_rs_py`
  7. `pytest` (unit, integration, security, differentiator tests)
  8. TypeScript compatibility, type tests, and npm package tests
  9. Example and template smoke tests
  10. `mkdocs build --strict`
* `just ci-fast` runs steps 1–3 and 5 for quick iteration.
* `just release-check` runs `just ci` plus dependency audits, the fuzzers (short run), cross-platform wheel builds (`maturin build --release` with `zig` or Docker for Linux targets), npm package builds, and the benchmark suite.
* Install git hooks via a script: `pre-commit` runs `just ci-fast`, `pre-push` runs `just ci`. Never bypass them with `--no-verify`.
* Pin tool versions (`rust-toolchain.toml`, `uv.lock`, `pnpm-lock.yaml`) so local runs are reproducible.

## 16. Git Workflow
* No branches and no pull requests. All work is committed to the main branch and pushed directly.
* Commit small and often: one logical, passing change per commit. Push after every commit.
* Conventional Commits: `<type>(<scope>): <imperative summary>`, for example:
  * `feat(core): parse batched GET query requests`
  * `perf(core): avoid copying procedure path during routing`
  * `feat(dev): regenerate TypeScript types on Python file save`
  * `feat(stream): cancel generator when SSE client disconnects`
  * `feat(adapter-django): add async view adapter`
  * `test(compat): run official tRPC client against Flask adapter`
  * `docs(matrix): mark subscriptions supported for ASGI`
  * `chore(release): v0.4.0`
* A commit must pass `just ci-fast`, and the pre-push hook runs the full gate. If the gate fails, fix forward in a new commit. Never rewrite pushed history.
* Docs, tests, and the feature matrix are updated in the same commit as the code they describe, or in the commit right after it.

## 17. Subagents
You are the lead orchestrator. You plan, implement, verify, and own git. Subagents are optional helpers; the orchestrator may implement and verify work directly. When subagents are used:
* **Git ownership:** subagents never commit or push. The orchestrator commits and pushes each change. This keeps one linear history without branches.
* **Parallel work:** because there are no branches, subagents share one working tree. Run subagents in parallel only when their file sets don't overlap, and state the allowed paths in each brief. Run everything else in sequence.
* **Task briefs** contain: goal, allowed file paths, acceptance criteria (tests that must pass, docs and matrix rows to update), performance expectations, and security considerations.

## 18. AGENTS.md and MEMORY.md
Create both in the repo root during the first step and keep them current.
* **`AGENTS.md`** (instructions for any agent working in the repo): project summary and positioning, layout, build and test commands (`just` recipes), coding standards, commit rules, the subagent rules from section 17, versioning rules, the definition of done, and things never to do (hosted CI, branches, `--no-verify`, hand-edited benchmark numbers).
* **`MEMORY.md`** (the project's working memory, so a new session can resume without re-discovering everything): current version and milestone, what is done, what is in progress, the next tasks in priority order, open questions, known issues and gotchas, recent decisions with links to ADRs, the latest benchmark headline numbers, and user feedback from the launch (section 19). Read it at the start of every session. Update it in every commit that changes project state.

## 19. Roadmap
Build the launch version first. Each milestone ends with a version bump, passing `just ci`, updated docs and feature matrix, and a benchmark run.
* **0.1.0 Foundation:** workspace restructure, `AGENTS.md`, `MEMORY.md`, `justfile`, local CI gate and git hooks, ADRs, docs skeleton, feature matrix skeleton, benchmark harness.
* **0.2.0 Core on FastAPI:** queries, mutations, batching, errors, and request limits in Rust; router and procedure API in Python; ASGI adapter; `codegen ts`; official client compatibility tests; `fastapi-basic` example.
* **0.3.0 Launch version (first public release):**
  * Live type sync (`trpc-py dev`, section 2.1).
  * AI streaming: subscriptions over SSE, cancellation, backpressure, OpenAI/Anthropic helpers (section 2.3).
  * OpenAPI/REST output (section 2.4).
  * `trpc-py init` with the Next.js + FastAPI template (section 2.8).
  * The `ai-chat` and `live-types` demos, the README pitch, and the launch materials.
  * **Then stop.** Ask the user to publish the release and the announcement, and to report the feedback. Record the feedback in `MEMORY.md` and propose a re-prioritized roadmap for the user to approve before continuing.
* **0.4.0 Migration and types:** Node tRPC migration link (section 2.2), `migrate-from-node` demo, Zod codegen (section 2.5), typed errors (section 2.6).
* **0.5.0 More adapters:** WSGI (Flask) and Django (sync and async), with examples and the remaining templates. Document WSGI streaming limitations.
* **0.6.0 Developer tools:** playground (section 2.7), remaining CLI commands, middleware, CORS, CSRF, rate limiting, tracing, test clients, Vercel AI SDK compatibility.
* **0.7.0 Python client, standalone Rust server,** and the performance pass.
* **0.8.0 Security hardening:** fuzzing, threat model, audits, review of every limit.
* **0.9.x Release hardening:** combination examples, cross-platform wheels, full docs, API freeze, production readiness checklist.
* **1.0.0-beta.1:** only when section 12 is fully checked.

## 20. Immediate First Step
1. Inspect `Cargo.toml`, `pyproject.toml`, and `src/lib.rs`.
2. Check that both subagent models are available. Stop and report if not.
3. Write `AGENTS.md` and `MEMORY.md`.
4. Write the first ADRs: Python version support, JSON library, schema libraries, standalone server framework.
5. Restructure into the layout from section 4, set up the `justfile`, local CI gate, git hooks, and pinned toolchains.
6. Commit and push each step separately, then continue with milestone 0.1.0 through subagents.
7. After each commit, report: what changed, gate results, and the next task.

Also first push this repo to GitHub as a public repo and save all these details in a file.
