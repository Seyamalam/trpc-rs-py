"""End-to-end tests for the standalone Rust server (trpc_rs_py.serve).

`_core.serve` is driven over real sockets on a test-owned asyncio loop
running in a daemon thread; stopping the loop is the deterministic shutdown
hook (mirroring the SIGINT path through the same graceful-stop machinery).
"""

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

import trpc_rs_py.serve as serve_module
from trpc_rs_py import _core, mount
from trpc_rs_py.cors import cors
from trpc_rs_py.errors import TRPCError, TRPCErrorCode
from trpc_rs_py.router import Router
from trpc_rs_py.serve import serve
from trpc_rs_py.streaming import tracked


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _RunningServer:
    """A live `_core.serve` instance on a test-owned asyncio loop."""

    def __init__(self, app: Any, **kwargs: Any) -> None:
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._loop_thread.start()
        self._thread = threading.Thread(
            target=_core.serve,
            args=(app, self.loop, "127.0.0.1", self.port),
            kwargs=kwargs,
            daemon=True,
        )
        self._thread.start()
        self._wait_ready()

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                    return
            except OSError as error:
                if not self._thread.is_alive():
                    raise RuntimeError("_core.serve thread exited before binding") from error
                time.sleep(0.02)
        raise RuntimeError("server did not start listening")

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)
        self._loop_thread.join(timeout=10)
        self.loop.close()
        assert not self._thread.is_alive(), "_core.serve did not return after loop stop"


@pytest.fixture
def running() -> Iterator[type[_RunningServer]]:
    servers: list[_RunningServer] = []
    yield lambda app, **kw: servers.append(_RunningServer(app, **kw)) or servers[-1]
    for server in servers:
        server.stop()


def _get(url: str, headers: dict[str, str] | None = None) -> urllib.response.addinfourl:
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10)


def _post(url: str, body: bytes) -> urllib.response.addinfourl:
    request = urllib.request.Request(
        url, data=body, headers={"content-type": "application/json"}, method="POST"
    )
    return urllib.request.urlopen(request, timeout=10)


def _read_frame(resp: Any) -> bytes:
    """Read one SSE frame (terminated by a blank line after the data line)."""
    data = bytearray()
    while not data.endswith(b"\n\n\n"):
        byte = resp.read(1)
        if not byte:
            break
        data += byte
    return bytes(data)


def make_router() -> Router:
    router = Router()

    @router.query
    def greet(name: str) -> str:
        return f"Hello {name}!"

    @router.mutation
    def echo(message: str) -> str:
        return message

    @router.query
    def boom() -> str:
        raise RuntimeError("internal-secret-details")

    @router.query
    def typed() -> str:
        raise TRPCError("User not found", code=TRPCErrorCode.NOT_FOUND)

    return router


def test_query_over_real_http(running) -> None:
    server = running(mount(make_router()))
    import urllib.parse

    query = urllib.parse.quote(json.dumps({"name": "Ada"}))
    with _get(server.url(f"/trpc/greet?input={query}")) as resp:
        payload = json.loads(resp.read())
    assert payload == {"result": {"data": "Hello Ada!"}}


def test_mutation_post(running) -> None:
    server = running(mount(make_router()))
    with _post(server.url("/trpc/echo"), json.dumps({"message": "hi"}).encode()) as resp:
        payload = json.loads(resp.read())
    assert payload == {"result": {"data": "hi"}}


def test_batch_get(running) -> None:
    server = running(mount(make_router()))
    import urllib.parse

    inputs = urllib.parse.quote(json.dumps({"0": {"name": "a"}, "1": {"name": "b"}}))
    with _get(server.url(f"/trpc/greet,greet?batch=1&input={inputs}")) as resp:
        payload = json.loads(resp.read())
    assert payload == [
        {"result": {"data": "Hello a!"}},
        {"result": {"data": "Hello b!"}},
    ]


def test_error_envelope_shape(running) -> None:
    server = running(mount(make_router()))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server.url("/trpc/missing"))
    assert excinfo.value.code == 404
    error = json.loads(excinfo.value.read())["error"]
    assert error["data"]["code"] == "NOT_FOUND"
    assert error["data"]["httpStatus"] == 404


def test_typed_error_passthrough(running) -> None:
    server = running(mount(make_router()))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server.url("/trpc/typed"))
    error = json.loads(excinfo.value.read())["error"]
    assert error["message"] == "User not found"
    assert error["data"]["code"] == "NOT_FOUND"


