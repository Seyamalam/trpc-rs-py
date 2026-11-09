#!/usr/bin/env bash
# Step 8 of the CI gate: TypeScript compatibility, type tests, and runtime
# @trpc/client compatibility tests against the Python ASGI adapters.
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

# The committed generated declarations must match the routers they describe.
uv run --project ../.. python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from router_source import build_router

from trpc_rs_py import generate_types

committed = Path("src/app-router.d.ts").read_text()
if committed != generate_types(build_router()):
    print("ts-tests: src/app-router.d.ts is stale; run `uv run python clients/ts-tests/generate-fixture.py`", file=sys.stderr)
    sys.exit(1)
PY
uv run --project ../.. python - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, "fixtures")
from router import build_fixture_router

from trpc_rs_py import generate_types

committed = Path("fixtures/app-router.d.ts").read_text()
if committed != generate_types(build_fixture_router()):
    print("ts-tests: fixtures/app-router.d.ts is stale; run `uv run python clients/ts-tests/fixtures/generate_types.py`", file=sys.stderr)
    sys.exit(1)
PY
uv run --project ../.. python - <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, "fixtures")
from router import build_fixture_router

from trpc_rs_py import generate_openapi

# Compare parsed documents: Prettier reformats the committed file (it
# collapses short arrays), so a byte comparison would always report stale.
expected = generate_openapi(build_fixture_router(), title="Fixture API", version="0.1.0")
if json.loads(Path("fixtures/rest.openapi.json").read_text()) != expected:
    print("ts-tests: fixtures/rest.openapi.json is stale; regenerate it with generate_openapi", file=sys.stderr)
    sys.exit(1)
PY

# The committed openapi-typescript types must match the served document.
# Prettier formats both sides so only semantic drift fails the diff.
pnpm exec openapi-typescript fixtures/rest.openapi.json -o src/rest-api.generated.d.ts
pnpm exec prettier --write src/rest-api.generated.d.ts >/dev/null
diff -q src/rest-api.d.ts src/rest-api.generated.d.ts >/dev/null || {
  rm -f src/rest-api.generated.d.ts
  echo "ts-tests: src/rest-api.d.ts is stale; run \`pnpm exec openapi-typescript fixtures/rest.openapi.json -o src/rest-api.d.ts && pnpm format\`" >&2
  exit 1
}
rm -f src/rest-api.generated.d.ts

# Runtime compatibility: spawns the FastAPI/Starlette fixture servers and runs
# the official @trpc/client v11 against them over real HTTP.
pnpm test
