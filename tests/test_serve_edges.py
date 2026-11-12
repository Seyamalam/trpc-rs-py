"""Transport-level edge tests for the standalone Rust server (`_core.serve`).

Raw-socket cases ordinary HTTP clients cannot produce: chunked bodies over
the transport limit, malformed request lines, escaped request paths,
response-channel backpressure, connections during graceful shutdown, and
ASGI apps that fail before or after `http.response.start`.
"""

import asyncio
import json
import socket
import struct
import threading
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

import trpc_rs_py.serve as serve_module
from trpc_rs_py import _core, mount
from trpc_rs_py.router import Router
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
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)
        self._loop_thread.join(timeout=10)
        self.loop.close()
        assert not self._thread.is_alive(), "_core.serve did not return after loop stop"


@pytest.fixture
def running() -> Iterator[Any]:
    servers: list[_RunningServer] = []

    def spawn(app: Any, **kwargs: Any) -> _RunningServer:
        server = _RunningServer(app, **kwargs)
        servers.append(server)
        return server

    yield spawn
    for server in servers:
        server.stop()


def make_router() -> Router:
    router = Router()

    @router.query
    def greet(name: str) -> str:
        return f"Hello {name}!"

    @router.mutation
    def echo(message: str) -> str:
        return message

    return router


