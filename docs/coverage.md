# Coverage report — 0.9.x → 1.0.0-beta gate

Measured **2026-11-12** at commit `abda30af0a37465d57e7bc9c24dadb1d8553b684`
(`main`, version 0.8.0 workspace). Gate requirement: **≥ 90% line coverage on
both Rust (`cargo llvm-cov`) and Python (`pytest-cov`)**.

## Totals

| Language | Tool | Line coverage | Missed / total |
|----------|------|---------------|----------------|
| Rust     | `cargo llvm-cov --workspace` | **62.32%** | 1209 / 1940 |
| Python   | `pytest --cov=trpc_rs_py`    | **86.00%** | 578 / 4114 |

**Both are below the 90% gate.** Rust is the bigger gap (~540 uncovered lines
needed just to reach 90% of 1940).

## Commands

```sh
# Rust (one flaky timing test skipped — see caveats)
cargo llvm-cov --workspace --lcov --output-path /tmp/rust.lcov \
  -- --skip watch::tests::timeout_returns_none

# Python
uv run pytest --cov=trpc_rs_py --cov-report=term-missing \
  --cov-report=lcov:/tmp/py.lcov -q
```

Artifacts: `/tmp/rust.lcov`, `/tmp/py.lcov` (ephemeral; regenerate with the
commands above).

## Rust per-file coverage

| File | Covered / total | % | Status |
|------|-----------------|---|--------|
| `crates/trpc-core/src/lib.rs`          | 15/15   | 100.00% | ✅ |
| `crates/trpc-core/src/json.rs`         | 43/44   |  97.73% | ✅ |
| `crates/trpc-core/src/error.rs`        | 142/146 |  97.26% | ✅ |
| `crates/trpc-core/src/protocol.rs`     | 508/523 |  97.13% | ✅ |
| `crates/trpc-core/src/sse.rs`          | 121/127 |  95.28% | ✅ |
| `crates/trpc-core/src/watch.rs`        | 81/105  |  77.14% | ❌ |
| `crates/trpc-py/src/lib.rs`            | 299/553 |  54.07% | ❌ |
| `crates/trpc-py/src/serve.rs`          | 0/427   |   0.00% | ❌ |

`trpc-core` is in good shape (all files ≥ 77%, most ≥ 95%). The entire gap is
in `trpc-py`, the PyO3 crate.

### `crates/trpc-py/src/serve.rs` — 0% (427 uncovered lines)

The whole embedded-server module has **no Rust unit tests**: the ASGI/HTTP
server (`serve`/`serve_async`), hyper request handling, SSE streaming loop,
connection tracking, graceful-shutdown drain, and the `Wake`/`_AsgiBridge`
callback bridge (`Wake::fire`, `fire_all`, `signal_drained`,
`index_pair`) are only exercised indirectly through Python tests running
against the **prebuilt** `_core` extension — which llvm-cov does not
instrument. Uncovered regions: lines ~94–152 (Wake/bridge internals),
~172–240 (request/response plumbing), ~261–341 (SSE stream pump),
~348–491 (connection lifecycle + shutdown), ~494–625 (`serve_async`,
`run_server`, `serve` pyfunction), ~631–683 (module tail/tests helpers).

### `trpc-py/src/lib.rs` — 54% (234 uncovered lines)

Covered only by the 9 `ffi::tests` unit tests (JSON conversion, error
contract, depth/float guards). Uncovered:

- `extra_from_python` (lines ~50–65): typed-error payload → `error.data`
  merge, `code`/`httpStatus` spoof stripping.
- `PyLimits` constructor/getters error branches (~86–135): rejecting
  negative/non-finite `procedure_timeout`, limit getters.
- `SseEncoder::serialized_error` and related PyO3 class methods
  (~239–342, ~361–399): SSE error frames exposed to Python.
- `Watcher::new`/`next` PyO3 wrappers (~419–491): the Rust `watch` logic is
  tested in `trpc-core`, but the Python-facing wrapper is not.
- Module init / remaining FFI surface (~558–616).

### `crates/trpc-core/src/watch.rs` — 77% (24 uncovered lines)

Uncovered: watcher construction error paths (~38, 57–61), event-batch edge
cases (~95, 106, 126–129), and the body of `timeout_returns_none`
(169–180) — skipped in this run because it is timing-flaky under
instrumentation (see caveats). Real gap is ~17 lines of watcher
error/edge handling.

### `trpc-core` stragglers (<10 uncovered lines each)

- `error.rs` 175–177, `json.rs` 81, `sse.rs` 160–164, `protocol.rs`
  137/189/203/216/278/349–353/413 — scattered error branches and rare
  envelope edge cases; cheap to close.

## Python per-module coverage

