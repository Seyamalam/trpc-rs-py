"""Unit tests for TRPCError and the tRPC v11 error code table."""

from __future__ import annotations

import pytest

from trpc_rs_py import TRPCError, TRPCErrorCode
from trpc_rs_py.errors import data_code_for

# The official tRPC v11 data codes, mirroring crates/trpc-core/src/error.rs.
EXPECTED_DATA_CODES: dict[TRPCErrorCode, int] = {
    TRPCErrorCode.PARSE_ERROR: -32700,
    TRPCErrorCode.BAD_REQUEST: -33600,
    TRPCErrorCode.INTERNAL_SERVER_ERROR: -32603,
    TRPCErrorCode.NOT_FOUND: -32004,
    TRPCErrorCode.UNAUTHORIZED: -32001,
    TRPCErrorCode.FORBIDDEN: -32003,
    TRPCErrorCode.METHOD_NOT_SUPPORTED: -32005,
    TRPCErrorCode.TIMEOUT: -32008,
    TRPCErrorCode.CONFLICT: -32009,
    TRPCErrorCode.PAYLOAD_TOO_LARGE: -32007,
    TRPCErrorCode.PRECONDITION_FAILED: -32012,
    TRPCErrorCode.CLIENT_CLOSED_REQUEST: -32012,
}


def test_exactly_twelve_official_codes_exist() -> None:
    assert {member.name for member in TRPCErrorCode} == {code.name for code in EXPECTED_DATA_CODES}


@pytest.mark.parametrize("code", list(TRPCErrorCode))
def test_data_code_mapping_mirrors_error_rs(code: TRPCErrorCode) -> None:
    assert data_code_for(code) == EXPECTED_DATA_CODES[code]
    assert code.value == code.name


def test_precondition_failed_and_client_closed_request_share_data_code() -> None:
    # tRPC's own quirk: both map to -32012. We mirror it exactly.
    assert (
        data_code_for(TRPCErrorCode.PRECONDITION_FAILED)
        == data_code_for(TRPCErrorCode.CLIENT_CLOSED_REQUEST)
        == -32012
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
    assert trpc_rs_py.data_code_for(trpc_rs_py.TRPCErrorCode.CLIENT_CLOSED_REQUEST) == -32012
