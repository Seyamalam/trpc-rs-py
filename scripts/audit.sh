#!/usr/bin/env bash
# Dependency audit gate (MISSION.md section 7):
#   Rust   - cargo audit (when installed) and cargo deny check (advisories,
#            bans, licenses, sources; see deny.toml)
#   Python - pip-audit against the uv-locked project environment
#   npm    - pnpm audit in clients/ts-tests and every packages/*/package.json
#
# Runs every audit even after a failure and exits non-zero if any step
# reported a real failure (vulnerabilities, denied licenses/bans/sources).
# Missing optional tools are skipped with a warning, not a failure.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0

header() { printf '\n=== %s ===\n' "$1"; }

run_step() {
  # run_step <label> <command...>
  local label="$1"
  shift
  header "$label"
  if "$@"; then
    printf '%s: ok\n' "$label"
  else
    local status=$?
    printf '%s: FAILED (exit %d)\n' "$label" "$status" >&2
    fail=1
  fi
}

# --- Rust -----------------------------------------------------------------

header "tool versions"
cargo --version
cargo deny --version
uv --version
pnpm --version
node --version

if cargo audit --version >/dev/null 2>&1; then
  run_step "cargo audit (RustSec advisories)" cargo audit
else
  header "cargo audit (RustSec advisories)"
  echo "cargo-audit not installed; skipped. 'cargo deny check advisories' below covers the same advisory database."
fi

run_step "cargo deny check (advisories, bans, licenses, sources)" cargo deny check

# --- Python ---------------------------------------------------------------

# pip-audit is not a project dependency; run it as a uv --with overlay so it
# audits the same resolved environment described by uv.lock. `uv run` syncs
# the project env first, so this covers every locked dependency.
if uv run --with pip-audit pip-audit --version >/dev/null 2>&1; then
  run_step "pip-audit (Python, uv.lock environment)" \
    uv run --with pip-audit pip-audit --progress-spinner off
else
  header "pip-audit (Python, uv.lock environment)"
  echo "could not provision pip-audit via 'uv run --with'; skipped." >&2
  fail=1
fi

# --- npm ------------------------------------------------------------------

pnpm_audit_dir() {
  local dir="$1"
  header "pnpm audit ($dir)"
  if [ ! -f "$dir/pnpm-lock.yaml" ]; then
    echo "no pnpm-lock.yaml in $dir; skipped"
    return 0
  fi
  if (cd "$dir" && pnpm audit); then
    printf 'pnpm audit (%s): ok\n' "$dir"
  else
    printf 'pnpm audit (%s): FAILED\n' "$dir" >&2
    fail=1
  fi
}

pnpm_audit_dir "clients/ts-tests"

for pkg in packages/*/package.json; do
  [ -e "$pkg" ] || continue
  pnpm_audit_dir "$(dirname "$pkg")"
done

# --- summary --------------------------------------------------------------

header "summary"
if [ "$fail" -eq 0 ]; then
  echo "all audits passed"
else
  echo "one or more audits failed; see output above" >&2
fi
exit "$fail"
