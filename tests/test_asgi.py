# ASGI adapter tests: FastAPI and Starlette integration through the pure-ASGI
# adapter, error mapping, limits, and both mounting styles.

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from trpc_rs_py import Limits, Router, attach, mount
from trpc_rs_py.errors import TRPCError, TRPCErrorCode


def make_router() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query
    def echo(message: str) -> str:
        return f"echo: {message}"

    @router.query
    def boom() -> str:
        raise TRPCError("nope", code=TRPCErrorCode.NOT_FOUND)

    @router.query
    def explode() -> str:
        raise RuntimeError("secret internals")

    @router.mutation
    def create(title: str) -> dict:
        return {"title": title, "created": True}

    def sync_slow() -> str:
        time.sleep(0.01)
        return "sync-ok"

    router.query(name="sync_slow")(sync_slow)

    async def async_echo(message: str) -> str:
        return f"async: {message}"

    router.query(name="async_echo")(async_echo)

    return router


@pytest.fixture()
def router() -> Router:
    return make_router()


@pytest.fixture()
def dev_client(router: Router) -> TestClient:
    app = FastAPI()
    app.mount("/trpc", mount(router))
    return TestClient(app)


@pytest.fixture()
def prod_client(router: Router) -> TestClient:
    app = FastAPI()
    app.mount("/trpc", mount(router, production=True))
    return TestClient(app)


def query_input(**values: object) -> str:
    return json.dumps(values)