def test_production_error_redaction(running) -> None:
    server = running(mount(make_router(), production=True))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server.url("/trpc/boom"))
    assert excinfo.value.code == 500
    error = json.loads(excinfo.value.read())["error"]
    assert error["message"] == "Internal server error"
    assert "internal-secret-details" not in json.dumps(error)


def test_development_error_includes_repr(running) -> None:
    server = running(mount(make_router()))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server.url("/trpc/boom"))
    error = json.loads(excinfo.value.read())["error"]
    assert "internal-secret-details" in error["message"]


def test_subscription_sse_stream(running) -> None:
    router = Router()

    @router.subscription
    async def ticks() -> AsyncIterator[object]:
        for index in range(3):
            yield tracked(str(index), {"tick": index})

    server = running(mount(router))
    resp = _get(server.url("/trpc/ticks?input=%7B%7D"))
    try:
        assert _read_frame(resp) == b"event: connected\ndata: {}\n\n\n"
        assert _read_frame(resp) == b'data: {"tick":0}\nid: 0\n\n\n'
        assert _read_frame(resp) == b'data: {"tick":1}\nid: 1\n\n\n'
        assert _read_frame(resp) == b'data: {"tick":2}\nid: 2\n\n\n'
        assert _read_frame(resp) == b"event: return\ndata: \n\n\n"
    finally:
        resp.close()


def test_disconnect_cancels_the_generator(running) -> None:
    started = threading.Event()
    finished = threading.Event()
    router = Router()

    @router.subscription
    async def stream() -> AsyncIterator[int]:
        started.set()
        try:
            yield 1
            await asyncio.sleep(60)
            yield 2
        finally:
            finished.set()

    server = running(mount(router))
    resp = _get(server.url("/trpc/stream?input=%7B%7D"))
    assert started.wait(timeout=10)
    assert _read_frame(resp) == b"event: connected\ndata: {}\n\n\n"
    assert _read_frame(resp) == b"data: 1\n\n\n"
    resp.close()
    assert finished.wait(timeout=10), "generator was not cancelled after disconnect"


def test_cors_wrapped_app_served(running) -> None:
    app = cors(mount(make_router()), allow_origins=["https://app.example.com"])
    server = running(app)
    import urllib.parse

    query = urllib.parse.quote(json.dumps({"name": "Ada"}))
    with _get(
        server.url(f"/trpc/greet?input={query}"),
        headers={"Origin": "https://app.example.com"},
    ) as resp:
        assert resp.headers["access-control-allow-origin"] == "https://app.example.com"
        json.loads(resp.read())

    request = urllib.request.Request(
        server.url("/trpc/greet"),
        method="OPTIONS",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        assert resp.status == 204


def test_arbitrary_asgi_app_served(running) -> None:
    async def plain_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert scope["type"] == "http"
        assert scope["method"] == "GET"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"plain-ok"})

    server = running(plain_app)
    with _get(server.url("/anything")) as resp:
        assert resp.read() == b"plain-ok"


def test_panel_served_in_development(running) -> None:
    server = running(mount(make_router()))
    with _get(server.url("/trpc/panel")) as resp:
        assert resp.status == 200
        assert b"html" in resp.headers.get("content-type", "").encode()
        assert resp.read()


def test_panel_disabled_in_production(running) -> None:
    server = running(mount(make_router(), production=True))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(server.url("/trpc/panel"))
    # The panel is a tRPC path in production: no procedure → NOT_FOUND envelope.
    assert excinfo.value.code == 404


def test_oversized_body_is_413(running) -> None:
    server = running(mount(make_router(), limits=_core.Limits(max_body_size=64)), max_body_size=64)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post(server.url("/trpc/echo"), json.dumps({"message": "x" * 1000}).encode())
    assert excinfo.value.code == 413
    error = json.loads(excinfo.value.read())["error"]
    assert error["data"]["code"] == "PAYLOAD_TOO_LARGE"


def test_non_get_post_method_reaches_app(running) -> None:
    server = running(mount(make_router()))
    request = urllib.request.Request(server.url("/trpc/greet"), method="DELETE")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    error = json.loads(excinfo.value.read())["error"]
    assert error["data"]["code"] in ("NOT_FOUND", "METHOD_NOT_SUPPORTED")


def test_sequential_requests_reuse_server(running) -> None:
    server = running(mount(make_router()))
    import urllib.parse

    for index in range(5):
        query = urllib.parse.quote(json.dumps({"name": f"n{index}"}))
        with _get(server.url(f"/trpc/greet?input={query}")) as resp:
            assert json.loads(resp.read())["result"]["data"] == f"Hello n{index}!"


