#!/usr/bin/env bash
# Smoke test for examples/feature-auth-context: starts the app with uvicorn on
# a free port, checks the public procedure answers without credentials, the
# protected procedure rejects missing/invalid bearer tokens with UNAUTHORIZED
# (HTTP 401) and resolves per-token users, checks the generated TypeScript
# fixture for drift, and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/feature-auth-context/generate_types.py` if it drifts).
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
        "`uv run python examples/feature-auth-context/generate_types.py`",
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
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/greeting"; then
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

# The public procedure answers without credentials.
expect "greeting is public" \
  '{"result":{"data":"Hello, world!"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/greeting")"

# The protected procedure rejects a missing token: HTTP 401 + UNAUTHORIZED.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/me")"
expect "me without token maps to HTTP 401" "401" "$status"
code="$(curl -s "http://127.0.0.1:$PORT/trpc/me" | uv run --project "$ROOT" python -c 'import json,sys; e=json.load(sys.stdin)["error"]; print(e["data"]["code"], e["data"]["httpStatus"], e["data"]["path"])')"
expect "error envelope carries UNAUTHORIZED + path" "UNAUTHORIZED 401 me" "$code"

# An unknown bearer token is rejected the same way.
status="$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer wrong-token' "http://127.0.0.1:$PORT/trpc/me")"
expect "me with bad token maps to HTTP 401" "401" "$status"

# A valid bearer token resolves the user through the context.
expect "me with Ada's token" \
  '{"result":{"data":{"id":1,"name":"Ada"}}}' \
  "$(curl -s -H 'Authorization: Bearer token-ada' "http://127.0.0.1:$PORT/trpc/me")"
expect "me with Grace's token" \
  '{"result":{"data":{"id":2,"name":"Grace"}}}' \
  "$(curl -s -H 'Authorization: Bearer token-grace' "http://127.0.0.1:$PORT/trpc/me")"

# The public procedure stays public even when a token is sent.
expect "greeting ignores auth" \
  '{"result":{"data":"Hello, world!"}}' \
  "$(curl -s -H 'Authorization: Bearer token-ada' "http://127.0.0.1:$PORT/trpc/greeting")"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: feature-auth-context passed"
