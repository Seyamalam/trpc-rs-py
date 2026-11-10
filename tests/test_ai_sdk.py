# Vercel AI SDK v5 UI message stream helpers: chunk sequencing, source
# adapters (str / OpenAI-shaped / Anthropic-shaped), the ai_sdk_endpoint
# ASGI transport (protocol headers, data framing, [DONE], keep-alive,
# disconnect cancellation, backpressure), and through-the-router delivery.

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from trpc_rs_py import Router, mount, tracked
from trpc_rs_py.streaming import (
    ai_data_part,
    ai_message_stream,
    ai_sdk_endpoint,
    ai_text_stream,
)


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


async def _collect(
    app: Any,
    scope: dict[str, Any],
    *,
    disconnect_after: float | None = None,
    body: bytes | None = None,
) -> list[dict[str, Any]]:
    """Run the app until it returns or disconnects, collecting messages."""
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    queued = (
        [{"type": "http.request", "body": body, "more_body": False}] if body is not None else []
    )

    if disconnect_after is None:

        async def receive() -> dict[str, Any]:
            if queued:
                return queued.pop(0)
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}

        await app(scope, receive, send)
        return sent

    disconnect = asyncio.Event()

    async def gated_receive() -> dict[str, Any]:
        if queued:
            return queued.pop(0)
        await disconnect.wait()
        return {"type": "http.disconnect"}

    task = asyncio.create_task(app(scope, gated_receive, send))
    await asyncio.sleep(disconnect_after)
    disconnect.set()
    await asyncio.wait_for(task, timeout=5)
    return sent


async def _aiter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


def _data_frames(bodies: list[bytes]) -> list[dict[str, Any]]:
    """Decode `data: {json}` SSE frames into objects (skips comments/DONE)."""
    chunks: list[dict[str, Any]] = []
    for frame in bodies:
        for line in frame.split(b"\n"):
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                chunks.append(json.loads(line[len(b"data: ") :]))
    return chunks


# --- part builders and source adapters --------------------------------------


def test_ai_text_stream_str_source_sequence() -> None:
    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_text_stream(_aiter(["Hel", "lo"]), text_id="t1")]

    chunks = asyncio.run(main())
    assert chunks == [
        {"type": "text-start", "id": "t1"},
        {"type": "text-delta", "id": "t1", "delta": "Hel"},
        {"type": "text-delta", "id": "t1", "delta": "lo"},
        {"type": "text-end", "id": "t1"},
    ]


def test_ai_text_stream_accepts_sync_iterable() -> None:
    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_text_stream(["a", "b"])]

    chunks = asyncio.run(main())
    assert [c["type"] for c in chunks] == ["text-start", "text-delta", "text-delta", "text-end"]


def test_ai_text_stream_skips_empty_deltas() -> None:
    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_text_stream(_aiter(["", "x", ""]))]

    chunks = asyncio.run(main())
    assert [c.get("delta") for c in chunks if c["type"] == "text-delta"] == ["x"]


def test_ai_text_stream_openai_object_chunks() -> None:
    """OpenAI ChatCompletionChunk-shaped objects feed deltas via choices[0]."""

    def chunk(content: Any = None) -> Any:
        delta = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    async def main() -> list[dict[str, Any]]:
        source = _aiter(
            [
                chunk("a"),
                chunk(None),  # role/tool chunks carry no text
                SimpleNamespace(choices=[]),  # usage-only chunk
                chunk("b"),
            ]
        )
        return [c async for c in ai_text_stream(source)]

    chunks = asyncio.run(main())
    assert [c.get("delta") for c in chunks if c["type"] == "text-delta"] == ["a", "b"]


def test_ai_text_stream_dict_and_anthropic_shapes() -> None:
    async def main() -> list[dict[str, Any]]:
        source = _aiter(
            [
                {"choices": [{"delta": {"content": "x"}}]},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "y"}},
                SimpleNamespace(delta=SimpleNamespace(text="z")),
                {"type": "ping"},  # unrecognized: skipped
            ]
        )
        return [c async for c in ai_text_stream(source)]

    chunks = asyncio.run(main())
    assert [c.get("delta") for c in chunks if c["type"] == "text-delta"] == ["x", "y", "z"]


