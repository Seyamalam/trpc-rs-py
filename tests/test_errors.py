"""Unit tests for TRPCError and the tRPC v11 error code table."""

from __future__ import annotations

import pytest

from trpc_rs_py import TRPCError, TRPCErrorCode
from trpc_rs_py.errors import data_code_for

# The official tRPC v11 data codes (TRPC_ERROR_CODES_BY_KEY in
# @trpc/server@11.25.0), mirroring crates/trpc-core/src/error.rs.
EXPECTED_DATA_CODES: dict[TRPCErrorCode, int] = {
    TRPCErrorCode.PARSE_ERROR: -32700,
    TRPCErrorCode.BAD_REQUEST: -32600,
    TRPCErrorCode.INTERNAL_SERVER_ERROR: -32603,
    TRPCErrorCode.NOT_IMPLEMENTED: -32603,
    TRPCErrorCode.BAD_GATEWAY: -32603,
    TRPCErrorCode.SERVICE_UNAVAILABLE: -32603,
    TRPCErrorCode.GATEWAY_TIMEOUT: -32603,
    TRPCErrorCode.UNAUTHORIZED: -32001,
    TRPCErrorCode.PAYMENT_REQUIRED: -32002,
    TRPCErrorCode.FORBIDDEN: -32003,
    TRPCErrorCode.NOT_FOUND: -32004,
    TRPCErrorCode.METHOD_NOT_SUPPORTED: -32005,
    TRPCErrorCode.TIMEOUT: -32008,
    TRPCErrorCode.CONFLICT: -32009,
    TRPCErrorCode.PRECONDITION_FAILED: -32012,
    TRPCErrorCode.PAYLOAD_TOO_LARGE: -32013,
    TRPCErrorCode.UNSUPPORTED_MEDIA_TYPE: -32015,
    TRPCErrorCode.UNPROCESSABLE_CONTENT: -32022,
    TRPCErrorCode.PRECONDITION_REQUIRED: -32028,
    TRPCErrorCode.TOO_MANY_REQUESTS: -32029,
    TRPCErrorCode.CLIENT_CLOSED_REQUEST: -32099,
}


def test_all_official_codes_exist() -> None:
    assert {member.name for member in TRPCErrorCode} == {code.name for code in EXPECTED_DATA_CODES}
    assert len(TRPCErrorCode) == 21


@pytest.mark.parametrize("code", list(TRPCErrorCode))
def test_data_code_mapping_mirrors_error_rs(code: TRPCErrorCode) -> None:
    assert data_code_for(code) == EXPECTED_DATA_CODES[code]
    assert code.value == code.name


def test_internal_server_error_block_shares_one_data_code() -> None:
    # tRPC's own quirk: five distinct codes share the -32603 data code and
    # differ only by HTTP status. We mirror it exactly.
    assert (
        data_code_for(TRPCErrorCode.INTERNAL_SERVER_ERROR)
        == data_code_for(TRPCErrorCode.NOT_IMPLEMENTED)
        == data_code_for(TRPCErrorCode.BAD_GATEWAY)
        == data_code_for(TRPCErrorCode.SERVICE_UNAVAILABLE)
        == data_code_for(TRPCErrorCode.GATEWAY_TIMEOUT)
        == -32603
    )


def test_error_attributes() -> None:
    error = TRPCError("no such user", code=TRPCErrorCode.NOT_FOUND, data={"id": 7})
    assert error.message == "no such user"
    assert error.code is TRPCErrorCode.NOT_FOUND
    assert error.data == {"id": 7}
    assert error.data_code == -32004


def test_data_defaults_to_none() -> None:
    error = TRPCError("boom", code=TRPCErrorCode.INTERNAL_SERVER_ERROR)
    assert error.data is None
    assert error.data_code == -32603


def test_error_is_an_exception_and_message_round_trips() -> None:
    error = TRPCError("thing broke", code=TRPCErrorCode.CONFLICT)
    assert isinstance(error, Exception)
    assert str(error) == "thing broke"

    with pytest.raises(TRPCError, match="thing broke") as caught:
        raise error
    assert caught.value is error
    assert caught.value.message == "thing broke"
    assert caught.value.code is TRPCErrorCode.CONFLICT


def test_error_code_values_are_the_official_names() -> None:
    assert TRPCErrorCode.PARSE_ERROR.value == "PARSE_ERROR"
    assert TRPCErrorCode.CLIENT_CLOSED_REQUEST.value == "CLIENT_CLOSED_REQUEST"


def test_data_code_for_is_publicly_importable() -> None:
    """`data_code_for` is part of the public API (review round 1)."""
    import trpc_rs_py

    assert trpc_rs_py.data_code_for(trpc_rs_py.TRPCErrorCode.NOT_FOUND) == -32004
    assert trpc_rs_py.data_code_for(trpc_rs_py.TRPCErrorCode.CLIENT_CLOSED_REQUEST) == -32099