def _raw(port: int, request: bytes) -> bytes:
    """Send one raw request; read until the peer closes or times out."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        sock.settimeout(10)
        data = bytearray()
        while True:
            try:
                chunk = sock.recv(65536)
            except (TimeoutError, ConnectionError):
                break
            if not chunk:
                break
            data += chunk
        return bytes(data)


def test_escaped_path_is_percent_decoded(running) -> None:
    server = running(mount(make_router()))
    # %65 = 'e' — the decoded path resolves to `greet`.
    query = "%7B%22name%22%3A%22Ada%22%7D"
    response = _raw(
        server.port,
        (
            f"GET /trpc/gr%65%65t?input={query} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
        ).encode(),
    )
    assert b"Hello Ada!" in response


def test_malformed_escapes_pass_through_literally(running) -> None:
    server = running(mount(make_router()))
    # %zz is not hex and %2 is truncated: both pass through unchanged, so the
    # '%' lands in the decoded path and fails the procedure-path charset.
    for raw_path in ("/trpc/a%zz", "/trpc/b%2"):
        response = _raw(
            server.port,
            (
                f"GET {raw_path}?input=%7B%7D HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
            ).encode(),
        )
        assert b"BAD_REQUEST" in response, raw_path


def test_letter_hex_escapes_decode(running) -> None:
    server = running(mount(make_router()))
    # %6e/%6f use a-f digits, %4E/%4F use A-F digits: all decode, and the
    # resulting path routes to no procedure.
    response = _raw(
        server.port,
        b"GET /trpc/%6e%4F%6f%4E HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert b"NOT_FOUND" in response


def test_http_10_request_line_serves(running) -> None:
    server = running(mount(make_router()))
    response = _raw(
        server.port,
        b"GET /trpc/greet?input=%7B%22name%22%3A%22A%22%7D HTTP/1.0\r\nHost: x\r\n\r\n",
    )
    assert b" 200" in response.split(b"\r\n", 1)[0]
    assert b"Hello A!" in response


def test_malformed_request_line_gets_400_or_close(running) -> None:
    server = running(mount(make_router()))
    response = _raw(server.port, b"THIS IS NOT HTTP\r\n\r\n")
    # hyper answers malformed request lines with 400 or just drops the conn.
    assert response == b"" or b"400" in response.split(b"\r\n", 1)[0]


def test_chunked_body_over_transport_limit_is_413(running) -> None:
    server = running(mount(make_router()), max_body_size=64)
    body = b"x" * 200
    request = (
        b"POST /trpc/echo HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        + f"{len(body):x}\r\n".encode()
        + body
        + b"\r\n0\r\n\r\n"
    )
    response = _raw(server.port, request)
    # No Content-Length to pre-reject: the streaming limit trips mid-body.
    assert b"413" in response.split(b"\r\n", 1)[0]
    assert b"PAYLOAD_TOO_LARGE" in response


def test_response_channel_backpressure(running) -> None:
    """Parks `send` on the 16-chunk channel; drain wake-up re-arms it."""

    async def big_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for _ in range(48):
            await send({"type": "http.response.body", "body": b"x" * 16384, "more_body": True})
        await send({"type": "http.response.body", "body": b"done"})

    server = running(big_app)
    received = bytearray()
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
        sock.sendall(b"GET /big HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        # Withhold reads so kernel buffers fill and Rust's channel reaches
        # capacity — that is the path that exercises offer()/drained.
        time.sleep(0.5)
        sock.settimeout(10)
        while True:
            try:
                data = sock.recv(65536)
            except (TimeoutError, ConnectionError):
                break
            if not data:
                break
            received += data
    body = bytes(received)
    assert len(body) > 48 * 16384, f"streamed only {len(body)} bytes"
    assert b"done" in body[-64:]


def test_sse_burst_backpressures_and_completes(running) -> None:
    router = Router()

    @router.subscription
    async def ticks() -> AsyncIterator[object]:
        # ~8 KiB per frame so the 16-chunk channel fills behind a slow reader.
        for index in range(48):
            yield tracked(str(index), {"tick": index, "pad": "x" * 8192})

    server = running(mount(router))
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
        sock.sendall(
            b"GET /trpc/ticks?input=%7B%7D HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
        )
        time.sleep(0.5)
        sock.settimeout(10)
        data = bytearray()
        while True:
            try:
                chunk = sock.recv(65536)
            except (TimeoutError, ConnectionError):
                break
            if not chunk:
                break
            data += chunk
    assert b"event: connected" in data
    assert b"id: 47" in data
    assert b"event: return" in data


def test_sse_client_abort_during_burst(running) -> None:
    """RST mid-burst: the next `offer` fails and the app unwinds."""
    finished = threading.Event()
    router = Router()

    @router.subscription
    async def burst() -> AsyncIterator[object]:
        try:
            for index in range(48):
                yield tracked(str(index), {"tick": index, "pad": "x" * 8192})
        finally:
            finished.set()

    server = running(mount(router))
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        sock.sendall(b"GET /trpc/burst?input=%7B%7D HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(10)
        received = 0
        while received < 60_000:
            data = sock.recv(65536)
            if not data:
                break
            received += len(data)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()
    assert finished.wait(timeout=10), "subscription did not unwind after RST"


def test_sse_client_abort_mid_stream(running) -> None:
    finished = threading.Event()
    router = Router()

    @router.subscription
    async def stream() -> AsyncIterator[int]:
        try:
            yield 1
            await asyncio.sleep(60)
            yield 2
        finally:
            finished.set()

    server = running(mount(router))
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        sock.sendall(b"GET /trpc/stream?input=%7B%7D HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(10)
        data = bytearray()
        deadline = time.monotonic() + 10
        while b"data: 1" not in data and time.monotonic() < deadline:
            data += sock.recv(65536)
        assert b"event: connected" in data
        # Abrupt reset (linger=0): the body channel dies while the app is
        # parked in sleep, forcing the disconnect wake-ups.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()
    assert finished.wait(timeout=10), "generator was not cancelled after RST"


def test_connection_open_during_shutdown(running) -> None:
    server = running(mount(make_router()))
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    try:
        # A partial request keeps the connection in-flight when the loop
        # stops; graceful shutdown must still let serve() return.
        sock.sendall(b"GET /trpc/greet?input=%7B%7D HTTP/1.1\r\nHost: x\r\n")
        server.stop()
    finally:
        sock.close()


def test_app_returning_without_start_is_500(running) -> None:
    async def silent_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        return None

    server = running(silent_app)
    response = _raw(server.port, b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 500")
    assert b"Internal Server Error" in response


def test_app_raising_before_start_is_500(running) -> None:
    async def bad_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("app exploded")

    server = running(bad_app)
    response = _raw(server.port, b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 500")


def test_schedule_failure_is_500(running, monkeypatch: pytest.MonkeyPatch) -> None:
    def exploding_schedule(*args: Any) -> None:
        raise RuntimeError("scheduling broke")

    monkeypatch.setattr(serve_module, "_schedule", exploding_schedule)
    server = running(mount(make_router()))
    response = _raw(
        server.port,
        b"GET /trpc/greet?input=%7B%7D HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert response.startswith(b"HTTP/1.1 500")


async def _double_start_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    # Second start: the head sender is already taken, so Rust ignores it.
    await send({"type": "http.response.start", "status": 201, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _unusual_status_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 42, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _body_after_finish_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"first"})
    # The channel is closed after the first body: this offer raises
    # ConnectionError inside send(), which kills the coroutine.
    await send({"type": "http.response.body", "body": b"second"})


async def _unprintable_error_app(scope: Any, receive: Any, send: Any) -> None:
    class Unprintable(Exception):
        def __str__(self) -> str:
            raise RuntimeError("unprintable")

    raise Unprintable("boom")


def test_app_double_start_uses_first(running) -> None:
    server = running(_double_start_app)
    response = _raw(
        server.port,
        b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b"ok\r\n0\r\n\r\n")


def test_app_unusual_status_falls_back(running) -> None:
    server = running(_unusual_status_app)
    response = _raw(
        server.port,
        b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    # Status 42 is not a valid http::StatusCode; the response keeps the
    # default 200 rather than failing the request.
    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b"ok\r\n0\r\n\r\n")


def test_app_body_after_finish_is_rejected(running) -> None:
    server = running(_body_after_finish_app)
    response = _raw(
        server.port,
        b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    # The head and first chunk were already sent; the extra body dies on
    # the closed channel, so the response carries only "first".
    assert response.startswith(b"HTTP/1.1 200")
    assert b"first" in response
    assert b"second" not in response


def test_app_unprintable_error_is_500(running) -> None:
    server = running(_unprintable_error_app)
    response = _raw(
        server.port,
        b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert response.startswith(b"HTTP/1.1 500")


def test_serve_without_schedule_hook_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    monkeypatch.delattr(serve_module, "_schedule")
    try:
        with pytest.raises(AttributeError):
            _core.serve(mount(make_router()), loop, "127.0.0.1", _free_port())
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=10)
        loop.close()


def test_schedule_without_arm_disconnect_fires_nothing(
    running, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `bridge.arm`: stream drop must not fire missing callbacks."""

    def no_arm_schedule(app: Any, scope: Any, body: bytes, bridge: Any, loop: Any) -> None:
        # No arm(), no work: the bridge is garbage-collected right after,
        # which drops the head sender and resolves the select's error arm.
        pass

    monkeypatch.setattr(serve_module, "_schedule", no_arm_schedule)
    server = running(mount(make_router()))
    response = _raw(
        server.port,
        b"GET /trpc/greet?input=%7B%7D HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    assert response.startswith(b"HTTP/1.1 500")


def test_done_before_start_is_500(running, monkeypatch: pytest.MonkeyPatch) -> None:
    """Done before the head is armed resolves the select's error arm."""
    held: list[Any] = []

    def done_schedule(app: Any, scope: Any, body: bytes, bridge: Any, loop: Any) -> None:
        # Keep the bridge alive so the head sender stays armed; only the
        # done signal reaches the select.
        held.append(bridge)
        bridge.done(RuntimeError("synthetic"))

    monkeypatch.setattr(serve_module, "_schedule", done_schedule)
    server = running(mount(make_router()))
    response = _raw(
        server.port,
        b"GET /trpc/greet?input=%7B%7D HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
    )
    held.clear()
    assert response.startswith(b"HTTP/1.1 500")


def test_second_bind_on_same_port_fails(running) -> None:
    server = running(mount(make_router()))
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _core.serve(mount(make_router()), loop, "127.0.0.1", server.port)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=10)
    loop.close()
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert "failed to bind" in str(errors[0])


