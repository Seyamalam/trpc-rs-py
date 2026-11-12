# Production Readiness Checklist

Gate for `1.0.0-beta.1` (MISSION.md section 12). Every item needs evidence
linked. The release is blocked until all boxes are checked.

- [x] All adapters pass the protocol compatibility tests against the official
  tRPC client (`clients/ts-tests` — 102 official `@trpc/client` v11 runtime
  + type tests across ASGI, WSGI, Django sync/async, and the standalone
  server; runs in `just ci`).
- [x] Every differentiator in MISSION.md section 2 is complete, tested, and
  documented (live type sync — measured 3.5 ms codegen for 200 procedures,
  see `docs/performance.md`; migration link `packages/link`; subscriptions
  with cancellation/backpressure; AI SDK helpers; REST + OpenAPI — all
  test-linked in `docs/feature-matrix.md`).
- [x] Feature matrix has no "partial" items in the core protocol rows
  (verified by `scripts/check_matrix.py` — 27 rows, 215 test links; the
  remaining "partial" cells are in the `@trpc/server` and
  FastAPI+OpenAPI *comparison* columns and describe competitor gaps).
- [ ] Coverage targets met (>= 90% lines, Rust `cargo llvm-cov` and Python `pytest-cov`).
- [ ] Fuzzer ran at least 1 hour per target with no crashes (targets exist
  and pass bounded smoke via `just fuzz-smoke`; the 1-hour soak is
  outstanding — see `docs/security/fuzzing.md`).
- [x] All dependency audits clean (`cargo deny` advisories/bans/licenses/sources,
  `pip-audit`, `pnpm audit` — `scripts/audit.sh` exits 0; see
  `docs/security/audit-report.md`).
- [x] Threat model written and every listed risk mitigated or documented
  (`docs/security/threat-model.md`; residual risks are listed and accepted).
- [x] Benchmarks published, and adapter overhead over a plain JSON view is
  documented (`docs/benchmarks.md` release numbers +
  `benchmarks/results/<version>/` artifacts; ASGI adapter within noise of a
  plain FastAPI JSON view).
- [x] Wheels build and install for Linux (x86_64, aarch64, manylinux and
  musllinux), macOS (x86_64, arm64), and Windows (x86_64)
  (`scripts/wheels.sh` + `docs/wheels.md` — abi3 wheels cover CPython
  3.10–3.14; 6/7 targets install-verified on this machine; the Windows
  wheel is a verified PE32+ build but can't execute without a Windows
  runner — the only residual).
- [x] Every example and template runs from a clean checkout following its
  README (25 smoke tests / 172 assertions green via
  `scripts/examples-smoke.sh`, wired into `just ci`).
- [ ] Docs complete (MISSION.md section 14) with no broken links —
  structure done (all §14 pages + diagrams + nav); honest deferrals:
  README demo GIF (launch material), matrix cells generated *from* test
  results (drift checker is the mitigation today), and docs code-snippet
  extraction tests.
- [x] Public API reviewed and frozen; any later breaking change requires 2.0
  (`docs/api-freeze.md` — full public-surface inventory with stability
  contract for each surface).
