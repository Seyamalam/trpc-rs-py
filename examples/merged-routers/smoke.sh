#!/usr/bin/env bash
# Smoke test for examples/merged-routers: starts the app with uvicorn on a
# free port, calls dotted procedure paths from each merged/nested sub-router
# (posts.*, users.*, posts.comments.*, meta at root) plus an error envelope
# over real HTTP, checks the generated TypeScript fixture for drift, and
# stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/merged-routers/generate_types.py` if it drifts).
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
        "`uv run python examples/merged-routers/generate_types.py`",
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
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/health"; then
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

# The meta router merged at root keeps its unprefixed paths.
expect "merged root procedure (health)" \
  '{"result":{"data":"ok"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/health")"

# Nested domain routers answer under their dotted prefixes.
expect "posts.create mutation" \
  '[{"result":{"data":{"id":1,"title":"First post"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/posts.create?batch=1" -H 'content-type: application/json' -d '{"0":{"title":"First post"}}')"
expect "posts.by_id query" \
  '[{"result":{"data":{"id":1,"title":"First post"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/posts.by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A1%7D%7D")"

# Nesting composes: comments lives two levels deep at posts.comments.*.
expect "posts.comments.add mutation" \
  '[{"result":{"data":{"body":"nice","post_id":1}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/posts.comments.add?batch=1" -H 'content-type: application/json' -d '{"0":{"post_id":1,"body":"nice"}}')"
expect "posts.comments.for_post query" \
  '[{"result":{"data":[{"body":"nice","post_id":1}]}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/posts.comments.for_post?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A1%7D%7D")"

expect "users.by_id query" \
  '[{"result":{"data":{"id":1,"name":"Ada"}}}]' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/users.by_id?batch=1&input=%7B%220%22%3A%7B%22user_id%22%3A1%7D%7D")"
expect "users.rename mutation" \
  '[{"result":{"data":{"id":1,"name":"Ada Lovelace"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/users.rename?batch=1" -H 'content-type: application/json' -d '{"0":{"user_id":1,"name":"Ada Lovelace"}}')"

# Errors from a nested procedure carry the official error envelope.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/posts.by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A99%7D%7D")"
expect "NOT_FOUND maps to HTTP 404" "404" "$status"
code="$(curl -s "http://127.0.0.1:$PORT/trpc/posts.by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A99%7D%7D" | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["data"]["code"])')"
expect "error envelope carries NOT_FOUND" "NOT_FOUND" "$code"
epath="$(curl -s "http://127.0.0.1:$PORT/trpc/posts.by_id?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A99%7D%7D" | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["data"]["path"])')"
expect "error envelope carries full dotted path" "posts.by_id" "$epath"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: merged-routers passed"
