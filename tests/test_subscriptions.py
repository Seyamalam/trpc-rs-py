# Subscription tests: SSE framing, tracked ids, resume, cancellation,
# backpressure, error streaming, and method/batch rules — all verified
# against the official @trpc/server 11.25.0 wire format.

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from trpc_rs_py import Router, mount, tracked
from trpc_rs_py.errors import TRPCError, TRPCErrorCode
from trpc_rs_py.streaming import Tracked


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


async def _collect(
    app: Any,
    scope: dict[str, Any],
    *,
    disconnect_after: float | None = None,
) -> list[dict[str, Any]]:
    """Run the app until it returns or disconnects, collecting messages."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    if disconnect_after is None:
        await app(scope, _receive_never_disconnect(), send)
        return sent

    disconnect = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(disconnect_after)
    disconnect.set()
    await asyncio.wait_for(task, timeout=5)
    return sent


def make_stream_router() -> Router:
    router = Router()

    @router.subscription
    async def ticks() -> AsyncIterator[object]:
        for index in range(3):
            yield tracked(str(index), {"tick": index})

    @router.subscription(name="plain")
    async def plain_stream() -> AsyncIterator[int]:
        yield 1
        yield 2

    @router.query
    def health() -> bool:
        return True

    return router


def test_subscription_streams_connected_data_and_return() -> None:
    app = mount(make_stream_router())
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/ticks", query=b"input=%7B%7D")))
    start = sent[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"text/event-stream"
    assert headers[b"cache-control"] == b"no-cache, no-transform"
    assert headers[b"x-accel-buffering"] == b"no"

    bodies = _bodies(sent)
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"
    assert bodies[1] == b'data: {"tick":0}\nid: 0\n\n\n'
    assert bodies[2] == b'data: {"tick":1}\nid: 1\n\n\n'
    assert bodies[3] == b'data: {"tick":2}\nid: 2\n\n\n'
    assert bodies[4] == b"event: return\ndata: \n\n\n"
    # The empty end-of-body terminator is sent last.
    assert sent[-1] == {"type": "http.response.body", "body": b""}


def test_untracked_events_have_no_id_line() -> None:
    app = mount(make_stream_router())
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/plain", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert bodies[1] == b"data: 1\n\n\n"
    assert bodies[2] == b"data: 2\n\n\n"


def test_untracked_after_tracked_restores_id() -> None:
    router = Router()

    @router.subscription
    async def mixed() -> AsyncIterator[object]:
        yield tracked("9", "a")
        yield "b"

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/mixed", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert bodies[1] == b'data: "a"\nid: 9\n\n\n'
    assert bodies[2] == b'data: "b"\nid: \n\n\nevent: restore-id\ndata: \nid: 9\n\n\n'


def test_resume_id_makes_first_untracked_event_restore() -> None:
    app = mount(make_stream_router())
    sent = asyncio.run(
        _collect(
            app,
            _scope(
                "GET",
                "/trpc/plain",
                query=b"input=%7B%7D",
                headers=[(b"last-event-id", b"42")],
            ),
        )
    )
    bodies = _bodies(sent)
    assert bodies[1] == b"data: 1\nid: \n\n\nevent: restore-id\ndata: \nid: 42\n\n\n"


def test_last_event_id_query_param_is_accepted() -> None:
    app = mount(make_stream_router())
    sent = asyncio.run(
        _collect(
            app,
            _scope(
                "GET",
                "/trpc/plain",
                query=b"input=%7B%7D&lastEventId=17",
            ),
        )
    )
    bodies = _bodies(sent)
    assert b"id: 17" in bodies[1]


def test_last_event_id_reaches_handler_parameter() -> None:
    seen: list[str | None] = []
    router = Router()

    @router.subscription
    async def replay(lastEventId: str | None = None) -> AsyncIterator[int]:
        seen.append(lastEventId)
        yield 1

    app = mount(router)
    asyncio.run(
        _collect(
            app,
            _scope(
                "GET",
                "/trpc/replay",
                query=b"input=%7B%7D",
                headers=[(b"last-event-id", b"7")],
            ),
        )
    )
    assert seen == ["7"]


def test_last_event_id_merged_in_input_reaches_handler() -> None:
    """The official client merges lastEventId into the input on reconnect."""
    seen: list[str | None] = []
    router = Router()

    @router.subscription
    async def replay(lastEventId: str | None = None) -> AsyncIterator[int]:
        seen.append(lastEventId)
        yield 1

    app = mount(router)
    input_json = json.dumps({"lastEventId": "31"}).encode()
    from urllib.parse import quote_from_bytes

    asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/replay", query=b"input=" + quote_from_bytes(input_json).encode()),
        )
    )
    assert seen == ["31"]


def test_subscription_input_is_validated() -> None:
    router = Router()

    @router.subscription
    async def room(name: str) -> AsyncIterator[str]:
        yield f"joined {name}"

    app = mount(router)
    input_json = json.dumps({"name": "lobby"}).encode()
    from urllib.parse import quote_from_bytes

    sent = asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/room", query=b"input=" + quote_from_bytes(input_json).encode()),
        )
    )
    assert _bodies(sent)[1] == b'data: "joined lobby"\n\n\n'


def test_invalid_input_streams_bad_request_error() -> None:
    router = Router()

    @router.subscription
    async def room(count: int) -> AsyncIterator[int]:
        yield count

    app = mount(router)
    sent = asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/room", query=b"input=%7B%22count%22%3A%22abc%22%7D"),
        )
    )
    bodies = _bodies(sent)
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["data"]["code"] == "BAD_REQUEST"


def test_post_to_subscription_streams() -> None:
    """A valid JSON POST streams SSE (methodOverride semantics, as queries)."""
    app = mount(make_stream_router())
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = _scope(
        "POST",
        "/trpc/ticks",
        headers=[(b"content-type", b"application/json")],
    )
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"text/event-stream"
    bodies = _bodies(sent)
    assert bodies[1:4] == [
        b'data: {"tick":0}\nid: 0\n\n\n',
        b'data: {"tick":1}\nid: 1\n\n\n',
        b'data: {"tick":2}\nid: 2\n\n\n',
    ]
    assert bodies[4] == b"event: return\ndata: \n\n\n"


def test_put_to_subscription_streams_method_not_supported() -> None:
    """Methods outside GET/POST end the stream with a serialized-error."""
    app = mount(make_stream_router())
    sent = asyncio.run(_collect(app, _scope("PUT", "/trpc/ticks", query=b"input=%7B%7D")))
    start = sent[0]
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"text/event-stream"
    bodies = _bodies(sent)
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_batched_subscription_call_is_json_bad_request() -> None:
    """Official: batched subscription calls get a JSON error envelope, not SSE."""
    app = mount(make_stream_router())
    sent = asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/ticks", query=b"batch=1&input=%7B%220%22%3A%7B%7D%7D"),
        )
    )
    start = sent[0]
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"application/json"
    results = json.loads(_bodies(sent)[0])
    assert results[0]["error"]["data"]["code"] == "BAD_REQUEST"
    assert "batch subscription" in results[0]["error"]["message"]


def test_post_batched_subscription_is_json_bad_request() -> None:
    """Batched subscriptions fail per call even on an accepted method."""
    app = mount(make_stream_router())
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = _scope(
        "POST",
        "/trpc/ticks",
        query=b"batch=1",
        headers=[(b"content-type", b"application/json")],
    )
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"application/json"
    results = json.loads(_bodies(sent)[0])
    assert results[0]["error"]["data"]["code"] == "BAD_REQUEST"
    assert "batch subscription" in results[0]["error"]["message"]


def test_batched_subscription_bad_method_is_method_not_supported() -> None:
    """The method check runs before the batch rejection, per call."""
    app = mount(make_stream_router())
    sent = asyncio.run(
        _collect(
            app,
            _scope("PUT", "/trpc/ticks", query=b"batch=1&input=%7B%220%22%3A%7B%7D%7D"),
        )
    )
    start = sent[0]
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"application/json"
    results = json.loads(_bodies(sent)[0])
    assert results[0]["error"]["data"]["code"] == "METHOD_NOT_SUPPORTED"


def test_mixed_kind_batch_is_top_level_bad_request() -> None:
    """Official: mixing procedure kinds in one batch fails before any call."""
    app = mount(make_stream_router())
    sent = asyncio.run(
        _collect(
            app,
            _scope(
                "GET",
                "/trpc/health,ticks",
                query=b"batch=1&input=%7B%220%22%3A%7B%7D%2C%221%22%3A%7B%7D%7D",
            ),
        )
    )
    start = sent[0]
    assert start["status"] == 400
    body = json.loads(_bodies(sent)[0])
    assert body["error"]["data"]["code"] == "BAD_REQUEST"
    assert "Cannot mix procedure types" in body["error"]["message"]


def test_trpc_error_before_streaming_streams_serialized_error() -> None:
    router = Router()

    @router.subscription
    async def guarded() -> AsyncIterator[int]:
        raise TRPCError("nope", code=TRPCErrorCode.UNAUTHORIZED)
        yield 0  # pragma: no cover - unreachable

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/guarded", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["message"] == "nope"
    assert error["data"]["code"] == "UNAUTHORIZED"


def test_mid_stream_raise_streams_serialized_error() -> None:
    router = Router()

    @router.subscription
    async def flaky() -> AsyncIterator[int]:
        yield 1
        raise TRPCError("lost the source", code=TRPCErrorCode.INTERNAL_SERVER_ERROR)

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/flaky", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert bodies[1] == b"data: 1\n\n\n"
    event_line, data_line = bodies[2].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["message"] == "lost the source"


def test_production_redacts_internal_subscription_errors() -> None:
    router = Router()

    @router.subscription
    async def flaky() -> AsyncIterator[int]:
        yield 1
        raise RuntimeError("database credentials leaked")

    app = mount(router, production=True)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/flaky", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    event_line, data_line = bodies[2].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["message"] == "Internal server error"
    assert "credentials" not in json.dumps(error)


def test_non_iterable_return_streams_internal_error() -> None:
    router = Router()

    @router.subscription
    def not_async() -> int:
        return 42

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/not_async", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "did not return" in error["message"]


def test_disconnect_cancels_the_generator() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()
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

    app = mount(router)
    sent = asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/stream", query=b"input=%7B%7D"),
            disconnect_after=0.1,
        )
    )
    assert started.is_set()
    assert finished.is_set()
    bodies = _bodies(sent)
    assert bodies[1] == b"data: 1\n\n\n"
    assert not any(b"data: 2" in body for body in bodies)


def test_ping_frames_flow_while_idle() -> None:
    router = Router()

    @router.subscription
    async def slow() -> AsyncIterator[int]:
        await asyncio.sleep(0.15)
        yield 1

    app = mount(router, sse_ping_interval=0.03)
    sent = asyncio.run(
        _collect(
            app,
            _scope("GET", "/trpc/slow", query=b"input=%7B%7D"),
            disconnect_after=0.2,
        )
    )
    bodies = _bodies(sent)
    assert b"event: ping\ndata: \n\n\n" in bodies
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"


def test_event_size_limit_streams_error() -> None:
    router = Router()

    @router.subscription
    async def big() -> AsyncIterator[str]:
        yield "x" * 200

    app = mount(router, max_sse_event_size=64)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/big", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"


def test_backpressure_does_not_pull_ahead() -> None:
    """The generator is only pulled once a frame has been accepted by send."""
    pulls: list[int] = []
    router = Router()

    @router.subscription
    async def counted() -> AsyncIterator[int]:
        for index in range(5):
            pulls.append(index)
            yield index

    app = mount(router)
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        gate = asyncio.Event()

        async def send(message: dict[str, Any]) -> None:
            # Block each data frame until the test releases it.
            if message.get("body", b"").startswith(b"data:"):
                await gate.wait()
            sent.append(message)

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}

        scope = _scope("GET", "/trpc/counted", query=b"input=%7B%7D")
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.sleep(0.05)
        # Only one item pulled and encoded; the generator is paused behind send.
        assert pulls == [0]
        gate.set()
        gate.clear()
        await asyncio.sleep(0.05)
        assert pulls == [0, 1]
        task.cancel()

    asyncio.run(run())


def test_tracked_rejects_invalid_event_id() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        tracked("bad\nid", "data")
    assert isinstance(tracked("ok", 1), Tracked)


def test_subscription_registration_marks_kind() -> None:
    router = Router()

    @router.subscription
    async def stream() -> AsyncIterator[int]:
        yield 1

    procedure = router.procedures["stream"]
    assert procedure.kind.value == "subscription"


def test_subscription_output_is_validated_per_item() -> None:
    router = Router()

    class Point(BaseModel):
        x: int

    @router.subscription
    async def bad() -> AsyncIterator[int]:
        yield Point(x=1)  # type: ignore[misc] - deliberately wrong item type

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/bad", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    event_line, data_line = bodies[1].split(b"\n")[:2]
    assert event_line == b"event: serialized-error"
    error = json.loads(data_line.split(b": ", 1)[1])
    assert error["data"]["code"] == "INTERNAL_SERVER_ERROR"
    assert error["message"] == "Invalid output from procedure"


def test_subscription_output_validator_coerces_items() -> None:
    router = Router()

    class Msg(BaseModel):
        text: str

    @router.subscription
    async def flow() -> AsyncIterator[Msg]:
        yield Msg(text="hi")

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/flow", query=b"input=%7B%7D")))
    assert _bodies(sent)[1] == b'data: {"text":"hi"}\n\n\n'
