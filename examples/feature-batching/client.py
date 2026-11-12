"""client.py: call the running feature-batching server with one batched request.

Exercises `trpc_rs_py.client.create_client`'s `batch` method: several query
calls travel in a single `?batch=1` GET and come back as an ordered list of
per-call results, where a failing call yields a `TrpcClientError` instance
instead of raising. Run it after starting `app.py`:

    cd examples/feature-batching && uv run --project ../.. python client.py
    # or: uv run --project ../.. python client.py http://127.0.0.1:9000/trpc

Every check prints `client: ok`/`client: FAIL`; the exit code is non-zero if
any check failed, which is how `smoke.sh` drives it.
"""

from __future__ import annotations

import sys
from typing import Any

from trpc_rs_py.client import TrpcClientError, create_client

_failures = 0


def check(label: str, expected: Any, actual: Any) -> None:
    """Print one assertion line in the same style as the smoke scripts."""
    global _failures
    if expected == actual:
        print(f"client: ok  - {label}")
    else:
        print(f"client: FAIL - {label}", file=sys.stderr)
        print(f"  expected: {expected}", file=sys.stderr)
        print(f"  actual:   {actual}", file=sys.stderr)
        _failures += 1


def main() -> int:
    """Run every client check against the URL in argv[1] (default :8000)."""
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/trpc"
    with create_client(url) as client:
        # One HTTP request carries all four calls; the response is a list in
        # the same order, and the divide-by-zero call is an error entry that
        # does not affect its neighbors.
        results = client.batch(
            [
                {"path": "add", "input": {"a": 2, "b": 3}},
                {"path": "multiply", "input": {"a": 2, "b": 3}},
                {"path": "divide", "input": {"a": 7, "b": 2}},
                {"path": "divide", "input": {"a": 1, "b": 0}},
            ]
        )
        check("batch length", 4, len(results))
        check("batch[0] add", 5, results[0])
        check("batch[1] multiply", 6, results[1])
        check("batch[2] divide", 3.5, results[2])
        batch_error = results[3]
        check(
            "batch[3] divide-by-zero is a TrpcClientError",
            True,
            isinstance(batch_error, TrpcClientError) and batch_error.code == "BAD_REQUEST",
        )
        if isinstance(batch_error, TrpcClientError):
            check("batch[3] error message", "Division by zero", batch_error.message)
            check("batch[3] error http status", 400, batch_error.http_status)

    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
