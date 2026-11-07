# Production Readiness Checklist

Gate for `1.0.0-beta.1` (MISSION.md section 12). Every item needs evidence
linked. The release is blocked until all boxes are checked.

- [ ] All adapters pass the protocol compatibility tests against the official tRPC client.
- [ ] Every differentiator in MISSION.md section 2 is complete, tested, and documented.
- [ ] Feature matrix has no "partial" items in the core protocol rows.
- [ ] Coverage targets met (>= 90% lines, Rust `cargo llvm-cov` and Python `pytest-cov`).
- [ ] Fuzzer ran at least 1 hour per target with no crashes.
- [ ] All dependency audits clean (`cargo audit`, `cargo deny`, `pip-audit`, `pnpm audit`).
- [ ] Threat model written and every listed risk mitigated or documented (`docs/security/threat-model.md`).
- [ ] Benchmarks published, and adapter overhead over a plain JSON view is documented.
- [ ] Wheels build and install for Linux (x86_64, aarch64, manylinux and musllinux), macOS (x86_64, arm64), and Windows (x86_64). npm packages build and pack cleanly.
- [ ] Every example and template runs from a clean checkout following its README.
- [ ] Docs complete (MISSION.md section 14) with no broken links.
- [ ] Public API reviewed and frozen; any later breaking change requires 2.0.
