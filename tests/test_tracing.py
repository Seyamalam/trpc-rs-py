# tracing_middleware tests (ADR 0011): an OpenTelemetry span per call with
# path/kind attributes, exception recording, ERROR status, and the
# optional-dependency hard-fail. Tracers are stubbed — only the
# opentelemetry-api import is real.

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest
from opentelemetry.trace import StatusCode
from starlette.testclient import TestClient

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount
from trpc_rs_py.middleware import Call, run_middleware, tracing_middleware
from trpc_rs_py.router import ProcedureKind


class _StubSpan:
    """Recording stand-in for an OpenTelemetry span (context manager)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, Any] = {}
        self.exceptions: list[BaseException] = []
        self.status: Any = None
        self.ended = False

    def __enter__(self) -> _StubSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        self.ended = True

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, error: BaseException) -> None:
        self.exceptions.append(error)

    def set_status(self, status: Any) -> None:
        self.status = status


class _StubTracer:
    """Duck-typed tracer recording every span `start_as_current_span` opens."""

    def __init__(self) -> None:
        self.spans: list[_StubSpan] = []

    def start_as_current_span(self, name: str) -> _StubSpan:
        span = _StubSpan(name)
        self.spans.append(span)
        return span


def _call(path: str = "ping", kind: ProcedureKind = ProcedureKind.QUERY) -> Call:
    return Call(path=path, kind=kind, input=None, ctx=None)


class TestSpanLifecycle:
    def test_span_opens_and_closes_with_attributes(self) -> None:
        tracer = _StubTracer()
        middleware = tracing_middleware(tracer)

        async def terminal(call: Call) -> Any:
            return "ok"

        result = asyncio.run(run_middleware((middleware,), _call(), terminal))
        assert result == "ok"
        assert len(tracer.spans) == 1
        span = tracer.spans[0]
        assert span.name == "trpc.ping"
        assert span.attributes == {"trpc.path": "ping", "trpc.kind": "query"}
        assert span.ended
        assert span.status is None
        assert span.exceptions == []

    def test_kind_attribute_reflects_procedure_kind(self) -> None:
        tracer = _StubTracer()
        middleware = tracing_middleware(tracer)

        async def terminal(call: Call) -> Any:
            return "ok"

        asyncio.run(run_middleware((middleware,), _call(kind=ProcedureKind.MUTATION), terminal))
        assert tracer.spans[0].attributes["trpc.kind"] == "mutation"

    def test_success_leaves_status_unset(self) -> None:
        tracer = _StubTracer()
        middleware = tracing_middleware(tracer)

        async def terminal(call: Call) -> Any:
            return await asyncio.sleep(0, result="ok")

        asyncio.run(run_middleware((middleware,), _call(), terminal))
        span = tracer.spans[0]
        assert span.status is None


class TestSpanErrors:
    def test_exception_records_span_marks_error_and_reraises(self) -> None:
        tracer = _StubTracer()
        middleware = tracing_middleware(tracer)

        async def terminal(call: Call) -> Any:
            raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

        with pytest.raises(TRPCError) as excinfo:
            asyncio.run(run_middleware((middleware,), _call(), terminal))
        assert excinfo.value.code == TRPCErrorCode.NOT_FOUND
        span = tracer.spans[0]
        assert span.exceptions == [excinfo.value]
        assert span.status is not None
        assert span.status.status_code == StatusCode.ERROR
        assert span.ended

    def test_unexpected_exception_also_marks_error(self) -> None:
        tracer = _StubTracer()
        middleware = tracing_middleware(tracer)

        async def terminal(call: Call) -> Any:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(run_middleware((middleware,), _call(), terminal))
        assert tracer.spans[0].status.status_code == StatusCode.ERROR


class TestMountDispatch:
    def test_span_per_call_over_real_mount(self) -> None:
        tracer = _StubTracer()
        router = Router(middleware=[tracing_middleware(tracer)])

        @router.query
        def hello() -> str:
            return "hi"

        @router.query
        def boom() -> str:
            raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

        client = TestClient(mount(router))
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"
        response = client.get("/trpc/boom")
        assert response.status_code == 404
        assert response.json()["error"]["data"]["code"] == "NOT_FOUND"

        assert [span.name for span in tracer.spans] == ["trpc.hello", "trpc.boom"]
        assert tracer.spans[0].attributes == {
            "trpc.path": "hello",
            "trpc.kind": "query",
        }
        assert tracer.spans[0].status is None
        assert tracer.spans[1].status.status_code == StatusCode.ERROR
        assert len(tracer.spans[1].exceptions) == 1

    def test_default_tracer_runs_end_to_end(self) -> None:
        router = Router(middleware=[tracing_middleware()])

        @router.query
        def hello() -> str:
            return "hi"

        client = TestClient(mount(router))
        assert client.get("/trpc/hello").json()["result"]["data"] == "hi"


class TestOptionalDependency:
    def test_missing_opentelemetry_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        with pytest.raises(RuntimeError, match="opentelemetry-api") as excinfo:
            tracing_middleware()
        assert isinstance(excinfo.value.__cause__, ImportError)