| Module | Stmts | Miss | % | Status |
|--------|-------|------|---|--------|
| `csrf.py`        | 43   | 0  | 100% | ✅ |
| `errors.py`      | 66   | 0  | 100% | ✅ |
| `cors.py`        | 87   | 1  |  99% | ✅ |
| `middleware.py`  | 132  | 3  |  98% | ✅ |
| `panel.py`       | 84   | 3  |  96% | ✅ |
| `context.py`     | 67   | 4  |  94% | ✅ |
| `openapi.py`     | 95   | 7  |  93% | ✅ |
| `serve.py`       | 69   | 6  |  91% | ✅ |
| `validation.py`  | 136  | 12 |  91% | ✅ |
| `codegen.py`     | 472  | 42 |  91% | ✅ |
| `testing.py`     | 226  | 22 |  90% | ✅ |
| `__init__.py`    | 20   | 2  |  90% | ✅ |
| `_bridge.py`     | 91   | 12 |  87% | ❌ |
| `asgi.py`        | 476  | 58 |  88% | ❌ |
| `django.py`      | 91   | 11 |  88% | ❌ |
| `rest.py`        | 202  | 26 |  87% | ❌ |
| `router.py`      | 183  | 23 |  87% | ❌ |
| `streaming.py`   | 246  | 33 |  87% | ❌ |
| `wsgi.py`        | 49   | 7  |  86% | ❌ |
| `client.py`      | 343  | 60 |  83% | ❌ |
| `cli.py`         | 232  | 44 |  81% | ❌ |
| `pyclient.py`    | 261  | 51 |  80% | ❌ |
| `zodgen.py`      | 289  | 78 |  73% | ❌ |
| `dev.py`         | 112  | 52 |  54% | ❌ |
| `_dev_loader.py` | 42   | 21 |  50% | ❌ |

## Gap analysis — where the 1.0.0 effort should aim

### Rust (need ~+540 covered lines to reach 90%)

1. **`serve.rs` (0%, 427 lines) — the single biggest gap.** The embedded
   hyper server and `_AsgiBridge` have zero Rust tests. Options: unit-test
   `Wake`/`_AsgiBridge` state transitions and the shutdown-drain logic
   directly in Rust, and/or build the extension under llvm-cov during a
   Python-driven run so the pytest suite counts. Note: the Python suite
   already exercises this code end-to-end via the prebuilt `.so`; the
   blocker is instrumentation, not behavior.
2. **`lib.rs` PyO3 surface (54%, 234 lines).** `SseEncoder` methods,
   `Watcher` wrappers, `PyLimits` validation, and `extra_from_python` are
   reachable from Rust tests with a `Python::with_gil` harness, or via
   instrumented-extension Python tests.
3. **`watch.rs` (~17 real uncovered lines).** Watcher error paths and
   batch edge cases; the skipped `timeout_returns_none` body would add a
   few more if the flake is fixed.

### Python (need ~+165 covered lines to reach 90%)

1. **`_dev_loader.py` + `dev.py` (50%/54%, ~73 missed lines combined).**
   The `trpc-py dev` watcher loop, subprocess server management, and
   `_dev_loader.main()` CLI path are essentially untested — the file-watch
   → regenerate → serve cycle is the project's flagship DX feature and
   currently has no coverage. Highest value-per-line on the Python side.
2. **`zodgen.py` (73%, 78 missed).** Default-value serialization
   (`_wrap_default`), discriminator detection (`_find_discriminator`),
   `**kwargs`/`*args` rejection branches, and union/Callable annotation
   rendering — mostly refusal/edge paths in the zod schema generator.
3. **`pyclient.py` (80%, 51 missed).** Error branches in annotation
   rendering (`_literal_source`, `_annotation_source` depth-32 and
   unrenderable-type `None` paths) and client error handling.
4. **`cli.py` (81%, 44 missed) + `client.py` (83%, 60 missed).** CLI
   subcommand edge cases and the Python tRPC client's error/streaming
   branches (`client.py` 847–855, 869–893 region).
5. **Adapter tails:** `asgi.py` (88%, 58 missed — disconnect/error paths,
   lines ~1075–1214), `rest.py` (87% — error envelope branches ~509–519),
   `router.py` (87% — merge/validation edges ~315–369, 834–838),
   `streaming.py` (87% — SSE edge cases ~658–714), `wsgi.py`/`django.py`
   (~87–88% — WSGI/Django adapter error paths).

## Caveats

- **Flaky test:** `watch::tests::timeout_returns_none` fails consistently
  under llvm-cov instrumentation (FSEvents latency exceeds the 50 ms
  assertion window) but passes uninstrumented. It was skipped for the
  Rust run via `-- --skip watch::tests::timeout_returns_none`; its ~12
  lines are therefore counted as uncovered. Fixing the flake (longer
  timeout or event-drain before asserting) would recover them.
- **trpc-py coverage is Python-test-blind:** `pytest` exercises the
  shipped `_core.cpython-*.so`, not an instrumented build, so
  `cargo llvm-cov --workspace` sees only Rust unit tests. A meaningful
  Rust number for `trpc-py` requires either Rust-side tests for
  `serve.rs`/`lib.rs` or a `maturin develop` build under
  `-Cinstrument-coverage` driven by the Python suite.
- Coverage is line-based; `trpc-core`'s 97%+ files still have scattered
  single-branch misses listed above.
