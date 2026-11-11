# Dependency audit report — 0.8.0 security milestone

Date: 2026-11-11
Scope: Rust workspace (`Cargo.lock`), Python project environment (`uv.lock`),
npm packages (`clients/ts-tests`, `packages/link`).
Runner: `scripts/audit.sh` (exit 0 = all audits pass, 1 = at least one failure).

## Tool versions

| Tool | Version | Notes |
| --- | --- | --- |
| cargo | 1.96.0 | rust-toolchain pinned |
| cargo-deny | 0.20.2 | covers RustSec advisories, bans, licenses, sources |
| cargo-audit | — | not installed; advisories covered by `cargo deny check advisories` (same RustSec advisory DB) |
| uv | 0.11.11 | pip-audit runs as a `uv run --with` overlay |
| pip-audit | 2.10.1 | via `uv run --with pip-audit` |
| pnpm | 11.0.8 | |
| node | v26.7.0 | |

## Commands run

```bash
./scripts/audit.sh
# equivalent to:
cargo deny check                                    # advisories + bans + licenses + sources
uv run --with pip-audit pip-audit --progress-spinner off
(cd clients/ts-tests && pnpm audit)
(cd packages/link && pnpm audit)
```

`cargo audit` was skipped: the binary is not installed on this machine
(`cargo install cargo-audit` not run — slow build). `cargo deny check
advisories` queries the same RustSec advisory database, so coverage is
equivalent for vulnerability detection.

## Results

| Audit | Result |
| --- | --- |
| cargo deny — advisories | PASS (0 advisories, 0 yanked) |
| cargo deny — licenses | PASS |
| cargo deny — sources | PASS (crates.io only) |
| cargo deny — bans | PASS (F1 resolved — was 1 wildcard dependency) |
| pip-audit (Python) | PASS — no known vulnerabilities |
| pnpm audit — clients/ts-tests | PASS |
| pnpm audit — packages/link | PASS |

Overall: `scripts/audit.sh` exits **0**. The wildcard finding below was
resolved.

## Findings

### F1 — Wildcard dependency spec (bans) — LOW, config hygiene

- Package: `trpc-py` → `trpc-core`
- Location: `crates/trpc-py/Cargo.toml:24`
- Detail: `trpc-core = { path = "../trpc-core" }` has no `version` field, so
  cargo-deny's wildcard check treats the requirement as unbounded (`*`).
- Reachability: workspace-internal path dependency only; no registry or git
  source involved. Not exploitable, but fails the "no wildcard deps" policy.
- **Resolved** (orchestrator): `version = ">=0.7.0"` added — a bounded-below
  range rather than an exact pin, so future workspace version bumps do not
  break the constraint. `scripts/audit.sh` now exits 0.

### F2 — Duplicate `syn` versions (bans warning, non-blocking)

- `syn 2.0.119` and `syn 3.0.9` both in `Cargo.lock`.
- `multiple-versions = "warn"` in `deny.toml`, so this does not fail the gate.
- Reachability: `syn 2.x` via `pyo3-macros`/`zerocopy-derive`;
  `syn 3.x` via `serde_derive`, `tokio-macros`, `wasm-bindgen`. All
  build-time proc-macro deps; normal for the ecosystem.

### F3 — pip-audit skip note (informational)

- `trpc-rs-py` itself is reported as "Dependency not found on PyPI and could
  not be audited" — expected for the unpublished project.
- The installed editable package reports version `0.4.0` while the workspace
  is `0.7.0`; the local `.venv` editable install is stale relative to
  `Cargo.toml`. Does not affect third-party audit coverage.

## Could not be audited

- `cargo audit` binary — not installed; substituted by
  `cargo deny check advisories` (same advisory source).
- `pip-audit -r <requirements>` — broken in this environment
  (`ensurepip` SIGABRT on the freethreaded CPython 3.14 interpreter used by
  `uvx`); worked around by auditing the synced project environment via
  `uv run --with pip-audit`, which covers the full `uv.lock` resolved set.
- Examples/templates and benchmark harnesses have no separate lockfiles and
  were not audited separately.