# -- serve() Python wrapper -------------------------------------------------


def test_serve_builds_app_and_runs_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    main_thread = threading.current_thread()

    def fake_serve(app: Any, loop: Any, host: str, port: int, *, max_body_size: int) -> None:
        captured["app"] = app
        captured["loop_running"] = loop.is_running()
        captured["host"] = host
        captured["port"] = port
        captured["max_body_size"] = max_body_size
        captured["caller_is_main"] = threading.current_thread() is main_thread

    monkeypatch.setattr(serve_module, "_rust_serve", fake_serve)
    assert serve(make_router(), host="0.0.0.0", port=8765, production=True) == 0
    assert captured["app"] is not None
    assert captured["loop_running"] is True
    assert captured["caller_is_main"] is True
    assert (captured["host"], captured["port"]) == ("0.0.0.0", 8765)
    assert captured["max_body_size"] == _core.Limits().max_body_size


def test_serve_passes_configured_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_serve(app: Any, loop: Any, host: str, port: int, *, max_body_size: int) -> None:
        captured["max_body_size"] = max_body_size

    monkeypatch.setattr(serve_module, "_rust_serve", fake_serve)
    serve(make_router(), limits=_core.Limits(max_body_size=4096))
    assert captured["max_body_size"] == 4096


def test_serve_forwards_arbitrary_asgi_app(monkeypatch: pytest.MonkeyPatch) -> None:
    async def custom(scope: Any, receive: Any, send: Any) -> None:
        return None

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        serve_module,
        "_rust_serve",
        lambda app, loop, host, port, *, max_body_size: captured.update(app=app),
    )
    assert serve(app=custom) == 0
    assert captured["app"] is custom


def test_serve_rejects_missing_and_conflicting_args() -> None:
    with pytest.raises(ValueError, match="needs a Router or an ASGI app"):
        serve()
    with pytest.raises(ValueError, match="not both"):
        serve(make_router(), app=mount(make_router()))
    with pytest.raises(ValueError, match="not both"):
        serve(app=mount(make_router()), production=True)


def test_serve_stops_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    threads_before = {t.name for t in threading.enumerate()}
    monkeypatch.setattr(
        serve_module, "_rust_serve", lambda app, loop, host, port, *, max_body_size: None
    )
    serve(make_router())
    time.sleep(0.05)
    assert "trpc-rs-py-serve" not in {t.name for t in threading.enumerate()}
    assert "trpc-rs-py-serve" not in threads_before


def test_context_factory_and_middleware_reach_handlers(running) -> None:
    """context_factory + mount middleware flow through serve() mount kwargs."""
    import urllib.parse

    calls: list[str] = []

    async def record(call: Any, next: Any) -> Any:
        calls.append(call.path)
        return await next()

    def make_ctx(request_context: Any) -> dict[str, str]:
        return {"method": request_context.method}

    router = Router()

    @router.query
    def whoami(ctx: dict[str, str]) -> str:
        return ctx["method"]

    server = running(mount(router, context_factory=make_ctx, middleware=[record]))
    query = urllib.parse.quote(json.dumps({}))
    with _get(server.url(f"/trpc/whoami?input={query}")) as resp:
        assert json.loads(resp.read())["result"]["data"] == "GET"
    assert calls == ["whoami"]


def test_input_validation_error_is_400(running) -> None:
    """Bad input through the standalone server hits the shared 400 path."""
    import urllib.parse

    server = running(mount(make_router()))
    bad = urllib.parse.quote(json.dumps({"name": 123}))
    try:
        _get(server.url(f"/trpc/greet?input={bad}"))
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as error:
        assert error.code == 400
        assert json.loads(error.read())["error"]["data"]["code"] == "BAD_REQUEST"


def test_csrf_wrapped_app_served(running) -> None:
    """csrf_protect() works as an ASGI wrapper on the standalone server."""
    from trpc_rs_py.csrf import csrf_protect

    app = csrf_protect(
        mount(make_router()),
        allowed_origins=["https://app.example.com"],
    )
    server = running(app)
    body = json.dumps({"message": "hi"}).encode()
    # Credentialed cross-site request without a matching Origin is rejected.
    try:
        request = urllib.request.Request(
            server.url("/trpc/echo"),
            data=body,
            headers={
                "content-type": "application/json",
                "cookie": "session=abc",
                "origin": "https://evil.example.com",
            },
            method="POST",
        )
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as error:
        assert error.code == 403
        assert json.loads(error.read())["error"]["code"] == "FORBIDDEN"
