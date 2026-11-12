# Coverage report — 0.9.x → 1.0.0-beta gate

Measured **2026-11-12** on `main` (post-0.8.0). Gate requirement: **≥ 90%
line coverage on both Rust and Python**.

## Totals

| Language | Metric | Line coverage | Missed / total | Gate |
|----------|--------|---------------|----------------|------|
| Python   | `pytest --cov=trpc_rs_py`          | **93%** | 269 / 4115 | ✅ |
| Rust     | unit (`cargo llvm-cov --workspace`) | **77.61%** | 488 / 2180 | ❌ |
| Rust     | integration (instrumented `.so` + pytest) | **~78.5%** of shipped lines | see below | ❌ |

**Python meets the gate.** Rust does not yet — the remaining gap is
concentrated in `crates/trpc-py/src/serve.rs`.

## The two Rust numbers and why they differ

`cargo llvm-cov --workspace` instruments `cargo test` binaries only. The
embedded server (`serve.rs`) and most of the FFI surface in `lib.rs` are
exercised by the **Python** suite against the installed `.so` — invisible to
that metric (serve.rs reads 0%).

The second number closes that blind spot: the extension is rebuilt with
`-Cinstrument-coverage` (`RUSTFLAGS`, see `scripts/coverage.sh`), the full
pytest suite runs against it, and `llvm-cov report` reads the merged
`.profdata`. That measures the *shipped* extension code — a different
denominator (no `#[cfg(test)]` lines).

### Unit coverage per file (`cargo llvm-cov --workspace`)

| File | % lines | Missed |
|------|---------|--------|
| `trpc-core/src/lib.rs`      | 100.00% | 0 |
| `trpc-core/src/json.rs`     |  97.73% | 1 |
| `trpc-core/src/error.rs`    |  97.26% | 4 |
| `trpc-core/src/protocol.rs` |  97.13% | 15 |
| `trpc-core/src/sse.rs`      |  95.28% | 6 |
| `trpc-core/src/watch.rs`    |  88.79% | 12 |
| `trpc-py/src/lib.rs`        |  97.09% | 23 |
| `trpc-py/src/serve.rs`      |   0.00% | 427 |

### Integration coverage per shipped file (instrumented `.so` + pytest)

| File | % lines | Missed |
|------|---------|--------|
| `trpc-core/src/json.rs`     |  98.63% | 1 |
| `trpc-core/src/sse.rs`      |  91.41% | 11 |
| `trpc-py/src/lib.rs`        |  82.03% | 106 |
| `trpc-core/src/protocol.rs` |  80.83% | 65 |
| `trpc-py/src/serve.rs`      |  73.74% | 187 |
| `trpc-core/src/watch.rs`    |  73.86% | 23 |
| `trpc-core/src/error.rs`    |  62.86% | 52 |

The true union (unit ∪ integration) is higher than either single number —
e.g. `lib.rs` is 97% under unit tests and 82% under integration, so its real
coverage is ≥97%. The dominant remaining gap is **`serve.rs` (~187 uncovered
lines)**: hyper request handling, the SSE stream pump, connection-lifecycle
and graceful-shutdown paths that pytest reaches only partially, plus
`_AsgiBridge`/`Wake` internals reachable from neither suite yet.

## Python per-module coverage

| Module | % | Module | % |
|--------|---|--------|---|
| `csrf.py` / `errors.py` | 100% | `serve.py` | 91% |
| `cors.py` | 99% | `testing.py` | 90% |
| `zodgen.py` | 99% | `rest.py` | 87% |
| `middleware.py` | 98% | `_bridge.py` | 87% |
| `panel.py` | 96% | `streaming.py` | 87% |
| `context.py` | 94% | `django.py` | 88% |
| `openapi.py` | 93% | `wsgi.py` | 86% |
| `codegen.py` | 92% | `router.py` | 87% |
| `validation.py` | 91% | `asgi.py` | 88% |
| `dev.py` / `_dev_loader.py` / `cli.py` / `client.py` / `pyclient.py` | 100% | | |

(The five `dev`-era gaps — `_dev_loader.py`, `dev.py`, `cli.py`, `client.py`,
`pyclient.py`, `zodgen.py` — went to 99–100% via
`tests/test_{dev_loader,zodgen_edges,pyclient_edges,client_edges,cli_edges}.py`.)

## Commands

```sh
# Python
uv run pytest --cov=trpc_rs_py --cov-report=term-missing -q

# Rust — full pipeline (unit + instrumented integration + Python)
bash scripts/coverage.sh
```

Artifacts land in `target/coverage/` (`pytest.profdata`,
`integration-report.txt`, `unit-coverage.json`).

## Remaining gaps → 90%

1. **`serve.rs` ~187 lines** — add Rust unit tests for `_AsgiBridge`/`Wake`
   state transitions and drain logic, plus real-socket edge tests
   (`tests/test_serve_edges.py`): shutdown races, transport-level malformed
   requests, SSE disconnect mid-stream, body-limit 413s.
2. **`protocol.rs`/`error.rs`/`watch.rs` unit edges** — scattered parse and
   error-name branches (~90 lines combined under integration).
3. **Python adapter tails** — `asgi.py` disconnect paths (~58 lines),
   `rest.py` error envelopes, `streaming.py` SSE edges; all ≥86% already.

## Caveats

- The instrumented-extension approach measures shipped code only; `#[cfg(test)]`
  code contributes under the unit metric, not integration.
- `watch::tests::timeout_returns_none` used to flake under instrumentation;
  fixed by draining watcher startup events (see commit adding
  `scripts/coverage.sh`).
