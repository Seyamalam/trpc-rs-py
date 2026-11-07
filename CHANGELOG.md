# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
