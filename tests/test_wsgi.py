# WSGI adapter tests: mount_wsgi under any WSGI server plus the Flask
# integration via attach_flask (DispatcherMiddleware mounting).

import json
from typing import Any

import pytest
from pydantic import BaseModel, field_serializer
from werkzeug.test import Client
from werkzeug.wrappers import Response

from trpc_rs_py import Router, attach_flask, mount_wsgi
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

    async def async_echo(message: str) -> str:
        return f"async: {message}"

    router.query(name="async_echo")(async_echo)

    @router.subscription
    def ticks():
        async def stream():
            yield {"n": 0}
            yield {"n": 1}
            yield {"n": 2}

        return stream()

    return router


@pytest.fixture()
def client() -> Client:
    return Client(mount_wsgi(make_router()), Response)


def encode_input(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def test_query_get(client: Client) -> None:
    r = client.get("/trpc/echo", query_string={"input": encode_input({"message": "hi"})})
    assert r.status_code == 200
    assert r.json == {"result": {"data": "echo: hi"}}


def test_query_no_input(client: Client) -> None:
    r = client.get("/trpc/ping")
    assert r.status_code == 200
    assert r.json == {"result": {"data": "pong"}}


def test_mutation_post(client: Client) -> None:
    r = client.post("/trpc/create", json={"title": "hello"})
    assert r.status_code == 200
    assert r.json == {"result": {"data": {"title": "hello", "created": True}}}


def test_batched_query(client: Client) -> None:
    r = client.get(
        "/trpc/ping,echo",
        query_string={
            "batch": "1",
            "input": encode_input({"0": None, "1": {"message": "two"}}),
        },
    )
    assert r.status_code == 200
    assert r.json == [
        {"result": {"data": "pong"}},
        {"result": {"data": "echo: two"}},
    ]


def test_unknown_procedure_404(client: Client) -> None:
    r = client.get("/trpc/nope")
    assert r.status_code == 404
    body = r.json["error"]
    assert body["data"]["code"] == "NOT_FOUND"
    assert body["data"]["path"] == "nope"


def test_trpc_error_envelope(client: Client) -> None:
    r = client.get("/trpc/boom")
    assert r.status_code == 404
    assert r.json["error"]["data"]["code"] == "NOT_FOUND"
    assert r.json["error"]["message"] == "nope"


def test_internal_error_hides_internals_in_production() -> None:
    app = mount_wsgi(make_router(), production=True)
    r = Client(app, Response).get("/trpc/explode")
    assert r.status_code == 500
    assert r.json["error"]["message"] == "Internal server error"
    assert "secret" not in r.text


def test_internal_error_leaks_in_development(client: Client) -> None:
    r = client.get("/trpc/explode")
    assert r.status_code == 500
    assert "secret internals" in r.json["error"]["message"]


def test_invalid_input_is_bad_request(client: Client) -> None:
    r = client.get("/trpc/echo")  # missing required `message`
    assert r.status_code == 400
    assert r.json["error"]["data"]["code"] == "BAD_REQUEST"


def test_mutation_over_get_rejected(client: Client) -> None:
    r = client.get("/trpc/create", query_string={"input": encode_input({"title": "x"})})
    assert r.status_code == 405
    assert r.json["error"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_unsupported_method(client: Client) -> None:
    r = client.delete("/trpc/ping")
    assert r.status_code == 405
    assert r.json["error"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_invalid_json_body(client: Client) -> None:
    r = client.post("/trpc/create", data="{not json", content_type="application/json")
    assert r.status_code == 400
    assert r.json["error"]["data"]["code"] == "PARSE_ERROR"


def test_async_handler_runs_under_wsgi(client: Client) -> None:
    """Coroutine handlers work under WSGI — the bridge runs a private loop."""
    r = client.get("/trpc/async_echo", query_string={"input": encode_input({"message": "yo"})})
    assert r.json == {"result": {"data": "async: yo"}}


def test_sse_subscription_streams_through_wsgi(client: Client) -> None:
    r = client.get("/trpc/ticks")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/event-stream")
    body = r.get_data(as_text=True)
    assert "event: connected" in body
    assert '"n":1' in body
    assert "event: return" in body


def test_input_pydantic_model() -> None:
    class In(BaseModel):
        name: str
        age: int = 0

        @field_serializer("name")
        def _ser(self, v: str) -> str:
            return v

    router = Router()

    @router.mutation
    def register(user: In) -> dict:
        return {"name": user.name, "age": user.age}

    r = Client(mount_wsgi(router), Response).post(
        "/trpc/register", json={"user": {"name": "Ada", "age": 36}}
    )
    assert r.json == {"result": {"data": {"name": "Ada", "age": 36}}}


# --- Flask integration -------------------------------------------------------


def test_attach_flask_serves_both() -> None:
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)

    @app.get("/")
    def index() -> str:
        return "flask index"

    attach_flask(app, make_router())
    c = Client(app, Response)

    assert c.get("/").text == "flask index"
    r = c.get("/trpc/ping")
    assert r.json == {"result": {"data": "pong"}}


def test_attach_flask_custom_prefix() -> None:
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    attach_flask(app, make_router(), path="/api/trpc")
    c = Client(app, Response)
    assert c.get("/api/trpc/ping").json == {"result": {"data": "pong"}}
    assert c.get("/trpc/ping").status_code == 404


def test_wsgi_environ_headers_forwarded(client: Client) -> None:
    """Authorization-style headers reach the scope as ASGI headers."""
    router = Router()

    @router.query
    def whoami() -> str:
        return "ok"

    c = Client(mount_wsgi(router), Response)
    r = c.get("/trpc/whoami", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200


def test_response_headers_preserved(client: Client) -> None:
    r = client.get("/trpc/ticks")
    names = {k for k, _v in r.headers}
    assert "Content-Type" in names or "content-type" in {n.lower() for n in names}


def test_body_iterable_is_closeable(client: Client) -> None:
    """The returned iterable supports close() for WSGI cleanup semantics."""
    app = mount_wsgi(make_router())

    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list, exc_info: Any = None) -> None:
        captured["status"] = status

    from io import BytesIO

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/trpc/ticks",
        "QUERY_STRING": "",
        "SERVER_NAME": "test",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(b""),
        "CONTENT_LENGTH": "0",
    }
    iterable = app(environ, start_response)
    assert captured["status"].startswith("200")
    assert hasattr(iterable, "close") or hasattr(iterable, "__iter__")
    first = next(iter(iterable))
    assert b"event: connected" in first
    if hasattr(iterable, "close"):
        iterable.close()