def test_serve_returns_when_loop_is_not_running() -> None:
    loop = asyncio.new_event_loop()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _core.serve(mount(make_router()), loop, "127.0.0.1", _free_port())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    loop.close()
    assert not thread.is_alive(), "_core.serve did not return for a dead loop"
    assert not errors


def test_body_after_finish_surfaces_app_error(running) -> None:
    """`offer` on a finished channel fails the app coroutine."""

    async def greedy_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first"})
        await send({"type": "http.response.body", "body": b"second"})

    server = running(greedy_app)
    response = _raw(server.port, b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    # The head and first chunk went out; the second offer raised inside the
    # app and the body stream ended early.
    assert response.startswith(b"HTTP/1.1 200")
    # The server must still serve the next request.
    response = _raw(server.port, b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    assert response.startswith(b"HTTP/1.1 200")


def test_invalid_status_and_headers_are_skipped(running) -> None:
    async def weird_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 99,
                "headers": [(b"bad\nname", b"v"), (b"x-ok", b"yes")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    server = running(weird_app)
    response = _raw(server.port, b"GET /x HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    # Status 99 is not a valid StatusCode -> the default 200 is kept;
    # the illegal header name is dropped, the legal one survives.
    assert response.startswith(b"HTTP/1.1 200")
    assert b"x-ok: yes" in response
    assert b"bad\nname" not in response


def test_concurrent_batch_and_subscription(running) -> None:
    router = make_router()
    done = threading.Event()

    @router.subscription
    async def ticks() -> AsyncIterator[object]:
        for index in range(4):
            yield tracked(str(index), {"tick": index})
        done.set()

    server = running(mount(router))
    results: list[bytes] = []
    errors: list[BaseException] = []

    def read_stream() -> None:
        try:
            request = urllib.request.Request(server.url("/trpc/ticks?input=%7B%7D"))
            with urllib.request.urlopen(request, timeout=10) as resp:
                results.append(resp.read())
        except BaseException as error:
            errors.append(error)

    def post_batch() -> None:
        try:
            body = json.dumps({"0": {"message": "a"}, "1": {"message": "b"}}).encode()
            request = urllib.request.Request(
                server.url("/trpc/echo,echo?batch=1"),
                data=body,
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as resp:
                results.append(resp.read())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=read_stream) for _ in range(3)]
    threads += [threading.Thread(target=post_batch) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors
    assert done.wait(timeout=5)
    assert sum(b"event: connected" in blob for blob in results) == 3
    assert sum(b'"data":"a"' in blob for blob in results) == 3
