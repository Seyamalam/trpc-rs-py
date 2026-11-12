#!/usr/bin/env bash
# Smoke test for examples/feature-pydantic-vs: starts the app on a free
# port, checks the generated TypeScript fixture for drift, then asserts
# the SAME signup payload behaves per its validation style — Pydantic
# coercion and fixed messages on `pydantic.signup`, strict hand-rolled
# checks and custom messages on `manual.signup` — and stops the server.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

# The committed app-router.d.ts must match the example app (regenerate
# with `uv run python examples/feature-pydantic-vs/generate_types.py` if
# it drifts). The manual side's `form: dict` must stay
# `Record<string, unknown>` — that loss of shape IS the trade-off.
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
        "`uv run python examples/feature-pydantic-vs/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
if "form: Record<string, unknown>" not in committed:
    print(
        "smoke: app-router.d.ts lost the manual side's "
        "`form: Record<string, unknown>`",
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

message() {
  # Print `error.message` from a single batched error envelope.
  uv run --project "$ROOT" python -c 'import json,sys; print(json.load(sys.stdin)[0]["error"]["message"])'
}

# The happy path is identical on both sides.
expect "pydantic.signup valid" \
  '[{"result":{"data":{"age":36,"referral":null,"username":"ada"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/pydantic.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":36}}}')"
expect "manual.signup valid" \
  '[{"result":{"data":{"age":36,"referral":null,"username":"ada"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/manual.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":36}}}')"

# Coercion: Pydantic's lax mode accepts "36" as 36; the isinstance-based
# manual check rejects it. Strict Pydantic types would reject it too.
expect "pydantic.signup coerces string age" \
  '[{"result":{"data":{"age":36,"referral":null,"username":"ada"}}}]' \
  "$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/pydantic.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":"36"}}}')"
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/manual.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":"36"}}}' | message)"
expect "manual.signup rejects string age" \
  "Invalid input: age: integer between 18 and 150 required" "$msg"

# Error detail: Pydantic's failure is the fixed, value-free message; the
# manual side wrote its own per-field message.
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/pydantic.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":12}}}' | message)"
expect "pydantic.signup age=12 message is fixed+generic" \
  "Invalid input: form: Invalid value" "$msg"
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/manual.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":12}}}' | message)"
expect "manual.signup age=12 message is custom" \
  "Invalid input: age: integer between 18 and 150 required" "$msg"

# Extra keys: the model's extra="forbid" and the manual check both reject.
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/pydantic.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":36,"nickname":"d"}}}' | message)"
expect "pydantic.signup rejects extra key" \
  "Invalid input: form: Extra inputs are not permitted" "$msg"
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/manual.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":36,"nickname":"d"}}}' | message)"
expect "manual.signup names the extra key" \
  "Invalid input: nickname: unexpected field" "$msg"

# The isinstance(True, int) gotcha: `true` must not pass as an int.
msg="$(curl -s -X POST "http://127.0.0.1:$PORT/trpc/manual.signup?batch=1" -H 'content-type: application/json' -d '{"0":{"form":{"username":"ada","age":true}}}' | message)"
expect "manual.signup rejects boolean age" \
  "Invalid input: age: integer between 18 and 150 required" "$msg"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "smoke: feature-pydantic-vs passed"
