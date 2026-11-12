#!/usr/bin/env bash
# scripts/wheels.sh — build and verify the cross-platform wheel matrix
# (MISSION.md section 12: "Wheels build and install for Linux x86_64/aarch64/
# manylinux/musllinux, macOS x86_64/arm64, Windows x86_64").
#
# Final recipe per target (established 2026-11 on macOS arm64, Rust 1.96,
# maturin 1.16, zig 0.16; details in docs/wheels.md):
#
#   target             rust-target                     toolchain
#   macos-arm64        aarch64-apple-darwin (host)     native clang, maturin
#   macos-x86_64       x86_64-apple-darwin             native clang -arch x86_64
#   linux-x86_64       x86_64-unknown-linux-gnu        maturin --zig (zig cc, glibc 2.17)
#   linux-x86_64-musl  x86_64-unknown-linux-musl       maturin --zig (zig cc, musl)
#   linux-aarch64      aarch64-unknown-linux-gnu       maturin --zig (zig cc, glibc 2.17)
#   linux-aarch64-musl aarch64-unknown-linux-musl      maturin --zig (zig cc, musl)
#   windows-x86_64     x86_64-pc-windows-msvc          maturin --zig + bundled cargo-xwin
#                                                      (clang-cl/lld-link; MSVC SDK
#                                                      auto-downloaded on first run to
#                                                      ~/Library/Caches/cargo-xwin)
#
# Every wheel is built with `pyo3/abi3-py310` enabled on the command line, so
# one wheel per platform covers all supported CPython versions (3.10-3.14):
# tags look like `cp310-abi3-manylinux_2_17_x86_64`. No project file changes
# are needed; maturin detects the abi3 feature from `-F`.
#
# Usage: scripts/wheels.sh [--only t1,t2,...] [--no-verify] [--timeout secs]
#
# Output: dist/wheels/<target>/*.whl, per-target logs in dist/wheels/logs/,
# and a summary table on stdout. One failing target never aborts the rest.
#
# Verification coverage: macOS wheels are pip-installed into scratch venvs on
# this machine (x86_64 under Rosetta via a uv-managed x86_64 CPython), Linux
# wheels are pip-installed + smoke-tested inside docker python:3.14-slim
# (glibc) / python:3.14-alpine (musl) containers for the matching arch, and
# the Windows wheel is structure-verified only (PE32+ DLL inside the wheel —
# no Windows runner exists locally). All of that is reflected in the table.

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
DIST="$ROOT/dist/wheels"
LOGS="$DIST/logs"
mkdir -p "$LOGS"

FEATURES="extension-module pyo3/abi3-py310"
PER_TARGET_TIMEOUT=900   # seconds; a hanging target is killed and reported
DO_VERIFY=1
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --only)      ONLY="$2"; shift 2 ;;
    --no-verify) DO_VERIFY=0; shift ;;
    --timeout)   PER_TARGET_TIMEOUT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "wheels.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

# --- helpers ----------------------------------------------------------------

have() { command -v "$1" >/dev/null 2>&1; }

say()  { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*" >&2; }

# Run a command with a watchdog timeout (macOS has no `timeout(1)`).
run_with_timeout() {
  local secs="$1"; shift
  "$@" &
  local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null ) &
  local watchdog=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null
  return "$rc"
}

# Python smoke run for every install verification: import both the package
# and the compiled module, check matching versions, exercise the Rust parser.
write_smoke() {
  cat > "$DIST/smoke.py" <<'PY'
import re

import trpc_rs_py
import trpc_rs_py._core as c


def canon(v):
    # PEP 440 writes "1.0.0b1"; Cargo semver writes "1.0.0-beta.1".
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:[-.]?(a|b|rc|alpha|beta)\.?(\d+))?", v)
    if not m:
        return v
    pre = f"{m.group(2)[0]}{m.group(3)}" if m.group(2) else ""
    return m.group(1) + pre


assert canon(trpc_rs_py.__version__) == canon(c.__version__), (
    trpc_rs_py.__version__,
    c.__version__,
)
req = c.parse_request("GET", "ping", "", None, c.Limits())
assert req.calls[0].path == "ping" and not req.batched
status, _body = c.build_response([c.CallResult.ok({"ok": True})], batched=False)
assert status == 200
print(f"smoke OK: trpc_rs_py {trpc_rs_py.__version__} (_core {c.__version__})")
PY
}

# --- build steps (one function per target) -----------------------------------

maturin() { uv run maturin "$@"; }

