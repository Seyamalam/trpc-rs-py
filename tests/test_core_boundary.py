"""Tests for the one-call PyO3 request/response boundary."""

import json

import pytest

from trpc_rs_py._core import (
    CallResult,
    Limits,
    TrpcProtocolError,
    build_response,
    parse_request,
)


def test_parse_get_single_and_batch() -> None:
    single = parse_request("get", "post.list", "input=%7B%22limit%22%3A2%7D", None, Limits())
    assert single.batched is False
    assert single.calls[0].path == "post.list"
    assert single.calls[0].input == {"limit": 2}

    batch = parse_request(
        "GET",
        "post.list,post.byId",
        "batch=1&input=%7B%220%22%3A1%2C%221%22%3A2%7D",
        None,
        Limits(),
    )
    assert batch.batched is True
    assert [call.input for call in batch.calls] == [1, 2]


def test_parse_post_single_and_batch() -> None:
    single = parse_request("POST", "post.create", "", b'{"title":"x"}', Limits())
    assert single.calls[0].input == {"title": "x"}
    batch = parse_request(
        "POST", "post.create,post.delete", "batch=1", b'{"0":{"x":1},"1":2}', Limits()
    )
    assert [call.input for call in batch.calls] == [{"x": 1}, 2]


@pytest.mark.parametrize(
    ("path", "query", "body", "limits", "code", "data_code", "status"),
    [
        ("x", "", b"12345", Limits(max_body_size=4), "PAYLOAD_TOO_LARGE", -32007, 413),
        (
            "a,b",
            "batch=1&input=%7B%220%22%3A1%2C%221%22%3A2%7D",
            None,
            Limits(max_batch_size=1),
            "PAYLOAD_TOO_LARGE",
            -32007,
            413,
        ),
        ("x", "", b'{"a":{"b":1}}', Limits(max_json_depth=1), "BAD_REQUEST", -33600, 400),
    ],
)
def test_protocol_error_attributes(
    path: str,
    query: str,
    body: bytes | None,
    limits: Limits,
    code: str,
    data_code: int,
    status: int,
) -> None:
    with pytest.raises(TrpcProtocolError) as caught:
        parse_request("POST" if body is not None else "GET", path, query, body, limits)
    assert caught.value.code == code
    assert caught.value.data_code == data_code
    assert caught.value.http_status == status
    assert caught.value.message


def test_build_response_single_success_and_error() -> None:
    status, body = build_response([CallResult.ok({"id": 1})], False)
    assert status == 200
    assert json.loads(body) == {"result": {"data": {"id": 1}}}

    error = CallResult.error("missing", "NOT_FOUND", -32004, 404, "NOT_FOUND")
    status, body = build_response([error], False)
    assert status == 404
    assert json.loads(body)["error"]["json"]["data"]["code"] == "NOT_FOUND"


def test_build_response_mixed_batch_and_validates_error_mapping() -> None:
    status, body = build_response(
        [CallResult.ok(1), CallResult.error("missing", "NOT_FOUND", -32004, 404, "NOT_FOUND")], True
    )
    assert status == 404
    assert json.loads(body)[0] == {"result": {"data": 1}}
    assert json.loads(body)[1]["error"]["json"]["code"] == -32004
    with pytest.raises(ValueError, match="do not match"):
        CallResult.error("missing", "NOT_FOUND", -32001, 404, "NOT_FOUND")


def test_json_values_round_trip() -> None:
    values = [{"a": [None, True, False, 123, -2, 1.25, "text"]}, None, "", 0, False]
    for index, value in enumerate(values):
        parsed = parse_request("POST", "echo", "", json.dumps(value).encode(), Limits())
        assert parsed.calls[0].input == value, index


def test_unsupported_http_method_is_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported HTTP method: PUT"):
        parse_request("PUT", "x", "", None, Limits())


def test_unsupported_response_value_has_type_error() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value type: set"):
        CallResult.ok({1, 2})


def test_oversized_json_integer_input_is_float_and_output_int_is_rejected() -> None:
    parsed = parse_request("POST", "echo", "", b'{"x":99999999999999999999999}', Limits())
    assert parsed.calls[0].input == {"x": 1e23}
    with pytest.raises(ValueError, match="outside the JSON integer range"):
        CallResult.ok(99999999999999999999999)


def test_call_result_rejects_unknown_official_code_name() -> None:
    with pytest.raises(ValueError, match="unsupported tRPC error code: UNKNOWN"):
        CallResult.error("bad", "UNKNOWN", 0, 0, "UNKNOWN")


def test_deep_json_conversion_guard_in_both_directions() -> None:
    accepted = 0
    value: object = None
    for _ in range(128):
        value = [value]
        accepted += 1
    result = CallResult.ok(value)
    _, body = build_response([result], False)
    decoded: object = json.loads(body)["result"]["data"]
    for _ in range(accepted):
        assert isinstance(decoded, list)
        decoded = decoded[0]
    assert decoded is None

    too_deep: object = None
    for _ in range(129):
        too_deep = [too_deep]
    with pytest.raises(ValueError, match="conversion limit of 128"):
        CallResult.ok(too_deep)

    input_value: object = None
    for _ in range(127):
        input_value = [input_value]
    parsed = parse_request(
        "POST", "echo", "", json.dumps(input_value).encode(), Limits(max_json_depth=200)
    )
    assert parsed.calls[0].input == input_value
    with pytest.raises(TrpcProtocolError):
        parse_request("POST", "echo", "", json.dumps(too_deep).encode(), Limits(max_json_depth=200))


def test_output_dict_requires_string_keys() -> None:
    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        CallResult.ok({"outer": {1: "value"}})


def test_limits_defaults_match_protocol_defaults() -> None:
    limits = Limits()
    assert limits.max_body_size == 1_048_576
    assert limits.max_batch_size == 100
    assert limits.max_json_depth == 32


def test_missing_get_and_post_inputs_are_none() -> None:
    get = parse_request("GET", "query", "", None, Limits())
    post = parse_request("POST", "mutation", "", None, Limits())
    assert get.calls[0].input is None
    assert post.calls[0].input is None
