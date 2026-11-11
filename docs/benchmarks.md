# Benchmarks

Latest run: **0.7.0** (see `benchmarks/results/0.7.0/` for the raw JSON —
reproduce with `uv run python benchmarks/run.py`). This run executed on a
busier machine than the 0.5.0/0.6.0 runs — criterion micros and the full
oha matrix ran back-to-back, so absolute numbers are down across **all**
servers including the Node reference and the plain-FastAPI baseline.
Compare ratios between rows, not totals across versions. The 0.7.0 release
run adds a fourth server: `trpc-standalone` (the in-process Rust HTTP
server shipped in this release).

## HTTP load (oha)

Four workloads per server: a single query, a 10-call batch in one HTTP
request, a mutation, and a 100 KB payload.

| Server | single query | batch of 10 | mutation | 100 KB payload |
| --- | --- | --- | --- | --- |
| trpc-fastapi | 6,260 req/s (p99 5.09 ms) | 2,717 req/s (p99 24.49 ms) | 4,217 req/s (p99 17.88 ms) | 2,644 req/s (p99 18.76 ms) |
| trpc-standalone | 6,833 req/s (p99 9.09 ms) | 3,883 req/s (p99 19.79 ms) | 8,311 req/s (p99 4.77 ms) | 5,817 req/s (p99 7.89 ms) |
| plain FastAPI | 7,916 req/s (p99 5.19 ms) | 4,324 req/s (p99 9.07 ms) | 4,700 req/s (p99 6.78 ms) | 1,382 req/s (p99 15.38 ms) |
| Node `@trpc/server` | 25,822 req/s (p99 1.48 ms) | 10,586 req/s (p99 4.67 ms) | 14,509 req/s (p99 5.93 ms) | 6,159 req/s (p99 8.28 ms) |

![Requests per second by workload and server](benchmarks/latest-http-rps.svg)

![p99 latency by workload and server](benchmarks/latest-http-p99.svg)

Reading: under load, **the standalone Rust server is the fastest Python
trpc-rs-py entry point on every workload** — up to ~2x faster than the
uvicorn-served mount on mutations and the 100 KB payload, and ~4x the
plain FastAPI baseline on large bodies. On single queries under load the
four Python-side servers bunch together (the dispatch path, not HTTP I/O,
bounds throughput); on an idle machine the standalone server leads by a
much wider margin — see [performance](performance.md) for the quieter
pre-release run where standalone beat even plain FastAPI on single
queries (+43% over trpc-fastapi). The official Node adapter still leads
on small requests — Python handler dispatch dominates there, which is the
expected gap for a Python-served API.

## Adapter micro-benchmarks (pytest-benchmark)

| Benchmark | median |
| --- | --- |
| tRPC query through the ASGI mount | 1,254.4 µs |
| Plain JSON view baseline | 1,158.3 µs |
| tRPC query through `mount_wsgi()` (Flask) | 1,927.3 µs |
| tRPC query through `trpc_view()` (Django) | 1,929.1 µs |
| `generate_types`, 10 procedures | 227.7 µs |
| `generate_types`, 50 procedures | 1,182.4 µs |
| `generate_types`, 200 procedures | 4,866.4 µs |

![Python adapter and codegen micros](benchmarks/latest-python-micro.svg)

All medians moved up together versus 0.6.0 — including the plain JSON
baseline, which has no code changes — consistent with the busier machine.
The ASGI adapter stays within noise of a plain JSON view; the WSGI and
Django adapters still cost roughly **0.7–0.8 ms per request** — the price
of the pumped event loop and worker-thread bridge that lets the shared
ASGI dispatch (including SSE streaming) run inside a synchronous
framework; see [adapters](adapters.md) for the operational limits this
implies. Codegen scales roughly linearly — ~4.9 ms for a 200-procedure
router — which leaves large headroom under the 1-second save-to-`.d.ts`
target that `trpc-py dev` operates under.

## Rust core hot paths (criterion)

| Benchmark | mean |
| --- | --- |
| `route_lookup_200` | 14.4 ns |
| `parse_single_post` | 311.2 ns |
| `parse_single_get` | 459.1 ns |
| `parse_batch_10_post` | 3,251.0 ns |
| `parse_batch_10_get` | 3,694.7 ns |
| `build_response_single` | 348.6 ns |
| `build_response_batch_10` | 3,418.6 ns |
| `error_envelope` | 619.4 ns |

![Rust core micros](benchmarks/latest-rust-micro.svg)

Protocol parsing and serialization are nanosecond-scale; per-request cost
is dominated by HTTP handling and Python dispatch, not the protocol core.

## Deferred until the features exist

- SSE / streaming-subscription load: benchmarks for long-lived streams
  (throughput per client, memory per connection) are planned for a later
  milestone; subscriptions have functional coverage only.
- Rust-side codegen benches: codegen is Python today.
- Pure-Python protocol comparison and WSGI/Django `oha` baselines: still
  deferred — the 0.7.0 performance pass prioritized the standalone-vs-
  uvicorn comparison, which was the decisive question for this release.
