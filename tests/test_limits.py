# Limits tests: `procedure_timeout` dispatch enforcement (ADR 0015) — the
# TIMEOUT envelope, per-call batch behavior, the subscription exemption,
# the middleware exclusion, and the Limits constructor surface.

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from trpc_rs_py import Limits, Router, mount


def make_timeout_router() -> Router:
    router = Router()

    @router.query
    async def slow() -> str:
        await asyncio.sleep(5)
        return "slow-done"

    @router.query
    async def slowish() -> str:
        await asyncio.sleep(0.05)
        return "slowish-done"

    @router.query
    def fast() -> str:
        return "fast-ok"

    @router.mutation
    async def slow_mutation() -> str:
        await asyncio.sleep(5)
        return "mutation-done"

    return router


def _client(router: Router, **mount_kwargs: Any) -> TestClient:
    app = FastAPI()
    app.mount("/trpc", mount(router, **mount_kwargs))
    return TestClient(app)


def _scope(
    method: str,
    path: str,
    *,
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }


def _bodies(sent: list[dict[str, Any]]) -> list[bytes]:
    """The non-empty response body chunks, in order."""
    return [
        message["body"]
        for message in sent
        if message["type"] == "http.response.body" and message["body"]
    ]


def _receive_never_disconnect() -> Any:
    async def receive() -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    return receive


async def _collect(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the app until it returns, collecting sent messages."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, _receive_never_disconnect(), send)
    return sent


def test_procedure_timeout_defaults_to_none() -> None:
    assert Limits().procedure_timeout is None


def test_procedure_timeout_accepts_seconds_and_reads_back() -> None:
    assert Limits(procedure_timeout=0.05).procedure_timeout == 0.05


def test_procedure_timeout_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="procedure_timeout"):
        Limits(procedure_timeout=-1.0)
    with pytest.raises(ValueError, match="procedure_timeout"):
        Limits(procedure_timeout=float("nan"))
    with pytest.raises(ValueError, match="procedure_timeout"):
        Limits(procedure_timeout=float("inf"))


def test_timed_out_query_returns_timeout_envelope() -> None:
    client = _client(make_timeout_router(), limits=Limits(procedure_timeout=0.05))
    response = client.get("/trpc/slow", params={"input": "{}"})
    assert response.status_code == 408
    envelope = response.json()
    assert envelope["error"]["code"] == -32008
    assert envelope["error"]["data"]["code"] == "TIMEOUT"
    assert envelope["error"]["data"]["httpStatus"] == 408
    assert envelope["error"]["data"]["path"] == "slow"


def test_timed_out_mutation_returns_timeout_envelope() -> None:
    client = _client(make_timeout_router(), limits=Limits(procedure_timeout=0.05))
    response = client.post("/trpc/slow_mutation", json={})
    assert response.status_code == 408
    assert response.json()["error"]["data"]["code"] == "TIMEOUT"


def test_timeout_unset_keeps_handler_unbounded() -> None:
    client = _client(make_timeout_router())
    response = client.get("/trpc/slowish", params={"input": "{}"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "slowish-done"}}


def test_timeout_set_but_not_hit_returns_normally() -> None:
    client = _client(make_timeout_router(), limits=Limits(procedure_timeout=30.0))
    response = client.get("/trpc/fast", params={"input": "{}"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "fast-ok"}}


def test_timeout_applies_per_call_inside_batch() -> None:
    client = _client(make_timeout_router(), limits=Limits(procedure_timeout=0.05))
    response = client.get(
        "/trpc/slow,fast",
        params={"batch": "1", "input": json.dumps({"0": {}, "1": {}})},
    )
    slow_envelope, fast_envelope = response.json()
    assert slow_envelope["error"]["code"] == -32008
    assert slow_envelope["error"]["data"]["code"] == "TIMEOUT"
    assert fast_envelope == {"result": {"data": "fast-ok"}}


def test_subscription_streams_past_procedure_timeout() -> None:
    router = Router()

    @router.subscription
    async def ticks() -> AsyncIterator[int]:
        for index in range(3):
            await asyncio.sleep(0.05)
            yield index

    app = mount(router, limits=Limits(procedure_timeout=0.05))
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/ticks", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    # The generator ran ~0.15s, well past the 0.05s bound, and still
    # streamed every event plus the clean `return` frame.
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"
    assert bodies[1] == b"data: 0\n\n\n"
    assert bodies[2] == b"data: 1\n\n\n"
    assert bodies[3] == b"data: 2\n\n\n"
    assert bodies[4] == b"event: return\ndata: \n\n\n"


def test_middleware_time_is_not_counted_toward_timeout() -> None:
    router = Router()

    @router.query
    def quick() -> str:
        return "ok"

    async def slow_middleware(call: Any, next: Any) -> Any:
        await asyncio.sleep(0.1)
        return await next()

    client = _client(
        router,
        limits=Limits(procedure_timeout=0.05),
        middleware=[slow_middleware],
    )
    response = client.get("/trpc/quick", params={"input": "{}"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "ok"}}


def test_sync_handler_times_out_but_thread_runs_on() -> None:
    # A CPU-bound sync handler cannot be preempted mid-call (documented
    # caveat): the *wait* still times out and the worker thread finishes in
    # the background.
    finished = threading.Event()
    router = Router()

    @router.query
    def slow_sync() -> str:
        time.sleep(0.2)
        finished.set()
        return "sync-done"

    client = _client(router, limits=Limits(procedure_timeout=0.02))
    response = client.get("/trpc/slow_sync", params={"input": "{}"})
    assert response.status_code == 408
    assert response.json()["error"]["data"]["code"] == "TIMEOUT"
    assert finished.wait(timeout=5)
