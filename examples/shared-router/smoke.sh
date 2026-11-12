#!/usr/bin/env bash
# Smoke test for examples/shared-router: serves ONE router module through
# both a FastAPI app (mount) and a Flask app (mount_wsgi) on two free ports,
# asserts both adapters return byte-identical results for query / mutation /
# error envelope over real HTTP, checks the generated TypeScript fixture for
# drift, and stops both servers.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the shared router (regenerate
# with `uv run python examples/shared-router/generate_types.py` if it drifts).
uv run --project "$ROOT" python - "$PWD/app-router.d.ts" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from shared_router import build_router

from trpc_rs_py import generate_types

committed = Path(sys.argv[1]).read_text()
if committed != generate_types(build_router()):
    print(
        "smoke: app-router.d.ts is stale; run "
        "`uv run python examples/shared-router/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# Pick two free ports.
free_port() {
  uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}
PORT_FASTAPI="$(free_port)"
PORT_FLASK="$(free_port)"

PORT="$PORT_FASTAPI" uv run --project "$ROOT" python fastapi_app.py &
FASTAPI_PID=$!
PORT="$PORT_FLASK" uv run --project "$ROOT" python flask_app.py &
FLASK_PID=$!
trap 'kill "$FASTAPI_PID" "$FLASK_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  fastapi_up=0; flask_up=0
  curl -s -o /dev/null "http://127.0.0.1:$PORT_FASTAPI/trpc/greeting?input=%7B%22name%22%3A%22x%22%7D" && fastapi_up=1
  curl -s -o /dev/null "http://127.0.0.1:$PORT_FLASK/trpc/greeting?input=%7B%22name%22%3A%22x%22%7D" && flask_up=1
  if [ "$fastapi_up" = 1 ] && [ "$flask_up" = 1 ]; then
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

# both <label> <request-path-suffix>: fire the same request at each app and
# require byte-identical bodies — the point of the shared router.
both() {
  local label="$1" suffix="$2"
  local fastapi flask
  fastapi="$(curl -s "http://127.0.0.1:$PORT_FASTAPI$suffix")"
  flask="$(curl -s "http://127.0.0.1:$PORT_FLASK$suffix")"
  expect "$label (fastapi == flask)" "$fastapi" "$flask"
}

# Identical query on both adapters.
both "greeting query" "/trpc/greeting?input=%7B%22name%22%3A%22Ada%22%7D"
expect "greeting payload" \
  '{"result":{"data":"Hello, Ada!"}}' \
  "$(curl -s "http://127.0.0.1:$PORT_FASTAPI/trpc/greeting?input=%7B%22name%22%3A%22Ada%22%7D")"

# Identical mutation on both adapters (each process owns its state).
for port in "$PORT_FASTAPI" "$PORT_FLASK"; do
  curl -s -X POST "http://127.0.0.1:$port/trpc/create_user?batch=1" \
    -H 'content-type: application/json' \
    -d '{"0":{"user":{"name":"Ada","age":36}}}' > /dev/null
done
both "user_by_id query" \
  "/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%7D"
expect "user_by_id payload" \
  '[{"result":{"data":{"age":36,"id":1,"name":"Ada"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT_FLASK/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%7D")"

# Identical error envelope: HTTP status and body match on both adapters.
for port in "$PORT_FASTAPI" "$PORT_FLASK"; do
  status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A99%7D%7D")"
  expect "NOT_FOUND maps to HTTP 404 (port $port)" "404" "$status"
done
both "error envelope" \
  "/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A99%7D%7D"
code="$(curl -s "http://127.0.0.1:$PORT_FLASK/trpc/user_by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A99%7D%7D" | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["data"]["code"])')"
expect "error envelope carries NOT_FOUND" "NOT_FOUND" "$code"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: shared-router passed"
