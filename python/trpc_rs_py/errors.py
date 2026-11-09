"""tRPC error types mirroring the official tRPC v11 error code table.

The codes, names, and data codes mirror `crates/trpc-core/src/error.rs` so the
Python API and the Rust envelope serializer always agree. Raise `TRPCError`
from a procedure handler to control what the client sees:

Example:
    ```python
    from trpc_rs_py import TRPCError, TRPCErrorCode

    raise TRPCError("no such user", code=TRPCErrorCode.NOT_FOUND)
    ```

Typed errors: subclass `TRPCError`, set the `code` and `data_model` class
attributes, and declare the class on a procedure with `errors=[...]`. The
generated TypeScript exposes a discriminated union over `error.data.code`
(ADR 0009).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, ClassVar, Final, cast

from pydantic import BaseModel

__all__ = [
    "TRPCError",
    "TRPCErrorCode",
    "data_code_for",
    "error_data_payload",
    "http_status_for",
    "validate_error_class",
]


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
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    BAD_GATEWAY = "BAD_GATEWAY"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_SUPPORTED = "METHOD_NOT_SUPPORTED"
    TIMEOUT = "TIMEOUT"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    UNPROCESSABLE_CONTENT = "UNPROCESSABLE_CONTENT"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    CLIENT_CLOSED_REQUEST = "CLIENT_CLOSED_REQUEST"


# Data code per error code, mirroring `ErrorCode::code` in
# crates/trpc-core/src/error.rs, which mirrors `TRPC_ERROR_CODES_BY_KEY` in
# `@trpc/server` v11. Note the `-32603` block shares one data code across
# five distinct codes that differ only by HTTP status — tRPC's own quirk,
# mirrored exactly.
_DATA_CODES: Final[Mapping[TRPCErrorCode, int]] = {
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


def data_code_for(code: TRPCErrorCode) -> int:
    """Return the tRPC data code sent on the wire for `code`.

    This is the single place where error names map to numeric codes; the Rust
    envelope serializer consumes the same table from `error.rs`.

    Args:
        code: One of the official tRPC v11 error codes.

    Returns:
        The negative JSON-RPC-style data code, e.g. `-32004` for `NOT_FOUND`.

    Example:
        ```python
        from trpc_rs_py import TRPCErrorCode, data_code_for

        assert data_code_for(TRPCErrorCode.NOT_FOUND) == -32004
        ```
    """
    return _DATA_CODES[code]


# HTTP status per error code, mirroring `ErrorCode::http_status` in
# crates/trpc-core/src/error.rs (which mirrors the official v11 table).
_HTTP_STATUSES: Final[Mapping[TRPCErrorCode, int]] = {
    TRPCErrorCode.PARSE_ERROR: 400,
    TRPCErrorCode.BAD_REQUEST: 400,
    TRPCErrorCode.INTERNAL_SERVER_ERROR: 500,
    TRPCErrorCode.NOT_IMPLEMENTED: 501,
    TRPCErrorCode.BAD_GATEWAY: 502,
    TRPCErrorCode.SERVICE_UNAVAILABLE: 503,
    TRPCErrorCode.GATEWAY_TIMEOUT: 504,
    TRPCErrorCode.UNAUTHORIZED: 401,
    TRPCErrorCode.PAYMENT_REQUIRED: 402,
    TRPCErrorCode.FORBIDDEN: 403,
    TRPCErrorCode.NOT_FOUND: 404,
    TRPCErrorCode.METHOD_NOT_SUPPORTED: 405,
    TRPCErrorCode.TIMEOUT: 408,
    TRPCErrorCode.CONFLICT: 409,
    TRPCErrorCode.PRECONDITION_FAILED: 412,
    TRPCErrorCode.PAYLOAD_TOO_LARGE: 413,
    TRPCErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    TRPCErrorCode.UNPROCESSABLE_CONTENT: 422,
    TRPCErrorCode.PRECONDITION_REQUIRED: 428,
    TRPCErrorCode.TOO_MANY_REQUESTS: 429,
    TRPCErrorCode.CLIENT_CLOSED_REQUEST: 499,
}


def http_status_for(code: TRPCErrorCode) -> int:
    """Return the HTTP status `code` maps to in the wire envelope.

    Mirrors `ErrorCode::http_status` in `crates/trpc-core/src/error.rs`; used
    by the adapter for the response status and by codegen for the emitted
    `httpStatus` literal in typed error unions.

    Args:
        code: One of the official tRPC v11 error codes.

    Returns:
        The HTTP status, e.g. `404` for `NOT_FOUND`.

    Example:
        ```python
        from trpc_rs_py import TRPCErrorCode
        from trpc_rs_py.errors import http_status_for

        assert http_status_for(TRPCErrorCode.CONFLICT) == 409
        ```
    """
    return _HTTP_STATUSES[code]


def error_data_payload(error: TRPCError) -> Any | None:
    """Extract a `TRPCError`'s typed payload for the wire envelope.

    Pydantic models are dumped to plain JSON mappings; other values pass
    through for the serializer, which merges mappings into `error.data` and
    places non-mapping values under `error.data.data`.

    Args:
        error: The `TRPCError` raised by a handler.

    Returns:
        The JSON-ready payload, or `None` when the error carries no data.

    Example:
        ```python
        from trpc_rs_py.errors import TRPCError, TRPCErrorCode, error_data_payload

        e = TRPCError("x", code=TRPCErrorCode.NOT_FOUND, data={"post_id": 5})
        assert error_data_payload(e) == {"post_id": 5}
        ```
    """
    data = error.data
    if data is None:
        return None
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        return cast(Any, model_dump(mode="json"))
    return data


def validate_error_class(error_type: object) -> type[TRPCError]:
    """Validate a class declared in a procedure's `errors=[...]` list.

    `errors=` is typed `Sequence[type[TRPCError]]`, but callers may pass
    anything at runtime and class attributes may be reassigned between
    decoration and generation — so the contract is re-checked here.

    Args:
        error_type: The candidate error class.

    Returns:
        The same class, narrowed to `type[TRPCError]`.

    Raises:
        TypeError: If `error_type` is not a `TRPCError` subclass, its `code`
            is not a `TRPCErrorCode`, or its `data_model` is not a pydantic
            `BaseModel` subclass or `None`.

    Example:
        ```python
        from trpc_rs_py import TRPCError
        from trpc_rs_py.errors import validate_error_class

        class Missing(TRPCError):
            pass

        assert validate_error_class(Missing) is Missing
        ```
    """
    if not (isinstance(error_type, type) and issubclass(error_type, TRPCError)):
        msg = f"errors=[...] entries must be TRPCError subclasses, got {error_type!r}"
        raise TypeError(msg)
    code = getattr(error_type, "code", None)
    if not isinstance(code, TRPCErrorCode):
        msg = f"errors=[...] entry {error_type.__name__}.code must be a TRPCErrorCode, got {code!r}"
        raise TypeError(msg)
    model = getattr(error_type, "data_model", None)
    if model is not None and not (isinstance(model, type) and issubclass(model, BaseModel)):
        msg = (
            f"errors=[...] entry {error_type.__name__}.data_model must be a "
            f"pydantic BaseModel subclass or None, got {model!r}"
        )
        raise TypeError(msg)
    return error_type


class TRPCError(Exception):
    """An error raised inside a procedure, mapped to a tRPC error envelope.

    The adapter catches `TRPCError` and serializes `code` and `message` into
    the wire envelope; anything else becomes `INTERNAL_SERVER_ERROR` so
    internal details never leak in production mode.

    Subclass to declare a *typed error*: set `code` and `data_model` as class
    attributes, then list the class in a procedure's `errors=[...]`. When
    raised with `data` set to a Pydantic model, its fields merge into
    `error.data` on the wire (reserved keys `code`/`httpStatus`/`path`/
    `stack` cannot be overridden) and the generated TypeScript exposes the
    fields in a discriminated union.

    Attributes:
        message: Human-readable error description sent to the client.
        code: The tRPC error code for this error; subclass class attribute
            or the constructor keyword wins.
        data_model: Class-level Pydantic model describing the typed `data`
            payload; `None` on the base class and untyped errors.
        data: Optional structured payload attached to the error. A
            `BaseModel` (or plain mapping) merges into `error.data`; any
            other JSON value lands under `error.data.data`.

    Example:
        ```python
        from pydantic import BaseModel

        from trpc_rs_py import TRPCError, TRPCErrorCode


        class PostRef(BaseModel):
            post_id: int


        class PostNotFound(TRPCError):
            code = TRPCErrorCode.NOT_FOUND
            data_model = PostRef


        error = PostNotFound("missing", data=PostRef(post_id=5))
        assert error.code is TRPCErrorCode.NOT_FOUND
        assert error.data_code == -32004
        ```
    """

    code: TRPCErrorCode = TRPCErrorCode.INTERNAL_SERVER_ERROR
    data_model: ClassVar[Any] = None

    def __init__(
        self, message: str, *, code: TRPCErrorCode | None = None, data: Any | None = None
    ) -> None:
        """Create a `TRPCError`.

        Args:
            message: Human-readable description; sent to the client as-is.
            code: Keyword-only tRPC error code; falls back to the class
                attribute (`INTERNAL_SERVER_ERROR` on the base class).
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
        self.code = code if code is not None else type(self).code
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
