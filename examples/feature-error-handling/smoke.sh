#!/usr/bin/env bash
# Smoke test for examples/feature-error-handling: starts one app serving the
# same router in production mode at /trpc and dev mode at /trpc-dev, asserts
# deliberate TRPCError messages pass through in both modes while INTERNAL
# errors and unexpected exceptions are redacted to the fixed production
# message (and DO contain internals in dev), checks the generated TypeScript
# fixture for drift, and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/feature-error-handling/generate_types.py` if it
# drifts).
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
        "`uv run python examples/feature-error-handling/generate_types.py`",
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

env_message() {
  # Print `message|data.code|data.httpStatus` from a single error envelope.
  uv run --project "$ROOT" python -c 'import json,sys; e=json.load(sys.stdin)["error"]; d=e["data"]; print(e["message"], "|", d["code"], "|", d["httpStatus"])'
}

# --- Production mount (/trpc) -------------------------------------------------

expect "prod health" \
  '{"result":{"data":"ok"}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/health")"

# Deliberate API errors pass their message through even in production.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/lookup?input=%7B%22account_id%22%3A9%7D")"
expect "prod lookup NOT_FOUND maps to HTTP 404" "404" "$status"
msg="$(curl -s "http://127.0.0.1:$PORT/trpc/lookup?input=%7B%22account_id%22%3A9%7D" | env_message)"
expect "prod NOT_FOUND message passes through" "Account 9 does not exist | NOT_FOUND | 404" "$msg"

# An unexpected exception is redacted to the fixed message.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/crash")"
expect "prod crash maps to HTTP 500" "500" "$status"
msg="$(curl -s "http://127.0.0.1:$PORT/trpc/crash" | env_message)"
expect "prod crash message is the fixed redaction" "Internal server error | INTERNAL_SERVER_ERROR | 500" "$msg"
body="$(curl -s "http://127.0.0.1:$PORT/trpc/crash")"
if printf '%s' "$body" | grep -q 'hunter2\|10\.0\.0\.5\|postgres'; then
  echo "smoke: FAIL - prod crash body leaks internals: $body" >&2
  fail=1
else
  echo "smoke: ok  - prod crash body leaks no internals"
fi

# A TRPCError that codes INTERNAL_SERVER_ERROR is redacted too.
msg="$(curl -s "http://127.0.0.1:$PORT/trpc/internal" | env_message)"
expect "prod INTERNAL TRPCError is redacted" "Internal server error | INTERNAL_SERVER_ERROR | 500" "$msg"
body="$(curl -s "http://127.0.0.1:$PORT/trpc/internal")"
if printf '%s' "$body" | grep -q 'hunter2'; then
  echo "smoke: FAIL - prod internal body leaks internals: $body" >&2
  fail=1
else
  echo "smoke: ok  - prod internal body leaks no internals"
fi

# --- Development mount (/trpc-dev) --------------------------------------------

# The same deliberate error reads identically in dev.
msg="$(curl -s "http://127.0.0.1:$PORT/trpc-dev/lookup?input=%7B%22account_id%22%3A9%7D" | env_message)"
expect "dev NOT_FOUND message passes through" "Account 9 does not exist | NOT_FOUND | 404" "$msg"

# In dev the unexpected exception's repr reaches the client, internals and all.
body="$(curl -s "http://127.0.0.1:$PORT/trpc-dev/crash")"
if printf '%s' "$body" | grep -q 'hunter2@10\.0\.0\.5'; then
  echo "smoke: ok  - dev crash exposes the exception repr"
else
  echo "smoke: FAIL - dev crash should expose internals, got: $body" >&2
  fail=1
fi
msg="$(printf '%s' "$body" | env_message)"
expect "dev crash code/status" "ValueError('could not connect to postgres://admin:hunter2@10.0.0.5/prod') | INTERNAL_SERVER_ERROR | 500" "$msg"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: feature-error-handling passed"
