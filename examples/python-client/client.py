"""client.py: call the running python-client server over real HTTP.

Exercises `trpc_rs_py.client.create_client` (query / mutation / batch /
typed-error round-trip) and the generated `AppRouterClient` stub from
`generated_client.py`. Run it after starting `app.py`:

    cd examples/python-client && uv run --project ../.. python client.py
    # or: uv run --project ../.. python client.py http://127.0.0.1:9000/trpc

Every check prints `client: ok`/`client: FAIL`; the exit code is non-zero if
any check failed, which is how `smoke.sh` drives it.
"""

from __future__ import annotations

import sys
from typing import Any

from generated_client import AppRouterClient

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
        # Query and mutation unwrap `result.data` on the wire envelope.
        check(
            "greeting query",
            "Hello, Ada!",
            client.query("greeting", {"name": "Ada"}),
        )
        check(
            "create_post mutation",
            {"id": 1, "title": "Hello tRPC", "priority": 2},
            client.mutation("create_post", {"title": "Hello tRPC", "priority": 2}),
        )
        check(
            "post_by_id query",
            {"id": 1, "title": "Hello tRPC", "priority": 2},
            client.query("post_by_id", {"post_id": 1}),
        )

        # One batched GET returns ordered per-call results; a failing call
        # yields a TrpcClientError instance instead of raising.
        results = client.batch(
            [
                {"path": "greeting", "input": {"name": "Bo"}},
                {"path": "post_by_id", "input": {"post_id": 1}},
                {"path": "post_by_id", "input": {"post_id": 99}},
            ]
        )
        check("batch greeting", "Hello, Bo!", results[0])
        check(
            "batch post_by_id",
            {"id": 1, "title": "Hello tRPC", "priority": 2},
            results[1],
        )
        batch_error = results[2]
        check(
            "batch partial failure is a TrpcClientError",
            True,
            isinstance(batch_error, TrpcClientError) and batch_error.code == "NOT_FOUND",
        )

        # The typed error round-trips: code, http_status and the declared
        # data payload all surface on TrpcClientError.
        try:
            client.query("post_by_id", {"post_id": 99})
        except TrpcClientError as error:
            check("typed error code", "NOT_FOUND", error.code)
            check("typed error message", "Post 99 not found", error.message)
            check("typed error http status", 404, error.http_status)
            check(
                "typed error data payload",
                99,
                error.data.get("post_id") if error.data else None,
            )
        else:
            check("typed error raised", "TrpcClientError", "no error")

        # The generated AppRouterClient stub delegates to the same client
        # with method names derived from the procedure paths.
        stub = AppRouterClient(client)
        check("stub greeting", "Hello, Grace!", stub.greeting("Grace"))
        check(
            "stub create_post",
            {"id": 2, "title": "From stub", "priority": 5},
            stub.create_post("From stub", priority=5),
        )
        check(
            "stub list_posts",
            [
                {"id": 1, "title": "Hello tRPC", "priority": 2},
                {"id": 2, "title": "From stub", "priority": 5},
            ],
            stub.list_posts(),
        )

    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
