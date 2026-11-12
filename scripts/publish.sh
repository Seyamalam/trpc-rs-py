#!/usr/bin/env bash
# Publish the workspace version's artifacts to PyPI (MISSION.md section 13):
# the platform wheel matrix in dist/wheels/, the native wheel and the sdist
# in target/wheels/. Uploads with twine, which reads ~/.pypirc — the same
# credential maturin's (deprecated) upload command used.
#
# Build the artifacts first:
#   scripts/release.sh <version>   # tag + native wheel in target/wheels/
#   scripts/wheels.sh              # abi3 matrix in dist/wheels/
#   uv run maturin build --sdist -o target/wheels
#
# Usage: scripts/publish.sh [--check-only]
set -euo pipefail
cd "$(dirname "$0")/.."

VER=$(python3 -c 'import re; print(re.search(r"(?m)^version = \"([^\"]+)\"", open("Cargo.toml").read()).group(1))')
# Cargo semver -> PEP 440: 1.0.0-beta.1 -> 1.0.0b1 (what the wheels are named).
PYVER=$(VER="$VER" python3 -c 'import os, re; v = os.environ["VER"]; print(re.sub(r"-(alpha|beta|rc)\.(\d+)$", lambda m: {"alpha": "a", "beta": "b", "rc": "rc"}[m.group(1)] + m.group(2), v))')

shopt -s nullglob
files=(
  dist/wheels/*/trpc_rs_py-"$PYVER"-*.whl
  target/wheels/trpc_rs_py-"$PYVER"-*.whl
  target/wheels/trpc_rs_py-"$PYVER".tar.gz
)
if [ "${#files[@]}" -eq 0 ]; then
  echo "publish: no artifacts for $VER ($PYVER)" >&2
  echo "publish: run scripts/wheels.sh and 'uv run maturin build --sdist -o target/wheels' first" >&2
  exit 1
fi
echo "publish: ${#files[@]} artifact(s) for $VER ($PYVER):"
printf '  %s\n' "${files[@]}"

uvx twine check "${files[@]}"
if [ "${1:-}" = "--check-only" ]; then
  echo "publish: metadata check passed; skipping upload"
  exit 0
fi
uvx twine upload "${files[@]}"
echo "publish: uploaded to PyPI"
