#!/usr/bin/env bash
# Smoke test for the `flask-only` init template: scaffolds a project with
# `trpc-py init`, starts the generated server on a free port, and calls
# tRPC, REST, and OpenAPI over real HTTP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
SERVER_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

cd "$ROOT"
uv run trpc-py init tmpl-smoke --template flask-only --dir "$TMP/app" >/dev/null

PORT="$(uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

(cd "$TMP/app" && PORT="$PORT" uv run --project "$ROOT" python server.py >/dev/null 2>&1) &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22x%22%7D"; then
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

expect "tRPC query" \
  '{"result":{"data":"Hello, world!"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22world%22%7D")"

expect "REST route" \
  '"Hello, world!"' \
  "$(curl -s "http://127.0.0.1:$PORT/api/greeting/world")"

expect "subscription streams SSE" \
  '0 1 2' \
  "$(curl -sN "http://127.0.0.1:$PORT/trpc/ticks" | grep -o '^data: [0-9]*' | awk '{print $2}' | xargs)"

expect "OpenAPI doc" \
  'tmpl-smoke' \
  "$(curl -s "http://127.0.0.1:$PORT/api/openapi.json" | uv run python -c 'import json,sys; print(json.load(sys.stdin)["info"]["title"])')"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: flask-only template passed"
