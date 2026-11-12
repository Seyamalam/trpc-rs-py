#!/usr/bin/env bash
# Smoke test for examples/fastapi-react-vite: checks the generated
# TypeScript declaration for drift, starts the FastAPI backend on a free
# port, curl-asserts the tRPC calls the React app makes (including a CORS
# preflight from the Vite origin), then installs the frontend dependencies
# if needed and typechecks the Vite + React app against the Python router.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed frontend/app-router.d.ts must match the example app
# (regenerate with `uv run python examples/fastapi-react-vite/generate_types.py`
# if it drifts).
uv run --project "$ROOT" python - "$PWD/frontend/app-router.d.ts" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app import build_router

from trpc_rs_py import generate_types

committed = Path(sys.argv[1]).read_text()
if committed != generate_types(build_router()):
    print(
        "smoke: frontend/app-router.d.ts is stale; run "
        "`uv run python examples/fastapi-react-vite/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# Frontend: install dependencies if needed, then typecheck the app against
# the generated AppRouter (strict mode; a wrong input shape fails here).
if [ ! -d frontend/node_modules ]; then
  pnpm --dir frontend install --prefer-offline >/dev/null
fi
pnpm --dir frontend exec tsc --noEmit
echo "smoke: ok  - frontend typechecks against the Python router"

# Pick a free port and start the backend.
PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

PORT="$PORT" uv run --project "$ROOT" python app.py &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22smoke%22%7D"; then
    break
  fi
  sleep 0.2
done

fail=0
expect() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "smoke: ok  - $label"
  else
    echo "smoke: FAIL - $label" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    fail=1
  fi
}

# The same calls the React app issues.
expect "greeting query" \
  '{"result":{"data":"Hello, Ada!"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22Ada%22%7D")"
expect "create_user mutation" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/create_user?batch=1" -H 'content-type: application/json' -d '{"0":{"user":{"name":"Ada","age":36}}}')"
expect "user_by_id query" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%7D")"

status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A99%7D%7D")"
expect "user_by_id NOT_FOUND maps to HTTP 404" "404" "$status"

# The browser call is cross-origin: the cors() wrapper must answer the
# Vite origin's preflight with a 204 carrying allow-origin.
status="$(curl -s -o /dev/null -w '%{http_code}' -X OPTIONS "http://127.0.0.1:$PORT/trpc/greeting" -H "Origin: http://localhost:5173" -H "Access-Control-Request-Method: GET")"
expect "Vite origin preflight" "204" "$status"
origin="$(curl -s -i -X OPTIONS "http://127.0.0.1:$PORT/trpc/greeting" -H "Origin: http://localhost:5173" -H "Access-Control-Request-Method: GET" | grep -i '^access-control-allow-origin:' | tr -d '\r' | cut -d' ' -f2)"
expect "preflight echoes the Vite origin" "http://localhost:5173" "$origin"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: fastapi-react-vite passed"
