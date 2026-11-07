# Contributing

Thanks for your interest in trpc-rs-py!

## Setup

```sh
git clone https://github.com/Seyamalam/trpc-rs-py && cd trpc-rs-py
uv sync --no-install-project
uv run maturin develop --release
just ci-fast
```

Requirements: Rust (pinned via `rust-toolchain.toml`), Python 3.10+ (pinned
3.14 via `.python-version` + `uv`), Node with `pnpm`, `just`.

## Workflow

- **No branches, no PRs.** All work is committed to `main` in small, logical,
  passing commits (Conventional Commits). Push after every commit.
- Every commit must pass `just ci-fast`; `pre-push` runs the full `just ci`.
  Never bypass hooks with `--no-verify`.
- Docs, tests, and the feature matrix change together with the code.
- Significant decisions need an ADR in `docs/decisions/` first.

## Coding standards

- Rust: `#![deny(unsafe_code)]`, clippy pedantic clean, rustdoc on public items.
- Python: `mypy --strict` + `pyright --strict` clean, ruff formatted, docstring
  with an example on every public symbol.
- TypeScript: `strict`, no `any` in public types, prettier.

## Testing

- `cargo test --workspace` (unit + property tests).
- `uv run pytest` (Python unit/integration/security).
- `clients/ts-tests/` runs the official `@trpc/client` against every adapter.
- Coverage target: ≥ 90% lines (Rust `cargo llvm-cov`, Python `pytest-cov`).

## Reporting security issues

See [`SECURITY.md`](SECURITY.md) — do not open public issues for
vulnerabilities.