def test_fastapi_get_query_single(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/echo", params={"input": query_input(message="hi")})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "echo: hi"}}


def test_fastapi_post_mutation_single(dev_client: TestClient) -> None:
    response = dev_client.post("/trpc/create", json={"title": "hello"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": {"title": "hello", "created": True}}}


def test_fastapi_batched_get_in_order(dev_client: TestClient) -> None:
    batch_input = json.dumps({"0": None, "1": {"message": "hi"}})
    response = dev_client.get("/trpc/ping,echo", params={"batch": "1", "input": batch_input})
    assert response.status_code == 200
    assert response.json() == [
        {"result": {"data": "pong"}},
        {"result": {"data": "echo: hi"}},
    ]


def test_trpc_error_maps_to_status_and_envelope(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/boom")
    assert response.status_code == 404
    error = response.json()["error"]["json"]
    assert error["data"]["code"] == "NOT_FOUND"
    assert error["data"]["httpStatus"] == 404
    assert error["code"] == -32004
    assert error["message"] == "nope"


def test_unexpected_exception_leaks_repr_in_dev_mode(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/explode")
    assert response.status_code == 500
    error = response.json()["error"]["json"]
    assert "secret internals" in error["message"]
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"


def test_unexpected_exception_is_fixed_message_in_production(prod_client: TestClient) -> None:
    response = prod_client.get("/trpc/explode")
    assert response.status_code == 500
    error = response.json()["error"]["json"]
    assert error["message"] == "Internal server error"
    assert "secret internals" not in json.dumps(response.json())


def test_wrong_input_shape_is_bad_request(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/echo", params={"input": query_input(wrong=1)})
    assert response.status_code == 400
    error = response.json()["error"]["json"]
    assert error["data"]["code"] == "BAD_REQUEST"
    assert error["message"] == "Invalid input for procedure echo"


def test_missing_procedure_is_not_found(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["json"]["data"]["code"] == "NOT_FOUND"


def test_oversized_body_is_413_without_reading(dev_client: TestClient) -> None:
    app = FastAPI()
    app.mount("/trpc", mount(make_router(), limits=Limits(max_body_size=8)))
    client = TestClient(app)
    response = client.post(
        "/trpc/create",
        content=b'{"title":"' + b"x" * 64 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["json"]["data"]["code"] == "PAYLOAD_TOO_LARGE"


def test_batch_size_limit_is_413() -> None:
    router = make_router()
    app = FastAPI()
    app.mount("/trpc", mount(router, limits=Limits(max_batch_size=1)))
    client = TestClient(app)
    batch_input = json.dumps({"0": None, "1": None})
    response = client.get("/trpc/ping,ping", params={"batch": "1", "input": batch_input})
    assert response.status_code == 413
    assert response.json()["error"]["json"]["data"]["code"] == "PAYLOAD_TOO_LARGE"


def test_non_json_content_type_is_rejected(dev_client: TestClient) -> None:
    response = dev_client.post(
        "/trpc/create", content=b'{"title":"x"}', headers={"content-type": "text/plain"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["json"]["data"]["code"] == "BAD_REQUEST"


def test_json_charset_parameters_are_lenient(dev_client: TestClient) -> None:
    response = dev_client.post(
        "/trpc/create",
        content=b'{"title":"x"}',
        headers={"content-type": "application/json; charset=utf-8"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["data"]["title"] == "x"


def test_unsupported_http_method_is_method_not_supported(dev_client: TestClient) -> None:
    response = dev_client.put("/trpc/ping")
    assert response.status_code == 405
    assert response.json()["error"]["json"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_mutation_over_get_is_method_not_supported(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/create", params={"input": query_input(title="x")})
    assert response.status_code == 405
    assert response.json()["error"]["json"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_sync_handler_runs_via_to_thread(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/sync_slow")
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "sync-ok"}}


def test_async_handler_is_awaited_directly(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/async_echo", params={"input": query_input(message="yo")})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "async: yo"}}


def test_starlette_mount_serves_router(router: Router) -> None:
    async def health(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/health", health)])
    app.mount("/trpc", mount(router))
    client = TestClient(app)
    response = client.get("/trpc/ping")
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "pong"}}
    # Starlette routes outside the mount still work.
    assert client.get("/health").text == "ok"


def test_mount_handles_stripped_prefix_scopes(router: Router) -> None:
    # Older Starlette/FastAPI versions strip the mount prefix, so the sub-app
    # sees only "/echo"; the adapter must treat that as the procedure path.
    inner = mount(router, path="/trpc")

    async def legacy_mount(scope: object, receive: object, send: object) -> None:
        mutable_scope = dict(scope)  # type: ignore[arg-type]
        mutable_scope["path"] = mutable_scope["path"][len("/trpc") :]
        await inner(mutable_scope, receive, send)  # type: ignore[arg-type]

    client = TestClient(legacy_mount)
    response = client.get("/trpc/echo", params={"input": query_input(message="hi")})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "echo: hi"}}


def test_attach_middleware_serves_and_passes_through(router: Router) -> None:
    async def health(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    starlette_app = Starlette(routes=[Route("/health", health)])
    app = attach(starlette_app, router, path="/api/trpc")
    client = TestClient(app)

    trpc_response = client.get("/api/trpc/ping")
    assert trpc_response.status_code == 200
    assert trpc_response.json() == {"result": {"data": "pong"}}

    passthrough_response = client.get("/health")
    assert passthrough_response.status_code == 200
    assert passthrough_response.text == "ok"


def test_attach_mutation_over_post(router: Router) -> None:
    async def health(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    starlette_app = Starlette(routes=[Route("/health", health)])
    app = attach(starlette_app, router, path="/api/trpc")
    client = TestClient(app)
    response = client.post("/api/trpc/create", json={"title": "hi"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": {"title": "hi", "created": True}}}


def test_mount_accepts_lifespan(router: Router) -> None:
    with TestClient(mount(router)) as client:
        response = client.get("/trpc/ping")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Raw-ASGI helpers for protocol-level behavior the TestClient cannot express
# (missing headers, mid-body disconnects, websocket scopes).
# ---------------------------------------------------------------------------


def _raw_scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
    }


def _raw_receive(messages: list[dict[str, Any]]) -> Callable[[], Any]:
    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return receive


def _raw_send(sent: list[dict[str, Any]]) -> Callable[[dict[str, Any]], Any]:
    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    return send


def test_post_without_content_type_is_bad_request(router: Router) -> None:
    app = mount(router)
    sent: list[dict[str, Any]] = []
    scope = _raw_scope("POST", "/trpc/create", [(b"content-length", b"16")])
    messages = [{"type": "http.request", "body": b'{"title":"x"}', "more_body": False}]
    asyncio.run(app(scope, _raw_receive(messages), _raw_send(sent)))
    assert sent[0]["status"] == 400
    body = json.loads(sent[1]["body"])
    assert body["error"]["json"]["data"]["code"] == "BAD_REQUEST"


def test_disconnect_mid_body_never_invokes_handler() -> None:
    invoked: list[str] = []
    router = Router()

    @router.mutation
    def create(title: str) -> dict:
        invoked.append(title)
        return {"title": title}

    app = mount(router)
    sent: list[dict[str, Any]] = []
    scope = _raw_scope(
        "POST", "/trpc/create", [(b"content-type", b"application/json"), (b"content-length", b"99")]
    )
    messages = [
        {"type": "http.request", "body": b'{"title":', "more_body": True},
        {"type": "http.disconnect"},
    ]
    asyncio.run(app(scope, _raw_receive(messages), _raw_send(sent)))
    # No handler dispatch, no partial-body response: the connection is gone.
    assert invoked == []
    assert sent == []


def test_mount_closes_websocket_connections_with_policy_code(router: Router) -> None:
    app = mount(router)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "path": "/trpc/ping",
        "headers": [],
    }
    asyncio.run(app(scope, receive, _raw_send(sent)))
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_handler_type_error_in_production_is_internal_server_error() -> None:
    router = Router()

    @router.query
    def sync_breaks(count: int) -> str:
        raise TypeError("internal failure: leaked handler detail")

    async def async_breaks(count: int) -> str:
        raise TypeError("internal failure: leaked handler detail")

    router.query(name="async_breaks")(async_breaks)

    app = mount(router, production=True)
    client = TestClient(app)
    for path in ("/trpc/sync_breaks", "/trpc/async_breaks"):
        response = client.get(path, params={"input": query_input(count=1)})
        assert response.status_code == 500
        error = response.json()["error"]["json"]
        assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"
        assert error["message"] == "Internal server error"
        assert "leaked handler detail" not in json.dumps(response.json())


def test_handler_type_error_in_dev_mode_leaks_repr() -> None:
    router = Router()

    @router.query
    def breaks(count: int) -> str:
        raise TypeError("internal failure: leaked handler detail")

    app = mount(router)
    client = TestClient(app)
    response = client.get("/trpc/breaks", params={"input": query_input(count=1)})
    assert response.status_code == 500
    error = response.json()["error"]["json"]
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "internal failure: leaked handler detail" in error["message"]


def test_trpc_error_internal_server_error_message_is_redacted_in_production() -> None:
    router = Router()

    @router.query
    def internal() -> str:
        raise TRPCError(
            "select * from secrets where user = 1", code=TRPCErrorCode.INTERNAL_SERVER_ERROR
        )

    app = mount(router, production=True)
    client = TestClient(app)
    response = client.get("/trpc/internal")
    assert response.status_code == 500
    error = response.json()["error"]["json"]
    assert error["message"] == "Internal server error"
    assert "select * from secrets" not in json.dumps(response.json())


def test_trpc_error_client_facing_codes_pass_through_in_production() -> None:
    router = Router()

    @router.query
    def missing() -> str:
        raise TRPCError("User not found", code=TRPCErrorCode.NOT_FOUND)

    app = mount(router, production=True)
    client = TestClient(app)
    response = client.get("/trpc/missing")
    assert response.status_code == 404
    error = response.json()["error"]["json"]
    assert error["data"]["code"] == "NOT_FOUND"
    assert error["message"] == "User not found"


def test_trpc_error_messages_all_pass_through_in_dev_mode() -> None:
    router = Router()

    @router.query
    def internal() -> str:
        raise TRPCError(
            "select * from secrets where user = 1", code=TRPCErrorCode.INTERNAL_SERVER_ERROR
        )

    @router.query
    def missing() -> str:
        raise TRPCError("User not found", code=TRPCErrorCode.NOT_FOUND)

    app = mount(router, production=False)
    client = TestClient(app)
    assert client.get("/trpc/internal").json()["error"]["json"]["message"] == (
        "select * from secrets where user = 1"
    )
    assert client.get("/trpc/missing").json()["error"]["json"]["message"] == "User not found"
