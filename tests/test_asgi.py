# ASGI adapter tests: FastAPI and Starlette integration through the pure-ASGI
# adapter, error mapping, limits, and both mounting styles.

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, field_serializer
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from trpc_rs_py import Limits, Router, attach, mount
from trpc_rs_py._core import build_response
from trpc_rs_py.asgi import _run_call
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


def test_pydantic_input_and_output_validation_integration() -> None:
    class UserInput(BaseModel):
        name: str
        age: int

    class UserOutput(BaseModel):
        greeting: str

    router = Router()

    @router.query
    def greet(user: UserInput) -> UserOutput:
        return UserOutput(greeting=f"Hello {user.name}, {user.age}")

    app = FastAPI()
    app.mount("/trpc", mount(router, production=True))
    client = TestClient(app)

    valid = client.get(
        "/trpc/greet", params={"input": query_input(user={"name": "Ada", "age": "37"})}
    )
    assert valid.status_code == 200
    assert valid.json() == {"result": {"data": {"greeting": "Hello Ada, 37"}}}

    missing = client.get("/trpc/greet", params={"input": query_input(user={"name": "Ada"})})
    assert missing.status_code == 400
    assert missing.json()["error"]["message"] == "Invalid input: user: Field required"

    extra = client.get(
        "/trpc/greet",
        params={"input": query_input(user={"name": "Ada", "age": 37}, extra=1)},
    )
    assert extra.status_code == 400
    assert extra.json()["error"]["message"] == (
        "Invalid input: input: Extra inputs are not permitted"
    )

    wrong = client.get("/trpc/greet", params={"input": query_input(user={"name": 1, "age": 37})})
    assert wrong.status_code == 400

    @router.query
    def wrong_output() -> UserOutput:
        return {"not_greeting": "bad"}  # type: ignore[return-value]

    # The adapter intentionally snapshots procedure validators at construction.
    # Construct a new app to include the newly registered procedure.
    app = FastAPI()
    app.mount("/trpc", mount(router, production=True))
    response = TestClient(app).get("/trpc/wrong_output")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["message"] == "Internal server error"
    assert "not_greeting" not in json.dumps(response.json())


def test_serializer_failure_is_normalized_in_development_and_production() -> None:
    class SerializedOutput(BaseModel):
        value: str

        @field_serializer("value")
        def serialize_value(self, value: str) -> str:
            """Raise a private serializer error for adapter error handling."""
            raise ValueError("secret serializer token")

    router = Router()

    @router.query
    def serialized() -> SerializedOutput:
        return SerializedOutput(value="private")

    dev_app = FastAPI()
    dev_app.mount("/trpc", mount(router))
    prod_app = FastAPI()
    prod_app.mount("/trpc", mount(router, production=True))

    for app, expected_message in (
        (dev_app, "Invalid output from procedure"),
        (prod_app, "Internal server error"),
    ):
        response = TestClient(app).get("/trpc/serialized")
        assert response.status_code == 500
        error = response.json()["error"]
        assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"
        assert error["message"] == expected_message
        assert "secret serializer token" not in json.dumps(response.json())


def test_run_call_normalizes_arbitrary_output_validator_exceptions() -> None:
    router = Router()

    @router.query
    def output_failure() -> str:
        return "ok"

    def validator(value: Any) -> Any:
        raise RuntimeError("secret custom validator detail")

    procedure = router.procedures["output_failure"]
    for production, expected_message in (
        (False, "Invalid output from procedure"),
        (True, "Internal server error"),
    ):
        result = asyncio.run(_run_call(procedure, None, production, (None, validator)))
        status, body = build_response([result], False)
        error = json.loads(body)["error"]
        assert status == 500
        assert error["message"] == expected_message
        assert "secret custom validator detail" not in body


def test_batched_validation_returns_ordered_individual_envelopes() -> None:
    router = Router()

    @router.query
    def double(value: int) -> int:
        return value * 2

    app = FastAPI()
    app.mount("/trpc", mount(router))
    client = TestClient(app)
    response = client.get(
        "/trpc/double,double",
        params={"batch": "1", "input": json.dumps({"0": {"value": 2}, "1": {"bad": "secret"}})},
    )
    assert response.status_code == 400
    result = response.json()
    assert result[0] == {"result": {"data": 4}}
    assert result[1]["error"]["data"]["code"] == "BAD_REQUEST"
    assert "secret" not in json.dumps(result)


def test_optional_only_typed_procedure_uses_default_for_null_input() -> None:
    router = Router()

    @router.query
    def greeting(prefix: str = "hello") -> str:
        return prefix

    response = TestClient(mount(router)).get("/trpc/greeting", params={"input": "null"})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "hello"}}


