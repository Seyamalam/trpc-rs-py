# Benchmarks

Method (MISSION.md section 10). Raw results live in `benchmarks/results/<version>/`
as JSON, produced by the harness — **never hand-edit numbers**. `plot.py`
generates SVG charts into `docs/benchmarks/`.

## Method

Record per run: hardware, OS, Python/Rust/Node versions, worker counts, warmup,
durations. Suites:

1. **Rust micro** (`criterion`, from 0.2.0): parse, route, batch, serialize,
   error path, SSE encoding, TS/Zod/OpenAPI codegen.
2. **Python micro** (`pytest-benchmark`): adapter overhead per framework.
3. **HTTP load** (`oha` or `wrk`): RPS + p50/p95/p99 for single query,
   10-call batch, large payload, mutation, concurrent streaming subscriptions.
4. **Live type sync latency**: save-to-updated-`.d.ts` for 10/50/200-procedure routers.
5. **Comparisons** (same machine, same workload):
   - plain FastAPI / Flask / Django JSON view (baseline overhead),
   - pure-Python protocol implementation (what the Rust core buys),
   - official Node.js `@trpc/server` standalone adapter (reference).

Full suite runs on every minor version bump; history chart across versions.
