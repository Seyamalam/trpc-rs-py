# Local CI gate (MISSION.md section 15). No hosted CI exists; everything runs locally.
set shell := ["bash", "-cu"]

default: ci-fast

# The full gate, in order, stopping on first failure.
ci: ci-fmt ci-lint ci-rust-test ci-build ci-types ci-stubtest ci-pytest ci-ts ci-examples ci-docs

# Quick iteration: steps 1-3 and 5.
ci-fast: ci-fmt ci-lint ci-rust-test ci-types

# Step 1: formatters (Rust, Python, TypeScript).
ci-fmt:
    cargo fmt --all --check
    uv run ruff format --check python tests
    cd clients/ts-tests && pnpm format:check

# Step 2: linters (Rust, Python, TypeScript).
ci-lint:
    cargo clippy --workspace --all-targets -- -D warnings
    uv run ruff check python tests

# Step 3: Rust unit and property tests.
ci-rust-test:
    cargo test --workspace

# Step 4: build and install the extension into the venv.
ci-build:
    uv run maturin develop --release

# Step 5: Python type checking.
ci-types:
    uv run mypy
    uv run pyright

# Step 6: stub vs compiled module.
ci-stubtest:
    uv run python -m mypy.stubtest trpc_rs_py

# Step 7: Python tests (unit, integration, security, differentiators).
ci-pytest:
    uv run pytest

# Step 8: TypeScript compatibility, type tests, and npm package tests.
ci-ts:
    ./scripts/ts-tests.sh

# Step 9: example and template smoke tests.
ci-examples:
    ./scripts/examples-smoke.sh

# Step 10: docs build (warnings fail) + rustdoc + TypeDoc + matrix drift check
# + doc snippet execution.
ci-docs:
    uv run mkdocs build --strict
    cargo doc --no-deps --workspace
    cd packages/link && pnpm run docs
    uv run python scripts/check_matrix.py
    uv run python scripts/test_doc_snippets.py

# Benchmark suite (MISSION.md section 10): raw JSON results + SVG charts.
bench:
    uv run python benchmarks/run.py
    uv run python benchmarks/plot.py

# Bounded cargo-fuzz smoke over the protocol parser (MISSION.md section 7).
# Requires a nightly toolchain and cargo-fuzz; never part of ci/ci-fast.
fuzz-smoke seconds="60":
    #!/usr/bin/env bash
    set -euo pipefail
    cd fuzz
    for target in parse_get_query parse_post_body parse_batch sse_encode; do
        mkdir -p "corpus/$target"
        echo "== fuzz target: $target ({{seconds}}s) =="
        cargo +nightly fuzz run "$target" "corpus/$target" "seeds/$target" \
            -- -max_total_time={{seconds}}
    done

# Release gate: full CI + benchmarks + audits + fuzzers (short) + cross-platform wheels.
release-check: ci
    uv run python benchmarks/run.py
    uv run python benchmarks/plot.py
    just fuzz-smoke 60
    bash scripts/audit.sh
    @echo "release-check: cross-platform wheels land in 0.9.x (see MISSION.md roadmap)"

# Cross-platform wheel matrix (MISSION.md section 12): builds all shippable
# targets into dist/wheels/ and verifies installs; see docs/wheels.md.
wheels:
    ./scripts/wheels.sh

# Individual pieces for quick iteration.
fmt:
    cargo fmt --all
    uv run ruff format python tests
    cd clients/ts-tests && pnpm format

lint:
    cargo clippy --workspace --all-targets -- -D warnings
    uv run ruff check python tests

test:
    cargo test --workspace
    uv run pytest

# Install the git hooks.
hooks:
    ./scripts/install-hooks.sh
