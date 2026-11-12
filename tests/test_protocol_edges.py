"""Edge-path tests for the `_core` extension boundary.

Direct `parse_request`/`build_response`/`CallResult`/`SseEncoder`/`Watcher`
calls covering Rust error branches the ASGI adapter normally validates away
before reaching the core, plus the full official error-code table and
watcher failure paths that need a real filesystem backend.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from trpc_rs_py._core import (
    CallResult,
    Limits,
    SseEncoder,
    TrpcProtocolError,
    Watcher,
    build_response,
    parse_request,
)

# The official tRPC v11 table — (code name, data code, HTTP status) —
# mirroring `TRPC_ERROR_CODES_BY_KEY` in `@trpc/server` 11.25.0.
OFFICIAL_CODES: list[tuple[str, int, int]] = [
    ("PARSE_ERROR", -32700, 400),
    ("BAD_REQUEST", -32600, 400),
    ("INTERNAL_SERVER_ERROR", -32603, 500),
    ("NOT_IMPLEMENTED", -32603, 501),
    ("BAD_GATEWAY", -32603, 502),
    ("SERVICE_UNAVAILABLE", -32603, 503),
    ("GATEWAY_TIMEOUT", -32603, 504),
    ("UNAUTHORIZED", -32001, 401),
    ("PAYMENT_REQUIRED", -32002, 402),
    ("FORBIDDEN", -32003, 403),
    ("NOT_FOUND", -32004, 404),
    ("METHOD_NOT_SUPPORTED", -32005, 405),
    ("TIMEOUT", -32008, 408),
    ("CONFLICT", -32009, 409),
    ("PRECONDITION_FAILED", -32012, 412),
    ("PAYLOAD_TOO_LARGE", -32013, 413),
    ("UNSUPPORTED_MEDIA_TYPE", -32015, 415),
    ("UNPROCESSABLE_CONTENT", -32022, 422),
    ("PRECONDITION_REQUIRED", -32028, 428),
    ("TOO_MANY_REQUESTS", -32029, 429),
    ("CLIENT_CLOSED_REQUEST", -32099, 499),
]


@pytest.mark.parametrize(("code_name", "data_code", "http_status"), OFFICIAL_CODES)
def test_every_official_error_code_serializes(
    code_name: str, data_code: int, http_status: int
) -> None:
    result = CallResult.error("failure", code_name, data_code, http_status, code_name)
    status, body = build_response([result], False)
    assert status == http_status
    error = json.loads(body)["error"]
    assert error["code"] == data_code
    assert error["data"]["code"] == code_name
    assert error["data"]["httpStatus"] == http_status


def test_error_scalar_payload_lands_under_data_key() -> None:
    result = CallResult.error("missing", "NOT_FOUND", -32004, 404, "NOT_FOUND", 7)
    status, body = build_response([result], False)
    assert status == 404
    assert json.loads(body)["error"]["data"]["data"] == 7


def test_error_payload_strips_spoofed_official_keys() -> None:
    result = CallResult.error(
        "missing",
        "NOT_FOUND",
        -32004,
        404,
        "NOT_FOUND",
        {"code": "SPOOFED", "httpStatus": 200, "kept": True},
    )
    _, body = build_response([result], False)
    data = json.loads(body)["error"]["data"]
    assert data["code"] == "NOT_FOUND"
    assert data["httpStatus"] == 404
    assert data["kept"] is True


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    [
        ("GET", "", "", None),  # empty procedure path
        ("GET", "x,y", "", None),  # multi-path without the batch flag
        ("GET", "a,,b", "batch=1&input=%7B%7D", None),  # empty batch segment
        ("GET", "x", "", b"{}"),  # GET must not carry a body
        ("GET", "x", "input=%7", None),  # truncated percent escape
        ("GET", "x", "input=%zz", None),  # non-hex escape digits
        ("POST", "a,b", "batch=1", None),  # batched POST requires a body
        ("POST", "a,b", "batch=1", b"[1,2]"),  # batch input must be an object
        ("POST", "a,b", "batch=1", b'{"x":1}'),  # batch keys must be numeric
        ("POST", "a,b", "batch=1", b'{"5":1}'),  # batch index outside call count
    ],
)
def test_parse_rejects_malformed_requests(
    method: str, path: str, query: str, body: bytes | None
) -> None:
    with pytest.raises(TrpcProtocolError) as caught:
        parse_request(method, path, query, body, Limits())
    assert caught.value.code == "BAD_REQUEST"


def test_lowercase_percent_escapes_decode() -> None:
    parsed = parse_request("GET", "x", "input=%7b%22x%22%3a1%7d", None, Limits())
    assert parsed.calls[0].input == {"x": 1}


def test_encoded_get_input_over_limit_is_413() -> None:
    oversized = "%22" + "9" * 3000 + "%22"
    with pytest.raises(TrpcProtocolError) as caught:
        parse_request("GET", "x", f"input={oversized}", None, Limits(max_body_size=64))
    assert caught.value.code == "PAYLOAD_TOO_LARGE"
    assert caught.value.http_status == 413


def test_deeply_nested_array_input_is_bad_request() -> None:
    with pytest.raises(TrpcProtocolError) as caught:
        parse_request("POST", "x", "", b"[[[1]]]", Limits(max_json_depth=1))
    assert caught.value.code == "BAD_REQUEST"


def test_float_input_and_integer_boundary_outputs() -> None:
    parsed = parse_request("POST", "x", "", b'{"x":1.5}', Limits())
    assert parsed.calls[0].input == {"x": 1.5}
    # u64::MAX still round-trips through CallResult.ok.
    _, body = build_response([CallResult.ok(2**64 - 1)], False)
    assert json.loads(body)["result"]["data"] == 2**64 - 1
    _, body = build_response([CallResult.ok(1.5)], False)
    assert json.loads(body)["result"]["data"] == 1.5


def test_build_response_empty_single_is_bad_request() -> None:
    status, body = build_response([], False)
    assert status == 400
    assert json.loads(body)["error"]["data"]["code"] == "BAD_REQUEST"


def test_batch_status_prefers_server_error() -> None:
    status, body = build_response(
        [
            CallResult.error("missing", "NOT_FOUND", -32004, 404, "NOT_FOUND"),
            CallResult.ok(1),
            CallResult.error("boom", "INTERNAL_SERVER_ERROR", -32603, 500, "INTERNAL_SERVER_ERROR"),
        ],
        True,
    )
    assert status == 500
    envelopes = json.loads(body)
    assert envelopes[0]["error"]["data"]["code"] == "NOT_FOUND"
    assert envelopes[1]["result"]["data"] == 1
    assert envelopes[2]["error"]["data"]["code"] == "INTERNAL_SERVER_ERROR"


def test_sse_data_rejects_frame_splitting_id() -> None:
    encoder = SseEncoder()
    with pytest.raises(TrpcProtocolError):
        encoder.data({"x": 1}, id="bad\nid")


def test_sse_serialized_error_accepts_dict_and_scalar_payloads() -> None:
    frame = SseEncoder.serialized_error("gone", "NOT_FOUND", {"hint": "check id"})
    text = frame.decode()
    assert text.startswith("event: serialized-error\n")
    assert '"hint":"check id"' in text
    scalar = SseEncoder.serialized_error("gone", "NOT_FOUND", 7).decode()
    assert '"data":7' in scalar


def test_get_input_that_decodes_to_invalid_json_is_rejected() -> None:
    # "%25" decodes to a bare '%', which is not JSON — the error propagates
    # from input_value, not the percent decoder.
    with pytest.raises(TrpcProtocolError):
        parse_request("GET", "x", "input=%25", None, Limits())


def test_untracked_send_after_tracked_restores_id() -> None:
    encoder = SseEncoder()
    encoder.data({"x": 1}, id="7")
    frame = encoder.data({"y": 2}).decode()
    assert "event: restore" in frame
    assert "id: 7" in frame


def test_error_payload_rejects_unencodable_values() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value type"):
        SseEncoder.serialized_error("gone", "NOT_FOUND", object())
    with pytest.raises(TypeError, match="unsupported JSON value type"):
        CallResult.error("gone", "NOT_FOUND", -32004, 404, "NOT_FOUND", object())


def test_call_result_value_conversion_edges() -> None:
    # u64-range ints convert; ints past u64 and non-finite floats do not.
    assert isinstance(CallResult.ok(2**63 + 5), CallResult)
    with pytest.raises(ValueError, match="outside the JSON integer range"):
        CallResult.ok(2**100)
    with pytest.raises(ValueError, match="NaN or infinity"):
        CallResult.ok(float("nan"))
    with pytest.raises(ValueError, match="NaN or infinity"):
        CallResult.ok(float("inf"))
    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        CallResult.ok({1: "x"})
    with pytest.raises(TypeError, match="unsupported JSON value type"):
        CallResult.ok({"k": object()})


def test_call_result_rejects_excessive_depth() -> None:
    nested: Any = []
    for _ in range(130):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting exceeds conversion limit"):
        CallResult.ok(nested)


def test_parse_input_at_serde_depth_limit() -> None:
    # serde_json's recursion guard (128) fires below the value-conversion
    # depth bound, so deep inputs surface as protocol errors.
    body = b"[" * 200 + b"0" + b"]" * 200
    with pytest.raises(TrpcProtocolError, match="nesting depth"):
        parse_request("POST", "x", "", body, Limits(max_json_depth=512))


def test_watcher_debounce_cap_returns_under_sustained_writes(tmp_path: Path) -> None:
    """Sustained writes faster than `debounce_ms` still terminate."""
    watcher = Watcher([str(tmp_path)])
    for _ in range(4):  # drain startup noise
        watcher.next(timeout_ms=300)

    stop = threading.Event()

    def spam_writes() -> None:
        counter = 0
        while not stop.is_set():
            (tmp_path / "x.py").write_text(str(counter))
            counter += 1
            time.sleep(0.01)

    writer = threading.Thread(target=spam_writes, daemon=True)
    writer.start()
    try:
        batch = watcher.next(timeout_ms=10_000, debounce_ms=400)
    finally:
        stop.set()
        writer.join(timeout=5)
    assert batch, "debounce-capped drain should still report the writes"


def test_watcher_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot watch"):
        Watcher([str(tmp_path / "gone")])


def test_watcher_idle_timeout_returns_none(tmp_path: Path) -> None:
    watcher = Watcher([str(tmp_path)])
    # Drain backend startup noise (pytest's own tmp-dir churn can arrive
    # first); a quiet watcher must then report None inside the deadline.
    deadline = time.monotonic() + 15
    while True:
        batch = watcher.next(timeout_ms=1000)
        if batch is None:
            return
        assert time.monotonic() < deadline, "watcher startup events never settled"


def test_watcher_burst_coalesces_within_debounce_cap(tmp_path: Path) -> None:
    watcher = Watcher([str(tmp_path)])
    stop = threading.Event()

    def write_continuously() -> None:
        counter = 0
        while not stop.is_set():
            (tmp_path / f"f{counter % 8}.py").write_text(f"x = {counter}")
            counter += 1

    writer = threading.Thread(target=write_continuously, daemon=True)
    writer.start()
    try:
        # Events keep arriving past the 2s debounce cap: the capped window
        # still yields a single batch instead of starving forever.
        batch = watcher.next(timeout_ms=15000, debounce_ms=100)
    finally:
        stop.set()
        writer.join(timeout=5)
    assert batch
    assert any(path.endswith(".py") for path in batch)


def test_watcher_reports_file_removal(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "gone.py"
    target.write_text("x = 1")
    watcher = Watcher([str(watched)])
    target.unlink()
    # A remove event is relevant like create/modify; drain startup noise
    # until the removal batch shows up.
    deadline = time.monotonic() + 15
    seen: list[str] = []
    while time.monotonic() < deadline:
        batch = watcher.next(timeout_ms=2000)
        if batch is None:
            continue
        seen += batch
        if any(path.endswith("gone.py") or path.endswith("watched") for path in seen):
            return
    raise AssertionError("watcher did not report the removal")
