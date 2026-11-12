#!/usr/bin/env bash
# Smoke test for examples/feature-typed-errors: starts the app on a free
# port, checks the generated TypeScript fixture for drift, asserts typed
# error envelopes carry the declared code + payload fields + httpStatus, and
# stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/feature-typed-errors/generate_types.py` if it
# drifts). It carries the discriminated error union; prove both declared
# codes are in it.
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
        "`uv run python examples/feature-typed-errors/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
for token in ('"NOT_FOUND"', '"TOO_MANY_REQUESTS"', "AppRouterErrorShape"):
    if token not in committed:
        print(f"smoke: app-router.d.ts is missing error union token {token}", file=sys.stderr)
        sys.exit(1)
PY

# Pick a free port.
PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

PORT="$PORT" uv run --project "$ROOT" python app.py &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/order_by_id?input=%7B%22order_id%22%3A1%7D"; then
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

# Happy path.
expect "order_by_id query" \
  '{"result":{"data":{"id":1,"item":"book","quantity":2}}}' \
  "$(curl -s "http://127.0.0.1:$PORT/trpc/order_by_id?input=%7B%22order_id%22%3A1%7D")"

# OrderNotFound: HTTP 404, error.data carries code + typed payload + status.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/order_by_id?input=%7B%22order_id%22%3A99%7D")"
expect "OrderNotFound maps to HTTP 404" "404" "$status"
code="$(curl -s "http://127.0.0.1:$PORT/trpc/order_by_id?input=%7B%22order_id%22%3A99%7D" | uv run --project "$ROOT" python -c 'import json,sys; e=json.load(sys.stdin)["error"]; d=e["data"]; print(e["message"], "|", e["code"], "|", d["code"], d["httpStatus"], d["path"], d["order_id"])')"
expect "NOT_FOUND envelope carries typed payload" "Order 99 not found | -32004 | NOT_FOUND 404 order_by_id 99" "$code"

# First checkout succeeds; resubmitting the same order is RateLimited:
# HTTP 429 and the declared payload fields land in error.data.
expect "place_order mutation" \
  '[{"result":{"data":{"id":1,"item":"book","quantity":2}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/place_order?batch=1" -H 'content-type: application/json' -d '{"0":{"order_id":1}}')"
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/trpc/place_order?batch=1" -H 'content-type: application/json' -d '{"0":{"order_id":1}}')"
expect "RateLimited maps to HTTP 429" "429" "$status"
code="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/place_order?batch=1" -H 'content-type: application/json' -d '{"0":{"order_id":1}}' | uv run --project "$ROOT" python -c 'import json,sys; e=json.load(sys.stdin)[0]["error"]; d=e["data"]; print(d["code"], d["httpStatus"], d["path"], d["retry_after"], d["limit"])')"
expect "TOO_MANY_REQUESTS envelope carries typed payload" "TOO_MANY_REQUESTS 429 place_order 30.0 1" "$code"

# The declared OrderNotFound also fires on the mutation's error union.
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/trpc/place_order?batch=1" -H 'content-type: application/json' -d '{"0":{"order_id":42}}')"
expect "place_order OrderNotFound maps to HTTP 404" "404" "$status"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: feature-typed-errors passed"
