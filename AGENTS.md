# AGENTS.md — Instructions for any agent working in trpc-rs-py

Read `MISSION.md` (the full project spec) and `MEMORY.md` (current state)
before doing anything. This file covers the operating rules.

## Project summary and positioning

**trpc-rs-py** is a tRPC server for Python backends (FastAPI, Starlette, Flask,
Django, plus an optional standalone Rust server) with a PyO3 Rust core. A
TypeScript app using the official `@trpc/client` calls Python procedures with
full end-to-end types that update live as you edit Python.

Positioning: **"the Python backend for your tRPC app"** — aimed at TypeScript
frontend + Python backend teams, especially AI apps. Every feature must answer
"why not just use FastAPI + OpenAPI + a TS client generator?"

## Repository layout

```
Cargo.toml          # workspace; version source of truth
pyproject.toml      # maturin, python-source = "python"
crates/trpc-core/   # protocol, router, codegen, watcher (pure Rust, no PyO3)
crates/trpc-py/     # PyO3 bindings (thin; module trpc_rs_py._core)
python/trpc_rs_py/  # user-facing API, adapters, CLI, .pyi stubs, py.typed
packages/           # npm packages (migration link, type helpers)
templates/          # trpc-py init templates
examples/           # runnable examples, each with README + smoke test
benchmarks/         # benchmark harness, results JSON, plot script
tests/              # Python unit, integration, protocol, typing tests
clients/ts-tests/   # official @trpc/client compatibility + type tests
docs/               # MkDocs site (built --strict)
scripts/            # local CI and release scripts
justfile            # task runner
```

## Build and test commands (`just` recipes)

- `just ci` — full gate, in order, stop on first failure (see MISSION.md §15).
- `just ci-fast` — steps 1–3 and 5 for quick iteration.
- `just release-check` — `ci` + audits + fuzzers + cross-platform wheels + npm builds + benchmarks.
- `just fmt` / `just lint` / `just test` — individual pieces.
- Build the extension: `uv run maturin develop --release`.
- Docs: `uv run mkdocs serve` / `uv run mkdocs build --strict`.
- Python env: `uv sync --no-install-project` (maturin installs the extension).
- TS: `pnpm` is the package manager; lockfiles are pinned and committed.

## Coding standards

- Rust: `#![deny(unsafe_code)]`; `clippy::pedantic` warnings as errors
  (`-D warnings` in the gate). All FFI `unsafe` lives in one `ffi` module with
  `// SAFETY:` comments, listed in `docs/architecture.md`. Rustdoc on every
  public item.
- Python: `mypy --strict` and `pyright --strict` with zero errors; `py.typed`
  shipped; docstring with an example on every public symbol; ruff format/lint.
- TypeScript: `strict` mode; no `any` in public npm package types; TSDoc on
  public exports; prettier formatting.
- Hot-path work: measure first (`cargo flamegraph`, `py-spy`); every commit
  touching a hot path includes before/after benchmark numbers in the body.
- ADR before any significant decision, in `docs/decisions/NNNN-title.md`.

## Commit rules

- Conventional Commits: `<type>(<scope>): <imperative summary>`.
- One logical, passing change per commit; push after every commit.
- A commit must pass `just ci-fast`; pre-push runs the full gate.
- Never rewrite pushed history. If the gate fails, fix forward.
- Docs, tests, and the feature matrix change in the same commit as the code
  they describe, or the commit right after.

## Subagent rules (MISSION.md §17)

- Subagent delegation is optional; the orchestrator may implement and
  verify work directly.
- Subagents never commit or push. The orchestrator owns git.
- Parallel subagents only with non-overlapping file sets; briefs must state
  allowed paths, acceptance criteria, performance and security expectations.

## Versioning rules

- Single source of truth: `version` in workspace `Cargo.toml`. `pyproject.toml`
  uses `dynamic = ["version"]`. npm packages version in lockstep via
  `scripts/release.sh`.
- 0.x: minor bump for features/breaking, patch for fixes. `1.0.0-beta.1` only
  when `docs/production-readiness.md` is fully checked.
- Each bump: update `CHANGELOG.md` (Keep a Changelog), commit
  `chore(release): vX.Y.Z`, annotated tag `vX.Y.Z`, push.

## Definition of done

Implemented, tested, documented, listed in the feature matrix (cell generated
from a test), and benchmarked if it touches a hot path.

## Things never to do

- No hosted CI (`.github/workflows` or any pipeline files) — everything runs
  locally via `just`.
- No branches, no PRs — one linear `main` history.
- Never bypass git hooks with `--no-verify`.
- Never hand-edit benchmark numbers or results JSON.
- Never let subagents commit or push.
- Never leak internal errors in production mode; error shapes are fixed.
- Never put the Rust core in the headline; it is the reason, not the pitch.
