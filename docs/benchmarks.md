# Benchmarks

Latest run: **0.3.0** (see `benchmarks/results/0.3.0/` for the raw JSON —
numbers are machine-produced by criterion, pytest-benchmark and oha, never
hand-edited). Method, environment and tuning knobs are documented in
[`benchmarks/README.md`](https://github.com/Seyamalam/trpc-rs-py/blob/main/benchmarks/README.md).

Environment: Apple M5 Pro (arm64), macOS, Python 3.14.7, rustc 1.96.0,
Node 26.7.0, oha 1.16.0. All servers ran single-worker on localhost with a
readiness probe before each load run; workloads ran for 5s at 16 connections.
No input validation ran on any server so the comparison isolates protocol and
adapter overhead.

## HTTP load (oha)

Four workloads per server: a single query, a 10-call batch in one HTTP
request, a mutation, and a 100 KB payload.

| Server | single query | batch of 10 | mutation | 100 KB payload |
| --- | --- | --- | --- | --- |
| trpc-fastapi | 16,269 req/s (p99 1.08 ms) | 11,887 req/s (p99 1.43 ms) | 14,697 req/s (p99 1.50 ms) | 6,547 req/s (p99 2.87 ms) |
| plain FastAPI | 11,203 req/s (p99 1.97 ms) | 9,984 req/s (p99 2.39 ms) | 11,561 req/s (p99 1.52 ms) | 3,204 req/s (p99 5.61 ms) |
| Node `@trpc/server` | 37,553 req/s (p99 0.90 ms) | 18,223 req/s (p99 1.75 ms) | 33,212 req/s (p99 1.08 ms) | 10,977 req/s (p99 2.81 ms) |

![Requests per second by workload and server](benchmarks/latest-http-rps.svg)

![p99 latency by workload and server](benchmarks/latest-http-p99.svg)

Reading: the tRPC mount is **~45% faster than a plain FastAPI JSON view** on
the single-query workload and ~2x on large payloads, because request parsing
and serialization happen in the Rust core. The official Node standalone
adapter is still ~2.3x faster on small requests — Python handler dispatch
dominates there, which is the expected gap for a Python-served API.

## Adapter micro-benchmarks (pytest-benchmark)

| Benchmark | mean |
| --- | --- |
| tRPC query through the ASGI mount | 465.7 µs |
| Plain JSON view baseline | 466.9 µs |
| `generate_types`, 10 procedures | 124.7 µs |
| `generate_types`, 50 procedures | 619.9 µs |
| `generate_types`, 200 procedures | 2,399.8 µs |

![Python adapter and codegen micros](benchmarks/latest-python-micro.svg)

At the micro level the adapter overhead is within noise of a plain JSON view.
Codegen scales roughly linearly — ~2.5 ms for a 200-procedure router — which
leaves large headroom under the 1-second save-to-`.d.ts` target that
`trpc-py dev` (shipped in 0.3.0) operates under.

## Rust core hot paths (criterion)

| Benchmark | mean |
| --- | --- |
| `route_lookup_200` | 7.4 ns |
| `parse_single_post` | 158.5 ns |
| `parse_single_get` | 202.9 ns |
| `parse_batch_10_post` | 1,711.0 ns |
| `parse_batch_10_get` | 2,049.5 ns |
| `build_response_single` | 197.7 ns |
| `build_response_batch_10` | 1,795.9 ns |
| `error_envelope` | 280.9 ns |

![Rust core micros](benchmarks/latest-rust-micro.svg)

Protocol parsing and serialization are nanosecond-scale; per-request cost is
dominated by HTTP handling and Python dispatch, not the protocol core.

## Deferred until the features exist

- SSE / streaming-subscription load: benchmarks for long-lived streams
  (throughput per client, memory per connection) are planned for a later
  milestone; subscriptions shipped in 0.3.0 with functional coverage.
- Rust-side codegen benches: codegen is Python today.
- Pure-Python protocol comparison and Flask/Django baselines: land with the
  0.5.0 adapters / 0.7.0 performance pass.
