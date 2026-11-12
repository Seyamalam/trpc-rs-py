#!/usr/bin/env bash
# Coverage measurement for the 1.0.0-beta >=90% gate (docs/coverage.md).
#
# Two numbers are reported:
#   1. Rust unit coverage  — `cargo llvm-cov --workspace` over `cargo test`.
#   2. Rust unit+integration coverage — the trpc-py extension rebuilt with
#      `-Cinstrument-coverage`, driven by the *Python* test suite over real
#      sockets (this is what exercises serve.rs/lib.rs's FFI surface), merged
#      with the unit-test profiles.
#
# Plus `pytest --cov=trpc_rs_py` for the Python half.
#
# Leaves the dev .so rebuilt WITHOUT instrumentation at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

SYSROOT="$(rustc --print sysroot)"
# llvm-tools for the pinned toolchain live under the host triple dir.
HOST="$(rustc -vV | sed -n 's/^host: //p')"
LLVMBIN="$SYSROOT/lib/rustlib/$HOST/bin"
PROFDATA="$LLVMBIN/llvm-profdata"
COV="$LLVMBIN/llvm-cov"
[ -x "$PROFDATA" ] || { echo "llvm-profdata missing — run: rustup component add llvm-tools"; exit 1; }

OUT=target/coverage
PROFRAW=$OUT/profraw
SO="python/trpc_rs_py/_core.cpython-314-darwin.so"
rm -rf "$OUT"; mkdir -p "$PROFRAW"

echo "=== 1/4 cargo unit-test coverage ==="
cargo llvm-cov --workspace --summary-only 2>&1 | tail -20
cargo llvm-cov --workspace --export --output-path "$OUT/unit-coverage.json" 2>/dev/null || true

echo "=== 2/4 instrumented extension build ==="
RUSTFLAGS="-Cinstrument-coverage" \
LLVM_PROFILE_FILE="$PWD/$PROFRAW/build-%p-%m.profraw" \
    uv run maturin develop >/dev/null
otool -l "$SO" | grep -q "__LLVM_COV" || {
    echo "instrumented build failed (.so has no __LLVM_COV segment)"; exit 1;
}

echo "=== 3/4 pytest under instrumentation ==="
LLVM_PROFILE_FILE="$PWD/$PROFRAW/pytest-%p-%m.profraw" \
    uv run pytest -q 2>&1 | tail -5

"$PROFDATA" merge -sparse "$PROFRAW"/*.profraw -o "$OUT/pytest.profdata"
# Integration coverage: just the extension .so.
"$COV" report "$SO" --instr-profile="$OUT/pytest.profdata" \
    --path-equivalence=.,/Users/seyam/Work/trpc-rs-py 2>/dev/null | tail -25 \
    | tee "$OUT/integration-report.txt"

echo "=== 4/4 python coverage ==="
uv run pytest -q --cov=trpc_rs_py --cov-report=term-missing:skip-covered 2>&1 | tail -40

echo "=== rebuilding clean dev .so ==="
uv run maturin develop --release >/dev/null
echo "coverage artifacts: $OUT/ (pytest.profdata, integration-report.txt, unit-coverage.json)"
