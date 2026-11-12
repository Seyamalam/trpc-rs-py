#!/usr/bin/env bash
# Smoke test for examples/python-client: starts the uvicorn-served app on a
# free port, checks the generated Python stub and TypeScript fixture for
# drift, exercises the tRPC Python client (query / mutation / batch / typed
# error) plus one curl error-envelope assert, and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed generated files must match the example app (regenerate with
# `uv run python examples/python-client/generate_client.py` and
# `generate_types.py` if they drift).
uv run --project "$ROOT" python - "$PWD/app-router.d.ts" "$PWD/generated_client.py" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app import build_router

from trpc_rs_py import generate_types
from trpc_rs_py.pyclient import generate_python_client

router = build_router()
stale = []
if Path(sys.argv[1]).read_text() != generate_types(router):
    stale.append("app-router.d.ts (run generate_types.py)")
if Path(sys.argv[2]).read_text() != generate_python_client(router):
    stale.append("generated_client.py (run generate_client.py)")
if stale:
    print(f"smoke: stale generated files: {', '.join(stale)}", file=sys.stderr)
    sys.exit(1)
PY

# Pick a free port.
PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

PORT="$PORT" uv run --project "$ROOT" python app.py &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/trpc/greeting?input=%7B%22name%22%3A%22x%22%7D"; then
    break
  fi
  sleep 0.2
done

# The typed error also arrives over raw HTTP: HTTP 404 and the official
# error envelope carrying the declared data payload.
status="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/trpc/post_by_id?input=%7B%22post_id%22%3A99%7D")"
if [ "$status" != "404" ]; then
  echo "smoke: FAIL - expected HTTP 404, got $status" >&2
  exit 1
fi
echo "smoke: ok  - typed error maps to HTTP 404"
code="$(curl -s "http://127.0.0.1:$PORT/trpc/post_by_id?input=%7B%22post_id%22%3A99%7D" | uv run --project "$ROOT" python -c 'import json,sys; e=json.load(sys.stdin)["error"]; print(e["data"]["code"], e["data"]["post_id"])')"
if [ "$code" != "NOT_FOUND 99" ]; then
  echo "smoke: FAIL - unexpected error envelope: $code" >&2
  exit 1
fi
echo "smoke: ok  - error envelope carries code + typed data"

# The client script asserts query / mutation / batch / typed-error
# round-trips and the generated AppRouterClient over the live socket.
uv run --project "$ROOT" python client.py "http://127.0.0.1:$PORT/trpc"

echo "smoke: python-client passed"
