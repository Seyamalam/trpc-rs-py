#!/usr/bin/env bash
# Smoke test for examples/flask-basic: starts the app with werkzeug's
# threaded dev server on a free port, exercises query / mutation / batching /
# errors / REST / OpenAPI over real HTTP, checks the generated TypeScript
# fixture for drift, and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/flask-basic/generate_types.py` if it drifts).
uv run --project "$ROOT" python - "$PWD/app-router.d.ts" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app import build_router

from trpc_rs_py import generate_types

committed = Path(sys.argv[1]).read_text()
if committed != generate_types(build_router()):
    print(
        "smoke: app-router.d.ts is stale; run "
        "`uv run python examples/flask-basic/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# Pick a free port.
PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

PORT="$PORT" uv run --project "$ROOT" python app.py &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/user_by_id?input=%7B%22user_id%22%3A1%7D"; then
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

# Mutation with Pydantic-validated input.
expect "create_user mutation" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/create_user?batch=1" -H 'content-type: application/json' -d '{"0":{"user":{"name":"Ada","age":36}}}')"

# Query by id.
expect "user_by_id query" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%7D")"

# Batched calls stay in order.
expect "batched order" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}},{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/user_by_id,user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%2C%221%22%3A%7B%22user_id%22%3A1%7D%7D")"

# Unknown user maps to HTTP 404.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A99%7D%7D")"
expect "NOT_FOUND maps to HTTP 404" "404" "$status"

# Invalid input maps to HTTP 400.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A%22not-a-number%22%7D%7D")"
expect "invalid input maps to HTTP 400" "400" "$status"

# The same router answers over REST through the WSGI bridge.
expect "REST /api/greeting" \
  '"Hello, Ada!"' \
  "$(curl -s "http://127.0.0.1:$PORT/api/greeting/Ada")"

# The OpenAPI document is served too.
expect "OpenAPI document served" \
  'flask-basic' \
  "$(curl -s "http://127.0.0.1:$PORT/api/openapi.json" | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)["info"]["title"])')"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: flask-basic passed"
