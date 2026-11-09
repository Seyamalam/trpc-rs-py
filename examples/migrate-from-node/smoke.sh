#!/usr/bin/env bash
# Smoke test for examples/migrate-from-node: builds @trpc-rs-py/link, starts
# the Python FastAPI server and the legacy Node tRPC server on free ports,
# then runs the merged client and checks which backend answered each call.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate with
# `uv run python examples/migrate-from-node/generate_types.py` if it drifts).
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
        "`uv run python examples/migrate-from-node/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# The example consumes the compiled package output.
pnpm --dir "$ROOT/packages/link" build >/dev/null
pnpm install --frozen-lockfile >/dev/null
pnpm typecheck

PY_PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
NODE_PORT="$(uv run --project "$ROOT" python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

uv run --project "$ROOT" uvicorn --app-dir . --host 127.0.0.1 --port "$PY_PORT" --log-level error app:app &
PY_PID=$!
NODE_SERVER_PORT="$NODE_PORT" node node-server.ts &
NODE_PID=$!
trap 'kill "$PY_PID" "$NODE_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PY_PORT/trpc/posts.byId?input=%7B%22id%22%3A1%7D"; then
    break
  fi
  sleep 0.2
done
for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$NODE_PORT/users.me"; then
    break
  fi
  sleep 0.2
done

OUT="$(NODE_URL="http://127.0.0.1:$NODE_PORT" PYTHON_URL="http://127.0.0.1:$PY_PORT/trpc" node client.ts)"
echo "$OUT"

fail=0
check() {
  if echo "$OUT" | grep -q "$1"; then
    echo "smoke: ok  - $2"
  else
    echo "smoke: FAIL - $2" >&2
    fail=1
  fi
}

check '"Ada (Node)"' "users.me answered by Node"
check '"tRPC types without a codegen step"' "posts.byId answered by Python (explicit procedures)"
check '"Python post about search"' "py.posts_search answered by Python (prefix rule)"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: migrate-from-node passed"