def test_positional_only_typed_procedure_validates_and_invokes_positionally() -> None:
    router = Router()

    @router.query
    def positional(value: int, /) -> int:
        return value * 2

    client = TestClient(mount(router))
    valid = client.get("/trpc/positional", params={"input": query_input(value="6")})
    assert valid.status_code == 200
    assert valid.json() == {"result": {"data": 12}}

    invalid = client.get("/trpc/positional", params={"input": query_input(value="bad-secret")})
    assert invalid.status_code == 400
    error = invalid.json()["error"]
    assert error["data"]["code"] == "BAD_REQUEST"
    assert error["message"] == "Invalid input: value: Invalid integer"
    assert "bad-secret" not in invalid.text


def test_missing_required_unannotated_parameter_is_bad_request() -> None:
    router = Router()

    def required(value: int, missing) -> int:
        return value + missing

    router.query(name="required")(required)
    response = TestClient(mount(router)).get(
        "/trpc/required", params={"input": query_input(value=1)}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["data"]["code"] == "BAD_REQUEST"
    assert error["message"] == "Invalid input: missing: Field required"


def test_varargs_only_procedure_accepts_scalar_list_and_null() -> None:
    router = Router()

    @router.query
    def collect(*values: Any) -> list[Any]:
        return list(values)

    client = TestClient(mount(router))
    cases = (
        (json.dumps(5), [5]),
        (json.dumps([1, "two"]), [1, "two"]),
        ("null", []),
    )
    for input_value, expected in cases:
        response = client.get("/trpc/collect", params={"input": input_value})
        assert response.status_code == 200
        assert response.json() == {"result": {"data": expected}}


def test_mixed_parameter_kinds_invoke_with_validated_args_and_kwargs() -> None:
    router = Router()

    @router.query
    def mixed(first: int, /, second: str, *, enabled: bool) -> str:
        return f"{first}:{second}:{enabled}"

    response = TestClient(mount(router)).get(
        "/trpc/mixed",
        params={"input": query_input(first="2", second="ok", enabled=True)},
    )
    assert response.status_code == 200
    assert response.json() == {"result": {"data": "2:ok:True"}}


def test_unannotated_procedure_keeps_legacy_invocation() -> None:
    router = Router()

    def legacy(value):
        return value

    router.query(name="legacy")(legacy)
    app = FastAPI()
    app.mount("/trpc", mount(router))
    response = TestClient(app).get("/trpc/legacy", params={"input": json.dumps(3)})
    assert response.status_code == 200
    assert response.json() == {"result": {"data": 3}}


def test_trpc_error_maps_to_status_and_envelope(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/boom")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["data"]["code"] == "NOT_FOUND"
    assert error["data"]["httpStatus"] == 404
    assert error["code"] == -32004
    assert error["message"] == "nope"


def test_unexpected_exception_leaks_repr_in_dev_mode(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/explode")
    assert response.status_code == 500
    error = response.json()["error"]
    assert "secret internals" in error["message"]
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"


def test_unexpected_exception_is_fixed_message_in_production(prod_client: TestClient) -> None:
    response = prod_client.get("/trpc/explode")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["message"] == "Internal server error"
    assert "secret internals" not in json.dumps(response.json())


def test_wrong_input_shape_is_bad_request(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/echo", params={"input": query_input(wrong=1)})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["data"]["code"] == "BAD_REQUEST"
    assert error["message"] == "Invalid input: input: Extra inputs are not permitted"


def test_missing_procedure_is_not_found(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["data"]["code"] == "NOT_FOUND"


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
    assert response.json()["error"]["data"]["code"] == "PAYLOAD_TOO_LARGE"


def test_batch_size_limit_is_413() -> None:
    router = make_router()
    app = FastAPI()
    app.mount("/trpc", mount(router, limits=Limits(max_batch_size=1)))
    client = TestClient(app)
    batch_input = json.dumps({"0": None, "1": None})
    response = client.get("/trpc/ping,ping", params={"batch": "1", "input": batch_input})
    assert response.status_code == 413
    assert response.json()["error"]["data"]["code"] == "PAYLOAD_TOO_LARGE"


def test_non_json_content_type_is_rejected(dev_client: TestClient) -> None:
    response = dev_client.post(
        "/trpc/create", content=b'{"title":"x"}', headers={"content-type": "text/plain"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["data"]["code"] == "BAD_REQUEST"


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
    assert response.json()["error"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_mutation_over_get_is_method_not_supported(dev_client: TestClient) -> None:
    response = dev_client.get("/trpc/create", params={"input": query_input(title="x")})
    assert response.status_code == 405
    assert response.json()["error"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


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
    assert body["error"]["data"]["code"] == "BAD_REQUEST"


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
        error = response.json()["error"]
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
    error = response.json()["error"]
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
    error = response.json()["error"]
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
    error = response.json()["error"]
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
    assert client.get("/trpc/internal").json()["error"]["message"] == (
        "select * from secrets where user = 1"
    )
    assert client.get("/trpc/missing").json()["error"]["message"] == "User not found"
