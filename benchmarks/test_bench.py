"""Adapter-overhead micro-benchmarks (pytest-benchmark; MISSION.md section 10).

Run via `benchmarks/run.py`, or directly:
    uv run pytest benchmarks/test_bench.py --benchmark-only
"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from trpc_rs_py import Router, generate_types, mount


def _router() -> Router:
    router = Router()

    @router.query
    async def greet(name):
        return f"Hello {name}"

    return router


def test_trpc_query_overhead(benchmark) -> None:
    app = Starlette(routes=[Mount("/trpc", app=mount(_router(), production=True))])
    with TestClient(app) as client:
        response = benchmark(client.get, "/trpc/greet", params={"input": '{"name":"ada"}'})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "Hello ada"}}


def test_plain_json_query_baseline(benchmark) -> None:
    async def endpoint(request):
        name = json.loads(request.query_params["input"])["name"]
        return JSONResponse({"result": {"data": f"Hello {name}"}})

    app = Starlette(routes=[Route("/plain/greet", endpoint)])
    with TestClient(app) as client:
        response = benchmark(client.get, "/plain/greet", params={"input": '{"name":"ada"}'})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "Hello ada"}}


@pytest.mark.parametrize("count", [10, 50, 200], ids=["p10", "p50", "p200"])
def test_codegen_types(benchmark, count: int) -> None:
    router = Router()

    def handler(name: str) -> str:
        return f"Hello {name}"

    for index in range(count):
        router.query(name=f"proc{index}")(handler)
    output = benchmark(generate_types, router)
    assert f"proc{count - 1}" in output