def test_multiple_text_blocks_keep_distinct_ids() -> None:
    async def parts() -> AsyncIterator[dict[str, Any]]:
        async for chunk in ai_text_stream(_aiter(["one"])):
            yield chunk
        async for chunk in ai_text_stream(_aiter(["two"])):
            yield chunk

    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_message_stream(parts())]

    chunks = asyncio.run(main())
    starts = [c["id"] for c in chunks if c["type"] == "text-start"]
    ends = [c["id"] for c in chunks if c["type"] == "text-end"]
    assert len(starts) == 2 and starts[0] != starts[1]
    assert ends == starts


def test_generated_ids_monotonic_and_prefixed() -> None:
    async def main() -> tuple[str, str, str]:
        first_id = ""
        async for chunk in ai_text_stream(_aiter(["a"])):
            first_id = chunk["id"]
            break
        second_id = ""
        async for chunk in ai_text_stream(_aiter(["b"])):
            second_id = chunk["id"]
            break
        message_id = ""
        async for chunk in ai_message_stream(_aiter([])):
            message_id = chunk["messageId"]
            break
        return first_id, second_id, message_id

    first, second, message = asyncio.run(main())
    assert first.startswith("text-") and second.startswith("text-")
    assert int(first.split("-", 1)[1]) < int(second.split("-", 1)[1])
    assert message.startswith("msg-")


def test_ai_message_stream_envelope_order() -> None:
    async def main() -> list[dict[str, Any]]:
        parts = ai_text_stream(_aiter(["hi"]), text_id="t-1")
        return [c async for c in ai_message_stream(parts, message_id="m-1")]

    chunks = asyncio.run(main())
    assert [c["type"] for c in chunks] == [
        "start",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]
    assert chunks[0] == {"type": "start", "messageId": "m-1"}
    assert chunks[-1] == {"type": "finish"}


def test_ai_message_stream_passes_data_parts_through() -> None:
    async def parts() -> AsyncIterator[dict[str, Any]]:
        yield ai_data_part("weather", {"location": "SF"}, part_id="p-1", transient=True)

    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_message_stream(parts())]

    chunks = asyncio.run(main())
    assert chunks[1] == {
        "type": "data-weather",
        "data": {"location": "SF"},
        "id": "p-1",
        "transient": True,
    }
    assert chunks[-1] == {"type": "finish"}


def test_ai_message_stream_error_chunk_replaces_finish() -> None:
    async def bad_parts() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "text-start", "id": "x"}
        raise RuntimeError("lost the source")

    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_message_stream(bad_parts())]

    chunks = asyncio.run(main())
    assert chunks[-1] == {"type": "error", "errorText": "lost the source"}
    assert all(c["type"] != "finish" for c in chunks)


def test_ai_message_stream_error_text_mapper() -> None:
    async def bad_parts() -> AsyncIterator[dict[str, Any]]:
        raise RuntimeError("database credentials leaked")
        yield {}  # pragma: no cover - unreachable

    async def main() -> list[dict[str, Any]]:
        stream = ai_message_stream(bad_parts(), error_text=lambda error: "internal error")
        return [c async for c in stream]

    chunks = asyncio.run(main())
    assert chunks[-1] == {"type": "error", "errorText": "internal error"}


def test_ai_data_part_shape_and_validation() -> None:
    part = ai_data_part("usage", {"tokens": 42})
    assert part == {"type": "data-usage", "data": {"tokens": 42}}
    with pytest.raises(ValueError, match="must be non-empty"):
        ai_data_part("has spaces", {})


def test_ai_message_stream_rejects_non_iterable_source() -> None:
    async def main() -> list[dict[str, Any]]:
        return [c async for c in ai_message_stream({"type": "text-start"})]  # type: ignore[arg-type]

    chunks = asyncio.run(main())
    # A dict is a single part, not a stream — the failure rides as an error chunk.
    assert chunks[-1]["type"] == "error"
    assert "iterable of chunks" in chunks[-1]["errorText"]


# --- ai_sdk_endpoint transport ----------------------------------------------


def _chat_factory(
    tokens: list[str] | None = None,
) -> Any:
    """An endpoint factory streaming one wrapped text block."""

    async def factory(body: Any, scope: Any) -> Any:
        return ai_message_stream(
            ai_text_stream(_aiter(tokens or ["Hel", "lo"]), text_id="t-1"),
            message_id="m-1",
        )

    return factory


