"""tRPC error types mirroring the official tRPC v11 error code table.

The codes, names, and data codes mirror `crates/trpc-core/src/error.rs` so the
Python API and the Rust envelope serializer always agree. Raise `TRPCError`
from a procedure handler to control what the client sees:

Example:
    ```python
    from trpc_rs_py import TRPCError, TRPCErrorCode

    raise TRPCError("no such user", code=TRPCErrorCode.NOT_FOUND)
    ```

Typed error classes with structured data land in 0.4.0; for now `data` is an
opaque payload carried on the exception.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Final

__all__ = ["TRPCError", "TRPCErrorCode", "data_code_for"]


class TRPCErrorCode(Enum):
    """Official tRPC v11 error codes, named exactly as the client expects them.

    The string value is the `SCREAMING_SNAKE_CASE` name sent in the JSON error
    envelope; the numeric data code for each member lives in `_DATA_CODES`
    below and mirrors `crates/trpc-core/src/error.rs`.

    Example:
        ```python
        from trpc_rs_py import TRPCErrorCode

        assert TRPCErrorCode.NOT_FOUND.value == "NOT_FOUND"
        ```
    """

    PARSE_ERROR = "PARSE_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    METHOD_NOT_SUPPORTED = "METHOD_NOT_SUPPORTED"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    CLIENT_CLOSED_REQUEST = "CLIENT_CLOSED_REQUEST"


# Data code per error code, mirroring `ErrorCode::code` in
# crates/trpc-core/src/error.rs (which mirrors the official tRPC v11 table).
# Note: tRPC maps both PRECONDITION_FAILED and CLIENT_CLOSED_REQUEST to the
# same data code -32012; that is tRPC's own quirk and we mirror it exactly.
_DATA_CODES: Final[Mapping[TRPCErrorCode, int]] = {
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


def data_code_for(code: TRPCErrorCode) -> int:
    """Return the tRPC data code sent on the wire for `code`.

    This is the single place where error names map to numeric codes; the Rust
    envelope serializer consumes the same table from `error.rs`.

    Args:
        code: One of the twelve official tRPC v11 error codes.

    Returns:
        The negative JSON-RPC-style data code, e.g. `-32004` for `NOT_FOUND`.

    Example:
        ```python
        from trpc_rs_py import TRPCErrorCode, data_code_for

        assert data_code_for(TRPCErrorCode.NOT_FOUND) == -32004
        ```
    """
    return _DATA_CODES[code]


class TRPCError(Exception):
    """An error raised inside a procedure, mapped to a tRPC error envelope.

    The adapter catches `TRPCError` and serializes `code` and `message` into
    the wire envelope; anything else becomes `INTERNAL_SERVER_ERROR` so
    internal details never leak in production mode.

    Attributes:
        message: Human-readable error description sent to the client.
        code: The tRPC error code for this error.
        data: Optional structured payload attached to the error. Opaque for
            now; typed error data lands in 0.4.0.

    Example:
        ```python
        from trpc_rs_py import TRPCError, TRPCErrorCode

        error = TRPCError("no such user", code=TRPCErrorCode.NOT_FOUND)
        assert error.code is TRPCErrorCode.NOT_FOUND
        assert error.data_code == -32004
        ```
    """

    def __init__(self, message: str, *, code: TRPCErrorCode, data: Any | None = None) -> None:
        """Create a `TRPCError`.

        Args:
            message: Human-readable description; sent to the client as-is.
            code: Keyword-only tRPC error code.
            data: Optional structured payload; `None` when not provided.

        Example:
            ```python
            from trpc_rs_py import TRPCError, TRPCErrorCode

            err = TRPCError("denied", code=TRPCErrorCode.FORBIDDEN, data={"role": "guest"})
            assert err.data == {"role": "guest"}
            ```
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data

    @property
    def data_code(self) -> int:
        """The numeric tRPC data code for this error's `code`.

        Example:
            ```python
            from trpc_rs_py import TRPCError, TRPCErrorCode

            assert TRPCError("x", code=TRPCErrorCode.TIMEOUT).data_code == -32008
            ```
        """
        return data_code_for(self.code)
