#!/usr/bin/env bash
# Step 9 of the CI gate: example and template smoke tests.
# Each example provides examples/<name>/smoke.sh (or templates/<name>/smoke.sh);
# examples without one are skipped with a note until implemented.
set -euo pipefail
cd "$(dirname "$0")/.."

found=0
for smoke in examples/*/smoke.sh templates/*/smoke.sh; do
  [ -e "$smoke" ] || continue
  found=1
  echo "smoke: running $smoke"
  ( cd "$(dirname "$smoke")" && ./smoke.sh )
done

if [ "$found" -eq 0 ]; then
  echo "smoke: no examples/templates registered yet, skipping"
fi
