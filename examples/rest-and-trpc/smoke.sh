#!/usr/bin/env bash
# Smoke test for examples/rest-and-trpc: starts the app with uvicorn on a
# free port, hits ONE router over both protocols (tRPC under /trpc, REST +
# OpenAPI under /api), asserts both error shapes, checks the generated
# TypeScript fixture for drift, and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/rest-and-trpc/generate_types.py` if it drifts).
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
        "`uv run python examples/rest-and-trpc/generate_types.py`",
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
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/greeting/x"; then
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

# The same query answers over tRPC (envelope) and REST (bare payload).
expect "tRPC greeting query" \
  '{"result":{"data":"Hello, Ada!"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22Ada%22%7D")"
expect "REST greeting" \
  '"Hello, Ada!"' \
  "$(curl -s "http://127.0.0.1:$PORT/api/greeting/Ada")"

# Create over REST, read back over tRPC: both protocols share one router.
expect "REST POST /api/posts" \
  '{"id": 1, "priority": 3, "title": "REST post"}' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/api/posts" -H 'content-type: application/json' -d '{"post":{"title":"REST post","priority":3}}')"
expect "tRPC post_by_id reads REST-created post" \
  '[{"result":{"data":{"id":1,"priority":3,"title":"REST post"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/post_by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A1%7D%7D")"

# Create over tRPC, read back over REST.
expect "tRPC create_post mutation" \
  '[{"result":{"data":{"id":2,"priority":0,"title":"tRPC post"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/create_post?batch=1" -H 'content-type: application/json' -d '{"0":{"post":{"title":"tRPC post"}}}')"
expect "REST GET reads tRPC-created post" \
  '{"id": 2, "priority": 0, "title": "tRPC post"}' \
  "$(curl -s "http://127.0.0.1:$PORT/api/posts/2")"

# Errors carry each protocol's own shape from the same TRPCError.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/post_by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A99%7D%7D")"
expect "tRPC NOT_FOUND maps to HTTP 404" "404" "$status"
code="$(curl -s "http://127.0.0.1:$PORT/trpc/post_by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A99%7D%7D" | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["data"]["code"])')"
expect "tRPC error envelope carries NOT_FOUND" "NOT_FOUND" "$code"
expect "REST error shape" \
  '{"error": {"code": "NOT_FOUND", "message": "Post 99 not found"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/api/posts/99")"

# The OpenAPI document describes the same procedures.
paths="$(curl -s "http://127.0.0.1:$PORT/api/openapi.json" | uv run --project "$ROOT" python -c 'import json,sys; d=json.load(sys.stdin); print(d["info"]["title"], sorted(d["paths"]))')"
expect "OpenAPI document served" \
  "rest-and-trpc ['/greeting/{name}', '/posts', '/posts/{post_id}']" \
  "$paths"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: rest-and-trpc passed"