def test_endpoint_streams_chunks_with_protocol_headers() -> None:
    app = ai_sdk_endpoint(_chat_factory())
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat")))
    start = sent[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
    headers = dict(start["headers"])
    assert headers[b"content-type"] == b"text/event-stream"
    assert headers[b"cache-control"] == b"no-cache"
    assert headers[b"x-accel-buffering"] == b"no"
    assert headers[b"x-vercel-ai-ui-message-stream"] == b"v1"

    bodies = _bodies(sent)
    assert bodies[0] == b'data: {"type":"start","messageId":"m-1"}\n\n'
    assert bodies[1] == b'data: {"type":"text-start","id":"t-1"}\n\n'
    assert bodies[2] == b'data: {"type":"text-delta","id":"t-1","delta":"Hel"}\n\n'
    assert bodies[4] == b'data: {"type":"text-end","id":"t-1"}\n\n'
    assert bodies[5] == b'data: {"type":"finish"}\n\n'
    assert bodies[6] == b"data: [DONE]\n\n"
    assert sent[-1] == {"type": "http.response.body", "body": b""}


def test_endpoint_post_delivers_json_body() -> None:
    seen: list[Any] = []

    async def factory(body: Any, scope: Any) -> Any:
        seen.append(body)
        return ai_message_stream(ai_text_stream(["ok"]))

    app = ai_sdk_endpoint(factory)
    payload = json.dumps({"messages": [{"role": "user", "parts": []}]}).encode()
    sent = asyncio.run(
        _collect(
            app,
            _scope("POST", "/api/chat", headers=[(b"content-type", b"application/json")]),
            body=payload,
        )
    )
    assert seen == [{"messages": [{"role": "user", "parts": []}]}]
    assert _bodies(sent)[-1] == b"data: [DONE]\n\n"


def test_endpoint_get_passes_none_body() -> None:
    seen: list[Any] = []

    async def factory(body: Any, scope: Any) -> Any:
        seen.append(body)
        return ai_message_stream(ai_text_stream(["ok"]))

    app = ai_sdk_endpoint(factory)
    asyncio.run(_collect(app, _scope("GET", "/api/chat")))
    assert seen == [None]


def test_endpoint_rejects_bad_method() -> None:
    app = ai_sdk_endpoint(_chat_factory())
    sent = asyncio.run(_collect(app, _scope("PUT", "/api/chat")))
    start = sent[0]
    assert start["status"] == 405
    assert json.loads(_bodies(sent)[0]) == {"error": "Method PUT is not supported; use GET or POST"}


def test_endpoint_rejects_non_json_body() -> None:
    app = ai_sdk_endpoint(_chat_factory())
    sent = asyncio.run(_collect(app, _scope("POST", "/api/chat"), body=b"definitely not json"))
    assert sent[0]["status"] == 400
    assert json.loads(_bodies(sent)[0]) == {"error": "Request body must be JSON"}


def test_endpoint_body_size_limit() -> None:
    app = ai_sdk_endpoint(_chat_factory(), max_body_size=8)
    sent = asyncio.run(_collect(app, _scope("POST", "/api/chat"), body=b'{"messages": []}'))
    assert sent[0]["status"] == 413


def test_endpoint_factory_error_is_json_500() -> None:
    async def factory(body: Any, scope: Any) -> Any:
        raise RuntimeError("provider is down")

    app = ai_sdk_endpoint(factory, error_text=lambda error: "internal error")
    sent = asyncio.run(_collect(app, _scope("POST", "/api/chat"), body=b"{}"))
    assert sent[0]["status"] == 500
    assert json.loads(_bodies(sent)[0]) == {"error": "internal error"}


def test_endpoint_mid_stream_error_becomes_error_chunk() -> None:
    async def bad() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "text-start", "id": "x"}
        raise RuntimeError("lost the source")

    async def factory(body: Any, scope: Any) -> Any:
        return bad()

    app = ai_sdk_endpoint(factory)
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat")))
    bodies = _bodies(sent)
    assert bodies[0] == b'data: {"type":"text-start","id":"x"}\n\n'
    assert bodies[1] == b'data: {"type":"error","errorText":"lost the source"}\n\n'
    assert bodies[2] == b"data: [DONE]\n\n"


def test_endpoint_non_object_chunk_becomes_error() -> None:
    async def factory(body: Any, scope: Any) -> Any:
        return ["not-a-chunk"]

    app = ai_sdk_endpoint(factory)
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat")))
    chunks = _data_frames(_bodies(sent))
    assert chunks[0]["type"] == "error"
    assert "non-object chunk" in chunks[0]["errorText"]
    assert _bodies(sent)[-1] == b"data: [DONE]\n\n"


