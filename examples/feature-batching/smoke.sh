#!/usr/bin/env bash
# Smoke test for examples/feature-batching: starts the app on a free port,
# checks the generated TypeScript fixture for drift, asserts the raw wire
# shape of one `?batch=1` GET (ordered per-call envelopes, including an error
# entry and the batch HTTP status), exercises the Python client's batch API,
# and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/feature-batching/generate_types.py` if it drifts).
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
        "`uv run python examples/feature-batching/generate_types.py`",
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
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/add?input=%7B%22a%22%3A1%2C%22b%22%3A1%7D"; then
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

# One GET carries four calls across three procedures; the body is an ordered
# list of per-call envelopes. The failing call produces an error entry, and
# the batch's HTTP status follows its worst per-call error (400 here) while
# still returning every entry — this is the official v11 wire contract.
BATCH_URL="http://127.0.0.1:$PORT/trpc/add,multiply,divide,divide?batch=1&input=%7B%220%22%3A%7B%22a%22%3A2%2C%22b%22%3A3%7D%2C%221%22%3A%7B%22a%22%3A2%2C%22b%22%3A3%7D%2C%222%22%3A%7B%22a%22%3A7%2C%22b%22%3A2%7D%2C%223%22%3A%7B%22a%22%3A1%2C%22b%22%3A0%7D%7D"

status="$(curl -s -o /dev/null -w '%{http_code}' "$BATCH_URL")"
expect "batch HTTP status follows worst per-call error" "400" "$status"

ordered="$(curl -s "$BATCH_URL" | uv run --project "$ROOT" python -c '
import json, sys
body = json.load(sys.stdin)
assert isinstance(body, list) and len(body) == 4, f"not a 4-entry batch: {body!r}"
print(
    body[0]["result"]["data"],
    body[1]["result"]["data"],
    body[2]["result"]["data"],
    body[3]["error"]["data"]["code"],
    body[3]["error"]["data"]["path"],
)')"
expect "ordered per-call envelopes incl. error entry" "5 6 3.5 BAD_REQUEST divide" "$ordered"

# The Python client drives the same batch and unwraps per-call results.
uv run --project "$ROOT" python client.py "http://127.0.0.1:$PORT/trpc" || fail=1

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: feature-batching passed"
