#!/usr/bin/env bash
# Smoke test for examples/ai-chat: starts the app, checks the generated
# TypeScript fixture for drift, streams the chat subscription over real SSE,
# and exercises the REST + OpenAPI surface.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

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
        "`uv run python examples/ai-chat/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

uv run --project "$ROOT" uvicorn --app-dir . --host 127.0.0.1 --port "$PORT" --log-level error app:app &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/models"; then
    break
  fi
  sleep 0.1
done

# The chat subscription streams tracked SSE frames.
STREAM="$(curl -sN --max-time 10 "http://127.0.0.1:$PORT/trpc/chat?input=%7B%22prompt%22%3A%22hi%22%7D")"
echo "$STREAM" | grep -q "event: connected" || { echo "smoke: no connected event"; exit 1; }
echo "$STREAM" | grep -q "id:" || { echo "smoke: no tracked id"; exit 1; }
echo "$STREAM" | grep -q '"token"' || { echo "smoke: no token data"; exit 1; }
echo "$STREAM" | grep -q '"done":true' || { echo "smoke: stream did not finish"; exit 1; }
echo "smoke: ok  - chat subscription streams tracked tokens"

# REST + OpenAPI surface.
MODELS="$(curl -s "http://127.0.0.1:$PORT/api/models")"
echo "$MODELS" | grep -q '"sim-1"' || { echo "smoke: REST /api/models failed"; exit 1; }
echo "smoke: ok  - REST /api/models"

DOC="$(curl -s "http://127.0.0.1:$PORT/api/openapi.json")"
echo "$DOC" | grep -q '"/models"' || { echo "smoke: openapi.json missing /models"; exit 1; }
echo "smoke: ok  - OpenAPI document served"

echo "smoke: ai-chat passed"
