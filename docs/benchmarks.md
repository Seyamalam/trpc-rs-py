# Benchmarks

Latest run: **0.6.0** (see `benchmarks/results/0.6.0/` for the raw JSON —
numbers are machine-produced by criterion, pytest-benchmark and oha, never
hand-edited). Method, environment and tuning knobs are documented in
[`benchmarks/README.md`](https://github.com/Seyamalam/trpc-rs-py/blob/main/benchmarks/README.md).

Environment: Apple M5 Pro (arm64), macOS, Python 3.14.7, rustc 1.96.0,
Node 26.7.0, oha 1.16.0. All servers ran single-worker on localhost with a
readiness probe before each load run; workloads ran for 5s at 16 connections.
No input validation ran on any server so the comparison isolates protocol and
adapter overhead. This run had a busier machine than 0.5.0 — absolute numbers
dropped ~30% on **every** server including Node — so read the ratios, not the
totals.

## HTTP load (oha)

Four workloads per server: a single query, a 10-call batch in one HTTP
request, a mutation, and a 100 KB payload.

| Server | single query | batch of 10 | mutation | 100 KB payload |
| --- | --- | --- | --- | --- |
| trpc-fastapi | 11,009 req/s (p99 1.84 ms) | 6,921 req/s (p99 2.65 ms) | 9,675 req/s (p99 2.05 ms) | 4,444 req/s (p99 4.10 ms) |
| plain FastAPI | 8,879 req/s (p99 2.18 ms) | 5,378 req/s (p99 3.58 ms) | 6,987 req/s (p99 2.85 ms) | 2,488 req/s (p99 8.94 ms) |
| Node `@trpc/server` | 22,604 req/s (p99 1.59 ms) | 10,376 req/s (p99 3.23 ms) | 19,810 req/s (p99 1.66 ms) | 6,661 req/s (p99 4.84 ms) |

![Requests per second by workload and server](benchmarks/latest-http-rps.svg)

![p99 latency by workload and server](benchmarks/latest-http-p99.svg)

Reading: the tRPC mount is **~24% faster than a plain FastAPI JSON view** on
the single-query workload and ~1.8x on large payloads, because request parsing
and serialization happen in the Rust core. The official Node standalone
adapter is still ~2x faster on small requests — Python handler dispatch
dominates there, which is the expected gap for a Python-served API.

## Adapter micro-benchmarks (pytest-benchmark)

| Benchmark | median |
| --- | --- |
| tRPC query through the ASGI mount | 747.4 µs |
| Plain JSON view baseline | 767.7 µs |
| tRPC query through `mount_wsgi()` (Flask) | 1,665.7 µs |
| tRPC query through `trpc_view()` (Django) | 1,661.0 µs |
| `generate_types`, 10 procedures | 196.3 µs |
| `generate_types`, 50 procedures | 957.9 µs |
| `generate_types`, 200 procedures | 3,789.1 µs |

![Python adapter and codegen micros](benchmarks/latest-python-micro.svg)

At the micro level the ASGI adapter overhead — now including the always-on
middleware dispatch added in 0.6.0 — stays within noise of a plain JSON view
(the ASGI figure moved 511.7 → 747.4 µs alongside a matching baseline move,
i.e. machine drift, not chain cost). The WSGI and Django adapters cost
roughly **0.9–1.0 ms per request** on top — that is the price of the pumped
event loop and worker-thread bridge that lets the shared ASGI dispatch
(including SSE streaming) run inside a synchronous framework; see
[adapters](adapters.md) for the operational limits this implies. Codegen
scales roughly linearly — ~3.8 ms for a 200-procedure router — which leaves
large headroom under the 1-second save-to-`.d.ts` target that `trpc-py dev`
(shipped in 0.3.0) operates under.

## Rust core hot paths (criterion)

| Benchmark | mean |
| --- | --- |
| `route_lookup_200` | 10.6 ns |
| `parse_single_post` | 367.7 ns |
| `parse_single_get` | 233.0 ns |
| `parse_batch_10_post` | 2,055.9 ns |
| `parse_batch_10_get` | 2,293.1 ns |
| `build_response_single` | 268.5 ns |
| `build_response_batch_10` | 2,709.6 ns |
| `error_envelope` | 398.2 ns |

![Rust core micros](benchmarks/latest-rust-micro.svg)

Protocol parsing and serialization are nanosecond-scale; per-request cost is
dominated by HTTP handling and Python dispatch, not the protocol core.

## Deferred until the features exist

- SSE / streaming-subscription load: benchmarks for long-lived streams
  (throughput per client, memory per connection) are planned for a later
  milestone; subscriptions shipped in 0.3.0 with functional coverage.
- Rust-side codegen benches: codegen is Python today.
- Pure-Python protocol comparison: lands with the 0.7.0 performance pass.
- WSGI/Django HTTP-load baselines (oha against a real server): the adapter
  micros above measure in-process dispatch; a loaded-server comparison lands
  with the 0.7.0 performance pass.