def test_endpoint_unwraps_tracked_chunks() -> None:
    """A generator yielding tracked() still streams payloads via the endpoint."""

    async def factory(body: Any, scope: Any) -> Any:
        async def stream() -> AsyncIterator[Any]:
            index = 0
            async for chunk in ai_message_stream(ai_text_stream(_aiter(["a"]), text_id="t-1")):
                yield tracked(str(index), chunk)
                index += 1

        return stream()

    app = ai_sdk_endpoint(factory)
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat")))
    chunks = _data_frames(_bodies(sent))
    assert [c["type"] for c in chunks] == [
        "start",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]
    # No tRPC-style `id:` lines: event ids are transport metadata the
    # AI SDK protocol has no concept of.
    assert all(b"id: " not in body for body in _bodies(sent))


def test_endpoint_disconnect_cancels_the_generator() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def factory(body: Any, scope: Any) -> Any:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            started.set()
            try:
                yield {"type": "text-start", "id": "x"}
                await asyncio.sleep(60)
                yield {"type": "text-end", "id": "x"}
            finally:
                finished.set()

        return stream()

    app = ai_sdk_endpoint(factory)
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat"), disconnect_after=0.1))
    assert started.is_set()
    assert finished.is_set()
    bodies = _bodies(sent)
    assert bodies == [b'data: {"type":"text-start","id":"x"}\n\n']
    assert not any(b"[DONE]" in body or b"text-end" in body for body in bodies)


def test_endpoint_keep_alive_comments_while_idle() -> None:
    async def factory(body: Any, scope: Any) -> Any:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            await asyncio.sleep(0.15)
            yield {"type": "text-start", "id": "x"}

        return stream()

    app = ai_sdk_endpoint(factory, keep_alive_interval=0.03)
    sent = asyncio.run(_collect(app, _scope("GET", "/api/chat"), disconnect_after=0.2))
    bodies = _bodies(sent)
    assert bodies[0] == b": stream-open\n\n"
    assert b": keep-alive\n\n" in bodies


def test_endpoint_backpressure_does_not_pull_ahead() -> None:
    """The iterator is only pulled once a frame has been accepted by send."""
    pulls: list[int] = []

    async def factory(body: Any, scope: Any) -> Any:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            for index in range(5):
                pulls.append(index)
                yield {"type": "text-delta", "id": "x", "delta": str(index)}

        return stream()

    app = ai_sdk_endpoint(factory)

    async def run() -> None:
        sent: list[dict[str, Any]] = []
        gate = asyncio.Event()

        async def send(message: dict[str, Any]) -> None:
            if message.get("body", b"").startswith(b"data:"):
                await gate.wait()
            sent.append(message)

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}

        task = asyncio.create_task(app(_scope("GET", "/api/chat"), receive, send))
        await asyncio.sleep(0.05)
        assert pulls == [0]
        gate.set()
        gate.clear()
        await asyncio.sleep(0.05)
        assert pulls == [0, 1]
        task.cancel()

    asyncio.run(run())


# --- through the tRPC router ------------------------------------------------


def test_subscription_frames_carry_ui_message_chunks() -> None:
    """Subscription data frames decode to valid UI-message-stream JSON."""
    router = Router()

    @router.subscription
    async def chat() -> AsyncIterator[object]:
        parts = ai_text_stream(_aiter(["a", "b"]), text_id="t-1")
        async for chunk in ai_message_stream(parts, message_id="m-1"):
            yield chunk

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/chat", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert bodies[0] == b"event: connected\ndata: {}\n\n\n"
    chunks = _data_frames(bodies[1:-1])
    assert [c["type"] for c in chunks] == [
        "start",
        "text-start",
        "text-delta",
        "text-delta",
        "text-end",
        "finish",
    ]
    assert chunks[0] == {"type": "start", "messageId": "m-1"}
    assert bodies[-1] == b"event: return\ndata: \n\n\n"


def test_tracked_wraps_ai_chunks_for_resume() -> None:
    """Inside a subscription, tracked() still attaches SSE ids to chunks."""
    router = Router()

    @router.subscription
    async def chat() -> AsyncIterator[object]:
        index = 0
        async for chunk in ai_message_stream(ai_text_stream(_aiter(["a"]), text_id="t-1")):
            yield tracked(str(index), chunk)
            index += 1

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/trpc/chat", query=b"input=%7B%7D")))
    bodies = _bodies(sent)
    assert b"id: 0" in bodies[1]
    chunk_frames = _data_frames(bodies[1:-1])
    assert [c["type"] for c in chunk_frames] == [
        "start",
        "text-start",
        "text-delta",
        "text-end",
        "finish",
    ]
    assert any(b"id: 3" in body for body in bodies)
