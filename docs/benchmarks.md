# Benchmarks

Latest run: **0.2.0** (see `benchmarks/results/0.2.0/` for the raw JSON —
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
| trpc-fastapi | 16,233 req/s (p99 1.35 ms) | 11,923 req/s (p99 2.02 ms) | 14,637 req/s (p99 1.47 ms) | 6,440 req/s (p99 2.99 ms) |
| plain FastAPI | 11,686 req/s (p99 2.05 ms) | 9,617 req/s (p99 2.21 ms) | 10,884 req/s (p99 1.86 ms) | 3,214 req/s (p99 5.56 ms) |
| Node `@trpc/server` | 35,634 req/s (p99 1.04 ms) | 17,739 req/s (p99 1.82 ms) | 32,148 req/s (p99 1.17 ms) | 10,293 req/s (p99 3.13 ms) |

![Requests per second by workload and server](benchmarks/latest-http-rps.svg)

![p99 latency by workload and server](benchmarks/latest-http-p99.svg)

Reading: the tRPC mount is **faster than a plain FastAPI JSON view** on every
workload (up to ~2x on large payloads), because request parsing and
serialization happen in the Rust core. The official Node standalone adapter
is still ~2.2x faster on small requests — Python handler dispatch dominates
there, which is the expected gap for a Python-served API.

## Adapter micro-benchmarks (pytest-benchmark)

| Benchmark | mean |
| --- | --- |
| tRPC query through the ASGI mount | 497.5 µs |
| Plain JSON view baseline | 499.1 µs |
| `generate_types`, 10 procedures | 127.8 µs |
| `generate_types`, 50 procedures | 627.1 µs |
| `generate_types`, 200 procedures | 2,520.1 µs |

![Python adapter and codegen micros](benchmarks/latest-python-micro.svg)

At the micro level the adapter overhead is within noise of a plain JSON view.
Codegen scales roughly linearly — ~2.5 ms for a 200-procedure router — which
leaves large headroom under the 1-second save-to-`.d.ts` target once the
watcher and reload overhead of `trpc-py dev` are added in 0.3.0.

## Rust core hot paths (criterion)

| Benchmark | mean |
| --- | --- |
| `route_lookup_200` | 7.4 ns |
| `parse_single_post` | 164.3 ns |
| `parse_single_get` | 227.6 ns |
| `parse_batch_10_post` | 1,794.1 ns |
| `parse_batch_10_get` | 2,058.5 ns |
| `build_response_single` | 196.0 ns |
| `build_response_batch_10` | 1,924.3 ns |
| `error_envelope` | 286.2 ns |

![Rust core micros](benchmarks/latest-rust-micro.svg)

Protocol parsing and serialization are nanosecond-scale; per-request cost is
dominated by HTTP handling and Python dispatch, not the protocol core.

## Deferred until the features exist

- SSE / streaming-subscription load: lands with 0.3.0 subscriptions.
- Live type sync latency (save-to-`.d.ts`): lands with 0.3.0 `trpc-py dev`.
- Rust-side codegen benches: codegen is Python today.
- Pure-Python protocol comparison and Flask/Django baselines: land with the
  0.5.0 adapters / 0.7.0 performance pass.
