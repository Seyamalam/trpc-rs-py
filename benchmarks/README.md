# Benchmarks

Method (MISSION.md section 10). Raw results live in `benchmarks/results/<version>/`
as JSON, produced by the harness — **never hand-edit numbers**. `plot.py`
generates SVG charts into `docs/benchmarks/`.

## Running

```sh
brew install oha                      # HTTP load generator (once)
pnpm -C clients/ts-tests install      # Node reference deps (once)
just bench                            # full suite + charts
```

`benchmarks/run.py` writes, per version:

- `meta.json` — hardware, OS, Python/Rust/Node/oha versions, workers,
  connections, duration, warmup policy.
- `rust-micro.json` — criterion means/medians/stddev (ns) collected from
  `target/criterion/*/new/estimates.json` after
  `cargo bench -p trpc-core --bench protocol`.
- `python-micro.json` — pytest-benchmark's own JSON export
  (`benchmarks/test_bench.py`: ASGI adapter overhead vs a plain JSON view,
  `generate_types` at 10/50/200 procedures).
- `http-load.json` — raw oha JSON per workload × server: single query,
  10-call batch, mutation, 100 KB payload; servers are the FastAPI tRPC
  mount, a plain FastAPI JSON baseline, and the official Node
  `@trpc/server` standalone adapter (`clients/ts-tests/bench/node-reference.mjs`).

Tune with `BENCH_DURATION` (default `5s`) and `BENCH_CONNECTIONS`
(default `16`). All servers run single-worker on free localhost ports;
handlers are unannotated on every server so no input validation skews the
comparison (the Node reference has no input parser).

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

## Deferred until the features exist

- SSE encoding / streaming-subscription load: lands with 0.3.0 subscriptions.
- Live type sync latency: lands with 0.3.0 `trpc-py dev` (codegen-only
  numbers are already recorded at 10/50/200 procedures).
- TS/Zod/OpenAPI codegen in Rust: codegen is Python today; Rust benches
  land when codegen moves to the core.
- Pure-Python protocol comparison and Flask/Django baselines: land with
  the 0.5.0 adapters / 0.7.0 performance pass.
