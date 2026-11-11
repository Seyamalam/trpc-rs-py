"""ASGI apps for the HTTP load suite (MISSION.md section 10).

The tRPC app, the plain-JSON baseline and the Node reference serve the same
procedures with the same wire work. Handlers are deliberately unannotated:
the Node reference performs no input validation, so the comparison isolates
protocol and adapter overhead rather than validation cost.

Standalone-server entry: ``uv run python benchmarks/apps.py <port>`` serves
the same router through ``trpc_rs_py.serve.serve`` (production mode, mounted
at ``/trpc``), matching the ``trpc-fastapi`` workloads in ``run.py``. The
harness spawns this as the ``trpc-standalone`` server.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from trpc_rs_py import Router, mount

router = Router()


@router.query
async def greet(name):
    return f"Hello {name}"


@router.mutation
async def echo(message):
    return message


trpc_app = FastAPI()
trpc_app.mount("/trpc", mount(router, production=True))

baseline_app = FastAPI()


@baseline_app.get("/plain/greet")
async def plain_greet(input: str) -> JSONResponse:
    name = json.loads(input)["name"]
    return JSONResponse({"result": {"data": f"Hello {name}"}})


@baseline_app.get("/plain/batch")
async def plain_batch(input: str) -> JSONResponse:
    inputs = json.loads(input)
    return JSONResponse(
        [{"result": {"data": f"Hello {item['name']}"}} for item in inputs.values()]
    )


@baseline_app.post("/plain/echo")
async def plain_echo(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"result": {"data": payload["message"]}})


def serve_standalone(port: int) -> int:
    """Run the standalone Rust server for the ``trpc-standalone`` entry."""
    from trpc_rs_py.serve import serve

    return serve(router, production=True, port=port)


if __name__ == "__main__":
    raise SystemExit(serve_standalone(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