build_macos_arm64() {
  maturin build --release -F "$FEATURES" -o "$DIST/macos-arm64"
}
build_macos_x86_64() {
  maturin build --release --target x86_64-apple-darwin \
    -F "$FEATURES" -o "$DIST/macos-x86_64"
}
build_linux_x86_64() {
  maturin build --release --target x86_64-unknown-linux-gnu --zig \
    -F "$FEATURES" -o "$DIST/linux-x86_64"
}
build_linux_x86_64_musl() {
  maturin build --release --target x86_64-unknown-linux-musl --zig \
    -F "$FEATURES" -o "$DIST/linux-x86_64-musl"
}
build_linux_aarch64() {
  maturin build --release --target aarch64-unknown-linux-gnu --zig \
    -F "$FEATURES" -o "$DIST/linux-aarch64"
}
build_linux_aarch64_musl() {
  maturin build --release --target aarch64-unknown-linux-musl --zig \
    -F "$FEATURES" -o "$DIST/linux-aarch64-musl"
}
build_windows_x86_64() {
  # maturin detects the msvc target and uses its bundled cargo-xwin:
  # clang-cl + lld-link + an auto-downloaded MSVC SDK. First run downloads
  # ~300 MB; afterwards it is served from the cache.
  maturin build --release --target x86_64-pc-windows-msvc --zig \
    -F "$FEATURES" -o "$DIST/windows-x86_64"
}

# --- verify steps ------------------------------------------------------------

# Return codes: 0 = install-verified, 2 = build-only (with reason on stdout),
# 1 = verification attempted and failed.
verify_macos() {   # <subdir> <uv-python-request> [arch-prefix...]
  local sub="$1" req="$2"; shift 2
  local py; py="$(uv python find "$req" 2>/dev/null || true)"
  if [ -z "$py" ]; then
    say "build-only: no interpreter matching '$req' (install one with: uv python install $req)"
    return 2
  fi
  local venv="$DIST/venv-$sub" wheel
  wheel="$(echo "$DIST/$sub"/*.whl)"
  [ -f "$wheel" ] || { say "build-only: no wheel found"; return 2; }
  rm -rf "$venv"
  if ! "$@" "$py" -m venv "$venv" >>"$LOGS/verify-$sub.log" 2>&1; then
    say "build-only: could not create venv with $py (see logs/verify-$sub.log)"
    return 2
  fi
  if "$@" "$venv/bin/python" -m pip install -q "$wheel" >>"$LOGS/verify-$sub.log" 2>&1 \
     && "$@" "$venv/bin/python" "$DIST/smoke.py" >>"$LOGS/verify-$sub.log" 2>&1; then
    say "install-verified in scratch venv ($("$@" "$venv/bin/python" -c 'import platform; print(platform.machine())'))"
    return 0
  fi
  say "VERIFY FAILED (see logs/verify-$sub.log)"
  return 1
}

verify_macos_arm64()  { verify_macos macos-arm64 "cpython-3.14-macos-aarch64-none"; }
verify_macos_x86_64() { verify_macos macos-x86_64 "cpython-3.14-macos-x86_64-none" arch -x86_64; }

verify_docker() {    # <subdir> <docker-platform> <image>
  local sub="$1" plat="$2" image="$3" wheel
  wheel="$(echo "$DIST/$sub"/*.whl)"
  [ -f "$wheel" ] || { say "build-only: no wheel found"; return 2; }
  if ! have docker; then
    say "build-only: docker not installed"
    return 2
  fi
  if ! docker info >/dev/null 2>&1; then
    say "build-only: docker daemon not running"
    return 2
  fi
  if docker run --rm -i --platform "$plat" \
       -v "$DIST:/w:ro" "$image" sh -c '
         set -e
         pip install -q "/w/'"$sub"'/"*.whl
         python /w/smoke.py
       ' >>"$LOGS/verify-$sub.log" 2>&1; then
    say "install-verified in docker $image ($plat)"
    return 0
  fi
  say "VERIFY FAILED (see logs/verify-$sub.log)"
  return 1
}

verify_linux_x86_64()       { verify_docker linux-x86_64 linux/amd64 python:3.14-slim; }
verify_linux_x86_64_musl()  { verify_docker linux-x86_64-musl linux/amd64 python:3.14-alpine; }
verify_linux_aarch64()      { verify_docker linux-aarch64 linux/arm64 python:3.14-slim; }
verify_linux_aarch64_musl() { verify_docker linux-aarch64-musl linux/arm64 python:3.14-alpine; }

verify_windows_x86_64() {
  local wheel; wheel="$(echo "$DIST/windows-x86_64"/*.whl)"
  [ -f "$wheel" ] || { say "build-only: no wheel found"; return 2; }
  # No Windows runner exists locally; do the strongest check available —
  # the wheel must unpack and contain a PE32+ x86-64 extension DLL.
  local tmp="$DIST/win-check"
  rm -rf "$tmp"; mkdir -p "$tmp"
  if unzip -o -q "$wheel" -d "$tmp" \
     && file "$tmp/trpc_rs_py/_core.pyd" 2>/dev/null | grep -q "PE32+.*x86-64"; then
    say "build-only (no Windows runner): wheel holds PE32+ x86-64 _core.pyd"
    rm -rf "$tmp"
    return 2
  fi
  say "VERIFY FAILED: _core.pyd missing or not a PE32+ x86-64 DLL"
  rm -rf "$tmp"
  return 1
}

