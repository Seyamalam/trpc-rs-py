#!/usr/bin/env bash
# Step 8 of the CI gate: TypeScript compatibility, type tests, and npm package tests.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f clients/ts-tests/package.json ]; then
  echo "ts-tests: no package.json, skipping"
  exit 0
fi

cd clients/ts-tests
pnpm install --frozen-lockfile
pnpm format:check
pnpm typecheck
pnpm test
