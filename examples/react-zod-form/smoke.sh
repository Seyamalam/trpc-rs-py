#!/usr/bin/env bash
# Smoke test for examples/react-zod-form: checks the generated TypeScript
# declaration AND Zod schemas for drift, starts the FastAPI backend on a
# free port, curl-asserts a valid and an invalid `register` mutation plus
# the `registrations` query, then installs the frontend dependencies if
# needed and typechecks the React form app against the Python router.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed frontend/app-router.d.ts and frontend/app-schemas.ts must
# match the example app (regenerate with
# `uv run python examples/react-zod-form/generate_types.py` if they drift).
uv run --project "$ROOT" python - "$PWD/frontend/app-router.d.ts" "$PWD/frontend/app-schemas.ts" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app import build_router

from trpc_rs_py import generate_types, generate_zod

stale = []
if Path(sys.argv[1]).read_text() != generate_types(build_router()):
    stale.append("frontend/app-router.d.ts")
if Path(sys.argv[2]).read_text() != generate_zod(build_router()):
    stale.append("frontend/app-schemas.ts")
if stale:
    print(
        "smoke: "
        + " and ".join(stale)
        + " is stale; run `uv run python examples/react-zod-form/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# Frontend: install dependencies if needed, then typecheck the React form
# (strict mode) — the Zod schema and the tRPC types are both generated
# from the same Python model.
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
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/registrations"; then
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

expect "registrations starts empty" \
  '{"result":{"data":[]}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/registrations")"

# A payload that passes the generated Zod schema is accepted server-side.
expect "register valid form" \
  '[{"result":{"data":{"age":36,"email":"ada@example.com","id":1,"newsletter":true,"username":"ada_lovelace"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/register?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada_lovelace","email":"ada@example.com","age":36,"newsletter":true}}}')"

# The server enforces the same constraints: an invalid form is BAD_REQUEST.
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/trpc/register?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"A!","email":"bad","age":12}}}')"
expect "invalid form maps to HTTP 400" "400" "$status"
code="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/register?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"A!","email":"bad","age":12}}}' | uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["data"]["code"])')"
expect "invalid form carries BAD_REQUEST" "BAD_REQUEST" "$code"

expect "registrations returns the new entry" \
  '{"result":{"data":[{"age":36,"email":"ada@example.com","id":1,"newsletter":true,"username":"ada_lovelace"}]}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/registrations")"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: react-zod-form passed"