# --- target registry ----------------------------------------------------------
# NOTE: stock /bin/bash on macOS is 3.2 — no associative arrays. Lookups are
# case statements and the summary is an indexed ROWS array instead.

TARGET_NAMES="macos-arm64 macos-x86_64 linux-x86_64 linux-x86_64-musl linux-aarch64 linux-aarch64-musl windows-x86_64"

toolchain() {
  case "$1" in
    macos-arm64)        echo "native clang" ;;
    macos-x86_64)       echo "native clang -arch x86_64" ;;
    linux-*-musl)       echo "zig cc (musl)" ;;
    linux-*)            echo "zig cc (glibc 2.17)" ;;
    windows-x86_64)     echo "clang-cl/lld-link via cargo-xwin" ;;
    *)                  echo "?" ;;
  esac
}

rust_target() {
  case "$1" in
    macos-arm64)        echo "aarch64-apple-darwin" ;;
    macos-x86_64)       echo "x86_64-apple-darwin" ;;
    linux-x86_64)       echo "x86_64-unknown-linux-gnu" ;;
    linux-x86_64-musl)  echo "x86_64-unknown-linux-musl" ;;
    linux-aarch64)      echo "aarch64-unknown-linux-gnu" ;;
    linux-aarch64-musl) echo "aarch64-unknown-linux-musl" ;;
    windows-x86_64)     echo "x86_64-pc-windows-msvc" ;;
  esac
}

# --- driver -------------------------------------------------------------------

if [ -n "$ONLY" ]; then
  WANTED=$(echo "$ONLY" | tr ',' ' ')
else
  WANTED="$TARGET_NAMES"
fi

write_smoke

ROWS=()
failures=0

for name in $WANTED; do
  fn="build_$(echo "$name" | tr '-' '_')"
  if ! declare -F "$fn" >/dev/null; then
    say "wheels.sh: unknown target '$name'" >&2
    continue
  fi

  # Preflight: the rust target and (for --zig targets) zig must exist.
  rt="$(rust_target "$name")"
  preflight=""
  rustup target list --installed 2>/dev/null | grep -qx "$rt" \
    || preflight="missing rust target $rt (rustup target add $rt)"
  case "$fn" in
    *linux*|*windows*) have zig || preflight="zig not installed" ;;
  esac
  have uv || preflight="uv not installed"

  say "== $name =="
  if [ -n "$preflight" ]; then
    say "  SKIP: $preflight"
    ROWS+=("$(printf '%-20s  %-31s  %-7s  %-14s  %s' "$name" "$(toolchain "$name")" "SKIP" "-" "—")")
    continue
  fi

  rm -rf "${DIST:?}/$name"
  start=$SECONDS
  build_status="" wheel="—"
  if run_with_timeout "$PER_TARGET_TIMEOUT" "$fn" >"$LOGS/$name.log" 2>&1; then
    wheel="$(basename "$(echo "$DIST/$name"/*.whl)" 2>/dev/null)"
    if [ -f "$DIST/$name/$wheel" ]; then
      build_status="OK"
      say "  built $wheel  ($((SECONDS - start))s, log: logs/$name.log)"
    else
      build_status="FAIL(no wheel)"
      say "  FAILED: no wheel produced (see logs/$name.log)"
    fi
  else
    rc=$?
    [ "$rc" -gt 128 ] && build_status="TIMEOUT" || build_status="FAIL"
    say "  FAILED (rc=$rc, see logs/$name.log)"
  fi

  verify_status="-"
  if [ "$build_status" = "OK" ]; then
    if [ "$DO_VERIFY" -eq 1 ]; then
      vfn="verify_$(echo "$name" | tr '-' '_')"
      msg="$("$vfn" 2>>"$LOGS/verify-$name.log")"
      vrc=$?
      say "  $msg"
      case $vrc in
        0) verify_status="YES" ;;
        2) verify_status="build-only" ;;
        *) verify_status="FAILED" ;;
      esac
    else
      verify_status="skipped"
    fi
  fi

  ROWS+=("$(printf '%-20s  %-31s  %-7s  %-14s  %s' \
    "$name" "$(toolchain "$name")" "$build_status" "$verify_status" "$wheel")")
  case "$build_status" in OK|SKIP) ;; *) failures=$((failures + 1)) ;; esac
  [ "$verify_status" = "FAILED" ] && failures=$((failures + 1))
done

# --- summary table ------------------------------------------------------------

say ""
say "target               toolchain                        build    verify          wheel"
say    "-------------------  -------------------------------  -------  --------------  -----"
for row in "${ROWS[@]}"; do say "$row"; done
say ""
say "wheels: $DIST  (logs: $LOGS)"
if [ "$failures" -gt 0 ]; then
  say "wheels.sh: $failures target(s) failed — see logs above"
  exit 1
fi
say "wheels.sh: all attempted targets succeeded"
