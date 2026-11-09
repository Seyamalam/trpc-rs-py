"""Pure-ASGI adapter: serve a tRPC `Router` from any ASGI app.

No framework is imported here — the adapter speaks raw ASGI
(`scope`/`receive`/`send` dicts), so it works with FastAPI, Starlette, and any
other ASGI server or framework. Requests are parsed and responses serialized
in one call each by the Rust core (`trpc_rs_py._core`).

Two usage styles are supported:

* **Mounted as a sub-app** (`mount`): attach the returned app under a path
  prefix with `app.mount("/trpc", mount(router))`. The adapter tolerates both
  Starlette mounting conventions: scopes where the prefix was already stripped
  (procedure path in `scope["path"]`, optionally with a leading slash) and
  scopes that still carry the full original path (the prefix is removed here).
* **Attached as middleware** (`attach`): wrap an existing ASGI app; requests
  whose path starts with the configured prefix are served by the router and
  everything else — including `websocket` and `lifespan` scopes — is forwarded
  to the wrapped app untouched.

Lifespan and websocket decisions: `mount` handles `lifespan` scopes itself
(accepting startup/shutdown, since a standalone adapter has nothing to forward
to) and closes `websocket` connections immediately with code 1008; `attach`
forwards both to the wrapped app.

Production error policy: when `production=True`, the message of a
handler-raised `TRPCError` passes through to the client except when its code
is `INTERNAL_SERVER_ERROR`, in which case the message is replaced with the
fixed `"Internal server error"`. The other codes are deliberate client-facing
API errors raised by application code (for example `"User not found"`), so
their messages are safe to expose; `INTERNAL_SERVER_ERROR` wraps unexpected
bugs and may contain internal details. Exceptions that are not `TRPCError`
are always replaced with the fixed message in production mode (their `repr`
is sent only in development mode). When `production=False`, every message
passes through unchanged.

Example:
    ```python
    from fastapi import FastAPI

    from trpc_rs_py import Router, mount

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"

    app = FastAPI()
    app.mount("/trpc", mount(router))
    # The official tRPC v11 client can now call greeting at /trpc/greeting.
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from typing import Any, TypeAlias, cast

from ._core import (
    CallResult,
    Limits,
    SseEncoder,
    TrpcProtocolError,
    build_response,
    parse_request,
)
from .errors import TRPCError, TRPCErrorCode, data_code_for
from .router import Procedure, ProcedureKind, Router
from .streaming import Tracked
from .validation import (
    InputValidator,
    OutputValidator,
    build_input_validator,
    build_output_validator,
)

__all__ = ["ASGIApp", "Message", "Receive", "Scope", "Send", "attach", "mount"]

# Standard ASGI type aliases (https://asgi.readthedocs.io/en/latest/specs/main.html).
# The `Any` value type is inherent to the ASGI wire contract, not a shortcut.
Scope: TypeAlias = MutableMapping[str, Any]
"""The ASGI connection-scope mapping, e.g. `{"type": "http", "path": "/trpc/echo"}`."""

Message: TypeAlias = MutableMapping[str, Any]
"""One ASGI event message, e.g. `{"type": "http.request", "body": b"..."}`."""

Receive: TypeAlias = Callable[[], Awaitable[Message]]
"""ASGI receive callable, e.g. `lambda: asgi_receive()` returning the next event."""

Send: TypeAlias = Callable[[Message], Awaitable[None]]
"""ASGI send callable, e.g. the callable that accepts `http.response.start` events."""

ASGIApp: TypeAlias = Callable[[Scope, Receive, Send], Awaitable[None]]
"""An ASGI application, e.g. a FastAPI/Starlette app or the callable from `mount`."""

# HTTP status for each official tRPC error code, mirroring
# `ErrorCode::http_status` in crates/trpc-core/src/error.rs. The core validates
# that `CallResult.error` statuses match this table.
_HTTP_STATUSES: Mapping[TRPCErrorCode, int] = {
    TRPCErrorCode.PARSE_ERROR: 400,
    TRPCErrorCode.BAD_REQUEST: 400,
    TRPCErrorCode.INTERNAL_SERVER_ERROR: 500,
    TRPCErrorCode.NOT_FOUND: 404,
    TRPCErrorCode.UNAUTHORIZED: 401,
    TRPCErrorCode.FORBIDDEN: 403,
    TRPCErrorCode.METHOD_NOT_SUPPORTED: 405,
    TRPCErrorCode.TIMEOUT: 408,
    TRPCErrorCode.CONFLICT: 409,
    TRPCErrorCode.PAYLOAD_TOO_LARGE: 413,
    TRPCErrorCode.PRECONDITION_FAILED: 412,
    TRPCErrorCode.CLIENT_CLOSED_REQUEST: 499,
}

_PRODUCTION_INTERNAL_MESSAGE = "Internal server error"

# Response headers for subscription streams, mirroring the official
# `sseHeaders` in @trpc/server 11.25.0.
_SSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-type", b"text/event-stream"),
    (b"cache-control", b"no-cache, no-transform"),
    (b"x-accel-buffering", b"no"),
    (b"connection", b"keep-alive"),
)

_DEFAULT_MAX_SSE_EVENT_SIZE = 1024 * 1024


class _BodyTooLarge(Exception):
    """Internal sentinel: the streamed body exceeded the configured limit."""


class _ClientDisconnected(Exception):
    """Internal sentinel: the client disconnected before the body was complete."""


def _error_result(code: TRPCErrorCode, message: str) -> CallResult:
    """Build a `CallResult.error` for `code`, keeping code/status tables in sync.

    Args:
        code: Official tRPC error code.
        message: Message sent to the client. Callers must never interpolate
            raw client input here.

    Returns:
        An error result carrying the code's data code and HTTP status.

    Example:
        ```python
        from trpc_rs_py._asgi import _error_result
        from trpc_rs_py.errors import TRPCErrorCode

        result = _error_result(TRPCErrorCode.NOT_FOUND, "missing")
        ```
    """
    name = code.value
    return CallResult.error(message, name, data_code_for(code), _HTTP_STATUSES[code], name)


def _header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """Return the first header value matching `name` (case-insensitive).

    Args:
        headers: ASGI header pairs (lowercase keys per the ASGI spec).
        name: Header name to find, lowercase bytes.

    Returns:
        The header value, or `None` when absent.

    Example:
        ```python
        from trpc_rs_py._asgi import _header

        assert _header([(b"content-type", b"application/json")], b"content-type") == (
            b"application/json"
        )
        ```
    """
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


def _error_response_body(message: str, code: TRPCErrorCode) -> tuple[int, str]:
    """Build a single (non-batched) error response from the core serializer.

    Args:
        message: Error message for the envelope.
        code: Official tRPC error code.

    Returns:
        The `(http_status, body)` pair produced by `build_response`.

    Example:
        ```python
        from trpc_rs_py._asgi import _error_response_body
        from trpc_rs_py.errors import TRPCErrorCode

        status, body = _error_response_body("missing", TRPCErrorCode.NOT_FOUND)
        assert status == 404
        ```
    """
    return build_response([_error_result(code, message)], False)


def _procedure_path(scope: Any, path: str) -> str:
    """Extract the dotted procedure path from an ASGI scope.

    Supports both mounting conventions: a scope whose path still starts with
    the configured prefix (the prefix is removed here) and a scope where the
    framework already stripped it (only a leading slash, if any, is removed).

    Args:
        scope: Raw ASGI scope mapping.
        path: The configured mount path, e.g. `/trpc`.

    Returns:
        The dotted procedure path, e.g. `post.list`.

    Example:
        ```python
        from trpc_rs_py._asgi import _procedure_path

        assert _procedure_path({"path": "/trpc/post.list"}, "/trpc") == "post.list"
        assert _procedure_path({"path": "/post.list"}, "/trpc") == "post.list"
        ```
    """
    request_path: str = scope["path"]
    if request_path == path or request_path.startswith(f"{path}/"):
        return request_path[len(path) :].lstrip("/")
    return request_path.lstrip("/")


async def _read_body(receive: Receive, limit: int) -> bytes:
    """Read the full request body, aborting once it exceeds `limit`.

    Args:
        receive: ASGI receive callable.
        limit: Maximum accepted body size in bytes.

    Returns:
        The accumulated body bytes.

    Raises:
        _BodyTooLarge: The body exceeded `limit` before it was fully read.
        _ClientDisconnected: The client disconnected before the final
            `http.request` message, so the body is incomplete.

    Example:
        ```python
        # Inside an ASGI app:
        # body = await _read_body(receive, limits.max_body_size)
        ```
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        if message["type"] != "http.request":  # pragma: no cover - defensive
            raise _ClientDisconnected
        chunk: bytes = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)
    return b"".join(chunks)


def _input_fits_signature(handler: Callable[..., Any], input_value: Any) -> bool:
    """Check parsed JSON input against the handler's signature before calling it.

    This pre-check mirrors the invocation contract of `_call_handler` so an
    argument-binding mismatch is reported as `BAD_REQUEST` (a client error)
    while exceptions raised *inside* the handler stay on the handler-failure
    path. Handlers whose signature cannot be introspected (for example some
    builtins) skip the check and rely on runtime errors instead.

    Args:
        handler: The procedure handler that would be invoked.
        input_value: Decoded JSON input from the core parser (`None` when
            absent or null).

    Returns:
        `True` when the planned invocation binds cleanly against the
        handler's signature (or the check was skipped).

    Example:
        ```python
        from trpc_rs_py._asgi import _input_fits_signature

        def echo(message: str) -> str:
            return message

        assert _input_fits_signature(echo, {"message": "hi"})
        assert not _input_fits_signature(echo, {"wrong": 1})
        ```
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        # Un-introspectable callable: let the actual invocation decide.
        return True
    try:
        if input_value is None:
            signature.bind()
        elif isinstance(input_value, dict):
            signature.bind(**input_value)
        else:
            signature.bind(input_value)
    except TypeError:
        return False
    return True


def _call_handler(procedure: Procedure, input_value: Any) -> Any:
    """Invoke a procedure handler with the parsed JSON input.

    The invocation contract: missing/null input calls the handler with no
    arguments, a JSON object is unpacked as keyword arguments, and any other
    JSON value is passed as the single positional argument.

    Args:
        procedure: The registered procedure.
        input_value: Decoded JSON input from the core parser (`None` when
            absent or null).

    Returns:
        Whatever the handler returns; must be JSON-ready.

    Raises:
        TypeError: The input does not fit the handler's signature.

    Example:
        ```python
        from trpc_rs_py import Procedure, ProcedureKind
        from trpc_rs_py._asgi import _call_handler

        def echo(message: str) -> str:
            return message

        proc = Procedure(path="echo", kind=ProcedureKind.QUERY, handler=echo)
        assert _call_handler(proc, {"message": "hi"}) == "hi"
        ```
    """
    handler = procedure.handler
    if input_value is None:
        return handler()
    if isinstance(input_value, dict):
        return handler(**input_value)
    return handler(input_value)


async def _run_call(
    procedure: Procedure,
    input_value: Any,
    production: bool,
    validators: tuple[InputValidator | None, OutputValidator | None] | None = None,
) -> CallResult:
    """Run one procedure and wrap its outcome in a `CallResult`.

    Sync handlers run in a worker thread via `asyncio.to_thread` so the event
    loop is never blocked; coroutine handlers are awaited directly. Input that
    does not fit the handler's signature is rejected *before* invocation as
    `BAD_REQUEST` with a fixed message. Errors raised during handler execution
    are mapped separately: `TRPCError` keeps its code, and anything else
    becomes `INTERNAL_SERVER_ERROR` — leaking the exception repr only in
    development mode.

    Production error policy: in production mode a handler-raised `TRPCError`'s
    message passes through to the client, except when its code is
    `INTERNAL_SERVER_ERROR`, which is replaced with the fixed `"Internal
    server error"` message (unexpected bugs may carry internal details; the
    other codes are deliberate client-facing API errors). In development mode
    every `TRPCError` message passes through unchanged.

    Args:
        procedure: The registered procedure to run.
        input_value: Decoded JSON input for the call.
        production: When `True`, internal failures never leak details.
        validators: Optional pair of prebuilt input and output validators.

    Returns:
        The success or error result for this call.

    Example:
        ```python
        from trpc_rs_py import Procedure, ProcedureKind
        from trpc_rs_py._asgi import _run_call

        def hello() -> str:
            return "hi"

        proc = Procedure(path="hello", kind=ProcedureKind.QUERY, handler=hello)
        result = await _run_call(proc, None, production=False)
        ```
    """
    path = procedure.path
    input_validator, output_validator = validators or (None, None)
    if input_validator is None and not _input_fits_signature(procedure.handler, input_value):
        # Fixed, non-leaking message: a binding failure is a client error, and
        # the raw error text can quote handler internals or client input.
        return _error_result(TRPCErrorCode.BAD_REQUEST, f"Invalid input for procedure {path}")
    try:
        if input_validator is not None:
            args, kwargs = input_validator(input_value)
            if inspect.iscoroutinefunction(procedure.handler):
                data = await procedure.handler(*args, **kwargs)
            else:
                data = await asyncio.to_thread(procedure.handler, *args, **kwargs)
        elif inspect.iscoroutinefunction(procedure.handler):
            data = await _call_handler(procedure, input_value)
        else:
            data = await asyncio.to_thread(_call_handler, procedure, input_value)
    except TRPCError as error:
        # Every code except INTERNAL_SERVER_ERROR is a deliberate,
        # client-facing API error raised by application code, so its message
        # passes through even in production mode; INTERNAL_SERVER_ERROR wraps
        # unexpected bugs and may contain internals, so it is redacted there.
        message = error.message
        if production and error.code is TRPCErrorCode.INTERNAL_SERVER_ERROR:
            message = _PRODUCTION_INTERNAL_MESSAGE
        return _error_result(error.code, message)
    except Exception as error:  # adapter boundary: every failure becomes an envelope
        message = _PRODUCTION_INTERNAL_MESSAGE if production else repr(error)
        return _error_result(TRPCErrorCode.INTERNAL_SERVER_ERROR, message)
    if output_validator is not None:
        try:
            data = output_validator(data)
        except Exception:
            # Output failures are always internal; do not trust a custom
            # validator exception's code or message at this boundary.
            message = (
                _PRODUCTION_INTERNAL_MESSAGE if production else "Invalid output from procedure"
            )
            return _error_result(TRPCErrorCode.INTERNAL_SERVER_ERROR, message)
    try:
        return CallResult.ok(data)
    except Exception:
        message = _PRODUCTION_INTERNAL_MESSAGE if production else "Invalid output from procedure"
        return _error_result(TRPCErrorCode.INTERNAL_SERVER_ERROR, message)


async def _send_response(send: Send, status: int, body: str) -> None:
    """Send a JSON response with the status and body from the core serializer.

    Args:
        send: ASGI send callable.
        status: HTTP status returned by `build_response`.
        body: JSON envelope text returned by `build_response`.

    Returns:
        `None`. Completes the ASGI response.

    Example:
        ```python
        # Inside an ASGI app:
        # await _send_response(send, 200, '{"result":{"data":1}}')
        ```
    """
    payload = body.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _query_param(query_string: bytes, name: bytes) -> bytes | None:
    """Return the first query parameter value matching `name`, undecoded.

    Args:
        query_string: Raw ASGI query string without the leading `?`.
        name: Parameter name, e.g. `b"lastEventId"`.

    Returns:
        The raw (still percent-encoded) value, or `None` when absent.

    Example:
        ```python
        from trpc_rs_py._asgi import _query_param

        assert _query_param(b"a=1&lastEventId=5", b"lastEventId") == b"5"
        ```
    """
    for part in query_string.split(b"&"):
        key, separator, value = part.partition(b"=")
        if separator and key == name:
            return value
    return None


def _extract_last_event_id(scope: Any) -> bytes | None:
    """Extract the client's resume id in official precedence order.

    Mirrors `getLastEventId` in `@trpc/server` 11.25.0: the `last-event-id`
    header wins, then the `lastEventId` and `Last-Event-Id` query parameters.

    Args:
        scope: Raw ASGI scope for an `http` request.

    Returns:
        The raw last-event-id bytes, or `None` when the request carries none.

    Example:
        ```python
        from trpc_rs_py._asgi import _extract_last_event_id

        scope = {"headers": [(b"last-event-id", b"9")], "query_string": b""}
        assert _extract_last_event_id(scope) == b"9"
        ```
    """
    header = _header(scope.get("headers", []), b"last-event-id")
    if header is not None:
        return header
    query_string: bytes = scope.get("query_string", b"")
    return _query_param(query_string, b"lastEventId") or _query_param(
        query_string, b"Last-Event-Id"
    )


async def _wait_disconnect(receive: Receive) -> None:
    """Block until the ASGI server reports `http.disconnect`.

    Other receive events (for example a stray `http.request` body frame on a
    GET request) are ignored; only the disconnect matters while streaming.

    Args:
        receive: ASGI receive callable.

    Returns:
        `None` once the client has disconnected.
    """
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def _send_sse_body(send: Send, payload: bytes) -> bool:
    """Send one SSE chunk, reporting whether the socket accepted it.

    Args:
        send: ASGI send callable.
        payload: Encoded SSE bytes (empty bytes ends the response body).

    Returns:
        `True` when the chunk was sent; `False` when the transport rejected
        it, which the caller treats as client disconnect.
    """
    try:
        await send({"type": "http.response.body", "body": payload, "more_body": bool(payload)})
    except (ConnectionError, OSError, RuntimeError):
        return False
    return True


async def _close_iterator(iterator: Any) -> None:
    """Best-effort `aclose()` of an async iterator after client disconnect.

    Closing throws `GeneratorExit` into a suspended generator so an in-flight
    `await` (for example an LLM streaming call) stops promptly instead of
    running to completion unseen. Iterators without `aclose` are skipped;
    close failures are deliberately swallowed — the client is already gone.

    Args:
        iterator: The async iterator being closed.

    Returns:
        `None` after the close attempt finishes.
    """
    aclose = getattr(iterator, "aclose", None)
    if aclose is None:
        return
    try:
        await asyncio.wait_for(aclose(), timeout=5.0)
    except Exception:  # client is gone; close failures are irrelevant
        return


def _declares_last_event_id(handler: Callable[..., Any]) -> bool:
    """Return whether the handler signature has a `lastEventId` parameter.

    Args:
        handler: The subscription handler.

    Returns:
        `True` when a parameter literally named `lastEventId` exists; the
        adapter passes the client's resume id through it.

    Example:
        ```python
        from trpc_rs_py._asgi import _declares_last_event_id

        def stream(lastEventId: str | None = None) -> None: ...

        assert _declares_last_event_id(stream)
        ```
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return False
    return "lastEventId" in signature.parameters


async def _serve_subscription(
    scope: Any,
    receive: Receive,
    send: Send,
    *,
    procedure: Procedure,
    input_value: Any,
    last_event_id: bytes | None,
    method: str,
    production: bool,
    validators: tuple[InputValidator | None, OutputValidator | None] | None,
    sse_ping_interval: float | None,
    max_sse_event_size: int,
) -> None:
    """Stream one subscription procedure over SSE in the official v11 format.

    Event order mirrors `@trpc/server` 11.25.0 `sseStreamProducer`: the
    `connected` frame always leads, then one data frame per yielded value
    (tracked events carry `id:`), `serialized-error` for any failure, `return`
    on a clean end, and `ping` frames when `sse_ping_interval` is configured.
    Backpressure is pull-based: one generator item is awaited, encoded, and
    sent before the next is pulled, so a slow client never grows a buffer
    beyond `max_sse_event_size`. On `http.disconnect` the pending
    `__anext__` is cancelled and the iterator is closed, which stops
    in-flight work inside the generator (for example an LLM call).

    Args:
        scope: Raw ASGI scope for an `http` request.
        receive: ASGI receive callable.
        send: ASGI send callable.
        procedure: The subscription procedure to stream.
        input_value: Decoded JSON input (`lastEventId` already removed).
        last_event_id: The client's resume id from header/query param, raw.
        method: HTTP method of the request.
        production: When `True`, internal failures never leak details.
        validators: Optional pair of prebuilt input and output validators.
        sse_ping_interval: Seconds between `ping` frames when the generator
            is idle, or `None` to disable keep-alives (the default).
        max_sse_event_size: Largest single encoded frame in bytes; a yielded
            value serializing beyond it ends the stream with an error.

    Returns:
        `None` when the stream has ended or the client went away.
    """
    path = procedure.path

    async def fail(message: str, code: TRPCErrorCode) -> None:
        """Emit `serialized-error` with the flat v11 error shape, then end."""
        await _send_sse_body(send, SseEncoder.serialized_error(message, code.value))

    try:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": list(_SSE_HEADERS),
            }
        )
    except (ConnectionError, OSError, RuntimeError):
        return
    if not await _send_sse_body(send, SseEncoder.connected()):
        return

    if method.upper() not in ("GET", "POST"):
        await fail(
            f'Procedure "{path}" is a subscription and only supports GET or POST',
            TRPCErrorCode.METHOD_NOT_SUPPORTED,
        )
        return

    # The official client merges `lastEventId` into the input object on
    # reconnect; handlers may instead declare a `lastEventId` parameter and
    # receive it directly — either way the value is protocol metadata, not
    # user input, so it is removed before validation.
    effective_last_event_id = last_event_id
    if isinstance(input_value, dict) and "lastEventId" in input_value:
        input_value = dict(cast("dict[str, Any]", input_value))
        effective_last_event_id = effective_last_event_id or input_value.pop("lastEventId")

    input_validator, output_validator = validators or (None, None)
    try:
        if input_validator is not None:
            args, kwargs = input_validator(input_value)
        else:
            if not _input_fits_signature(procedure.handler, input_value):
                raise TRPCError(
                    f"Invalid input for procedure {path}", code=TRPCErrorCode.BAD_REQUEST
                )
            args, kwargs = (), {}
            if input_value is None:
                pass
            elif isinstance(input_value, dict):
                kwargs = dict(cast("dict[str, Any]", input_value))
            else:
                args = (input_value,)
        if _declares_last_event_id(procedure.handler):
            kwargs["lastEventId"] = (
                effective_last_event_id.decode("utf-8", errors="replace")
                if isinstance(effective_last_event_id, bytes)
                else effective_last_event_id
            )
        result = procedure.handler(*args, **kwargs)
    except TRPCError as error:
        message = error.message
        if production and error.code is TRPCErrorCode.INTERNAL_SERVER_ERROR:
            message = _PRODUCTION_INTERNAL_MESSAGE
        await fail(message, error.code)
        return
    except Exception as error:  # adapter boundary: failures stream as serialized-error
        message = _PRODUCTION_INTERNAL_MESSAGE if production else repr(error)
        await fail(message, TRPCErrorCode.INTERNAL_SERVER_ERROR)
        return

    iterator: Any = None
    if hasattr(result, "__aiter__"):
        iterator = result.__aiter__()
    elif hasattr(result, "__anext__"):
        iterator = result
    if iterator is None or not hasattr(iterator, "__anext__"):
        await fail(
            f"Subscription {path} did not return an observable or a AsyncGenerator",
            TRPCErrorCode.INTERNAL_SERVER_ERROR,
        )
        return

    resume_text: str | None = None
    if effective_last_event_id is not None:
        resume_text = (
            effective_last_event_id.decode("utf-8", errors="replace")
            if isinstance(effective_last_event_id, bytes)
            else str(effective_last_event_id)
        )
    encoder = SseEncoder(resume_text)
    disconnect_waiter = asyncio.ensure_future(_wait_disconnect(receive))
    anext_task: asyncio.Task[Any] = asyncio.ensure_future(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait(
                {anext_task, disconnect_waiter},
                timeout=sse_ping_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_waiter in done:
                anext_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await anext_task
                await _close_iterator(iterator)
                return
            if anext_task not in done:
                # Idle interval elapsed: keep-alive ping, the same pending
                # __anext__ stays in flight — never pull ahead of the client.
                if not await _send_sse_body(send, SseEncoder.ping()):
                    return
                continue
            try:
                value = anext_task.result()
            except StopAsyncIteration:
                await _send_sse_body(send, SseEncoder.close())
                return
            except TRPCError as error:
                message = error.message
                if production and error.code is TRPCErrorCode.INTERNAL_SERVER_ERROR:
                    message = _PRODUCTION_INTERNAL_MESSAGE
                await fail(message, error.code)
                return
            except Exception as error:  # generator raised mid-stream
                message = _PRODUCTION_INTERNAL_MESSAGE if production else repr(error)
                await fail(message, TRPCErrorCode.INTERNAL_SERVER_ERROR)
                return

            event_id: str | None = None
            data: Any = value
            if isinstance(value, Tracked):
                event_id = value.id
                data = cast(Any, value).data
            if output_validator is not None:
                try:
                    data = output_validator(data)
                except Exception:
                    message = (
                        _PRODUCTION_INTERNAL_MESSAGE
                        if production
                        else "Invalid output from procedure"
                    )
                    await fail(message, TRPCErrorCode.INTERNAL_SERVER_ERROR)
                    return
            try:
                frame = encoder.data(data, event_id)
            except Exception as error:
                message = _PRODUCTION_INTERNAL_MESSAGE if production else repr(error)
                await fail(message, TRPCErrorCode.INTERNAL_SERVER_ERROR)
                return
            if len(frame) > max_sse_event_size:
                message = (
                    _PRODUCTION_INTERNAL_MESSAGE
                    if production
                    else f"Event on {path} exceeds the SSE size limit"
                )
                await fail(message, TRPCErrorCode.INTERNAL_SERVER_ERROR)
                return
            if not await _send_sse_body(send, frame):
                return
            anext_task = asyncio.ensure_future(iterator.__anext__())
    finally:
        if not anext_task.done():
            anext_task.cancel()
        disconnect_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await disconnect_waiter
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            await send({"type": "http.response.body", "body": b""})


def _limits_or_default(limits: Limits | None) -> Limits:
    """Return `limits`, or the protocol defaults when `None`.

    Args:
        limits: Caller-provided limits, or `None`.

    Returns:
        A concrete `Limits` instance.

    Example:
        ```python
        from trpc_rs_py._asgi import _limits_or_default
        from trpc_rs_py._core import Limits

        assert _limits_or_default(None).max_body_size == Limits().max_body_size
        ```
    """
    return Limits() if limits is None else limits


async def _serve_trpc(
    scope: Any,
    receive: Receive,
    send: Send,
    *,
    router: Router,
    limits: Limits,
    production: bool,
    procedure_path: str,
    validators: Mapping[str, tuple[InputValidator | None, OutputValidator | None]],
    sse_ping_interval: float | None,
    max_sse_event_size: int,
) -> None:
    """Parse, dispatch, and answer one HTTP request against the router.

    Enforces the body-size limit from the `Content-Length` header before any
    body bytes are read (and additionally guards streamed bodies), accepts
    only `application/json` POST bodies (a missing `Content-Type` header is
    rejected the same as an unsupported media type, but charset parameters
    are lenient), stops without a response when the client disconnects
    mid-body, and maps unsupported HTTP methods to `METHOD_NOT_SUPPORTED`.

    Args:
        scope: Raw ASGI scope for an `http` request.
        receive: ASGI receive callable.
        send: ASGI send callable.
        router: The router serving procedures.
        limits: Configured request limits.
        production: When `True`, internal errors never leak details.
        procedure_path: Dotted procedure path extracted from the URL.
        validators: Validators built once at adapter construction for each
            registered procedure; later registrations use legacy checks.
        sse_ping_interval: Seconds between SSE `ping` frames on idle
            subscription streams, or `None` to disable keep-alives.
        max_sse_event_size: Largest single encoded SSE frame in bytes.

    Returns:
        `None`. The response has been sent when this coroutine finishes.

    Example:
        ```python
        # Inside an ASGI app after prefix handling:
        # await _serve_trpc(scope, receive, send, router=router,
        #                   limits=Limits(), production=False,
        #                   procedure_path="post.list")
        ```
    """
    method: str = scope["method"]

    if method.upper() not in ("GET", "POST"):
        # The official resolves the procedure first and checks the method per
        # call, so a batch reports one envelope per call and a single
        # unbatched subscription streams the failure as `serialized-error`.
        early_query: bytes = scope.get("query_string", b"")
        batched = _query_param(early_query, b"batch") == b"1"
        if not batched:
            if not procedure_path:
                status, body = _error_response_body(
                    "Procedure path is empty", TRPCErrorCode.BAD_REQUEST
                )
                await _send_response(send, status, body)
                return
            if "," in procedure_path:
                status, body = _error_response_body(
                    "Batch flag and procedure paths do not match", TRPCErrorCode.BAD_REQUEST
                )
                await _send_response(send, status, body)
                return
            procedure = router.procedures.get(procedure_path)
            if procedure is not None and procedure.kind is ProcedureKind.SUBSCRIPTION:
                await _serve_subscription(
                    scope,
                    receive,
                    send,
                    procedure=procedure,
                    input_value=None,
                    last_event_id=_extract_last_event_id(scope),
                    method=method,
                    production=production,
                    validators=None,
                    sse_ping_interval=sse_ping_interval,
                    max_sse_event_size=max_sse_event_size,
                )
                return
        bad_method_results = [
            _error_result(TRPCErrorCode.NOT_FOUND, f'No procedure found on path "{call_path}"')
            if (procedure := router.procedures.get(call_path)) is None
            else _error_result(
                TRPCErrorCode.METHOD_NOT_SUPPORTED,
                f'Procedure "{call_path}" is a {procedure.kind.value}'
                " and only supports "
                + ("POST" if procedure.kind is ProcedureKind.MUTATION else "GET or POST"),
            )
            for call_path in procedure_path.split(",")
        ]
        status, body = build_response(bad_method_results, batched)
        await _send_response(send, status, body)
        return

    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])

    # Enforce the body-size limit before reading unbounded data: a declared
    # Content-Length over the limit answers 413 without touching the body.
    declared = _header(headers, b"content-length")
    if declared is not None:
        try:
            content_length = int(declared)
        except ValueError:
            status, body = _error_response_body(
                "Invalid Content-Length header", TRPCErrorCode.BAD_REQUEST
            )
            await _send_response(send, status, body)
            return
        if content_length > limits.max_body_size:
            status, body = _error_response_body(
                "Request body exceeds the configured limit", TRPCErrorCode.PAYLOAD_TOO_LARGE
            )
            await _send_response(send, status, body)
            return

    content_type = _header(headers, b"content-type")
    if method.upper() == "POST":
        media_type = (
            content_type.split(b";", 1)[0].strip().lower() if content_type is not None else None
        )
        if media_type != b"application/json":
            # tRPC v11 clients always send application/json; a missing header
            # is equally invalid, so both cases map to the same envelope. The
            # core's error table maps BAD_REQUEST to HTTP 400.
            status, body = _error_response_body(
                "tRPC requests must use the application/json content type",
                TRPCErrorCode.BAD_REQUEST,
            )
            await _send_response(send, status, body)
            return

    if method.upper() == "GET":
        request_body: bytes | None = None
    else:
        try:
            request_body = await _read_body(receive, limits.max_body_size)
        except _ClientDisconnected:
            # The connection is gone before the body completed; do not
            # dispatch a handler on partial input and do not send a response
            # (per the ASGI spec, simply returning is correct).
            return
        except _BodyTooLarge:
            status, body = _error_response_body(
                "Request body exceeds the configured limit", TRPCErrorCode.PAYLOAD_TOO_LARGE
            )
            await _send_response(send, status, body)
            return

    query_string: bytes = scope.get("query_string", b"")
    try:
        parsed = parse_request(
            method, procedure_path, query_string.decode("latin-1"), request_body, limits
        )
    except TrpcProtocolError as error:
        result = CallResult.error(
            error.message, error.code, error.data_code, error.http_status, error.code
        )
        status, body = build_response([result], False)
        await _send_response(send, status, body)
        return
    except ValueError as error:
        # Defensive: unsupported methods are filtered above; this keeps any
        # future core-level ValueError from escaping as a 500.
        status, body = _error_response_body(str(error), TRPCErrorCode.BAD_REQUEST)
        await _send_response(send, status, body)
        return

    procedures = router.procedures
    # `trpc-accept: application/jsonl` (or an `accept` entry for it) marks an
    # official stream call, which must be batched — the check runs before
    # method and procedure dispatch. JSONL responses themselves are not
    # implemented; batched stream calls still get JSON envelopes.
    trpc_accept = _header(headers, b"trpc-accept")
    accept = _header(headers, b"accept")
    if trpc_accept == b"application/jsonl" or (
        accept is not None and b"application/jsonl" in (part.strip() for part in accept.split(b","))
    ):
        if not parsed.batched:
            status, body = _error_response_body(
                "Streaming requests must be batched (you can do a batch of 1)",
                TRPCErrorCode.BAD_REQUEST,
            )
            await _send_response(send, status, body)
            return
    elif not parsed.batched and len(parsed.calls) == 1:
        call = parsed.calls[0]
        procedure = procedures.get(call.path)
        if procedure is not None and procedure.kind is ProcedureKind.SUBSCRIPTION:
            await _serve_subscription(
                scope,
                receive,
                send,
                procedure=procedure,
                input_value=call.input,
                last_event_id=_extract_last_event_id(scope),
                method=method,
                production=production,
                validators=validators.get(call.path),
                sse_ping_interval=sse_ping_interval,
                max_sse_event_size=max_sse_event_size,
            )
            return

    # Mirror the official request-info rule before any call runs: a batch may
    # never mix procedure kinds (unknown paths contribute no kind). The
    # official error is thrown during request parsing, so it surfaces as one
    # top-level error envelope rather than per-call results.
    seen_kinds: list[ProcedureKind] = []
    for call in parsed.calls:
        procedure = procedures.get(call.path)
        if procedure is not None and procedure.kind not in seen_kinds:
            seen_kinds.append(procedure.kind)
    if len(seen_kinds) > 1:
        type_names = ", ".join(kind.value for kind in seen_kinds)
        status, body = _error_response_body(
            f"Cannot mix procedure types in call: {type_names}",
            TRPCErrorCode.BAD_REQUEST,
        )
        await _send_response(send, status, body)
        return

    results: list[CallResult] = []
    for call in parsed.calls:
        procedure = procedures.get(call.path)
        if procedure is None:
            # call.path has already passed the core's charset validation, so
            # echoing it mirrors the core's own NOT_FOUND messages.
            results.append(
                _error_result(TRPCErrorCode.NOT_FOUND, f'No procedure found on path "{call.path}"')
            )
            continue
        if procedure.kind is ProcedureKind.MUTATION and method.upper() != "POST":
            results.append(
                _error_result(
                    TRPCErrorCode.METHOD_NOT_SUPPORTED,
                    f'Procedure "{call.path}" is a mutation and only supports POST',
                )
            )
            continue
        if procedure.kind is ProcedureKind.SUBSCRIPTION:
            # A subscription reaching this loop is necessarily batched; the
            # official checks the method first, then rejects the batch —
            # both as per-call JSON error envelopes.
            if method.upper() not in ("GET", "POST"):
                results.append(
                    _error_result(
                        TRPCErrorCode.METHOD_NOT_SUPPORTED,
                        f'Procedure "{call.path}" is a subscription and only supports GET or POST',
                    )
                )
            else:
                results.append(
                    _error_result(
                        TRPCErrorCode.BAD_REQUEST,
                        "Cannot batch subscription calls",
                    )
                )
            continue
        results.append(
            await _run_call(procedure, call.input, production, validators.get(call.path))
        )

    status, body = build_response(results, parsed.batched)
    await _send_response(send, status, body)


def mount(
    router: Router,
    *,
    path: str = "/trpc",
    limits: Limits | None = None,
    production: bool = False,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int = _DEFAULT_MAX_SSE_EVENT_SIZE,
) -> ASGIApp:
    """Return a standalone ASGI app serving `router` as tRPC v11 endpoints.

    The returned app is a pure-ASGI callable. Mount it under a path prefix
    (`app.mount("/trpc", ...)`) or serve it directly with an ASGI server; both
    scopes resolve the procedure path after `path`. Non-HTTP scopes:
    `lifespan` is answered locally (startup/shutdown accepted) and
    `websocket` connections are closed immediately with code 1008. Input and
    output validators are built once for procedures registered when `mount`
    is called; procedures added later still dispatch with legacy signature
    checking until a new adapter is constructed. Annotated inputs use
    Pydantic v2's default lax coercion and reject unknown keys. For a ``*args``-only
    procedure, a JSON list is unpacked as positional arguments, a scalar is one
    positional argument, and absent/null input is zero arguments.

    Production error policy (`production=True`): exceptions escaping a
    procedure handler and `TRPCError`s with code `INTERNAL_SERVER_ERROR` are
    sent with the fixed message `"Internal server error"` so internal details
    never leak. `TRPCError`s with any other code are deliberate client-facing
    API errors raised by application code (for example `"User not found"`),
    so their messages pass through unchanged. With `production=False`
    (development, the default), all messages pass through, including the
    `repr` of unexpected exceptions.

    Args:
        router: The router whose procedures are served.
        path: Mount path prefix, used to strip itself when the framework did
            not. Defaults to `/trpc`.
        limits: Request limits (body size, batch size, JSON depth); protocol
            defaults when `None`.
        production: See the production error policy above; `False` (default)
            is for development.
        sse_ping_interval: Seconds between SSE `ping` keep-alive frames while
            a subscription generator is idle, or `None` (default) to disable
            keep-alives — the same default as the official server.
        max_sse_event_size: Largest single encoded SSE frame in bytes before
            the stream ends with an error; defaults to 1 MiB.

    Returns:
        An ASGI application (`Callable[[Scope, Receive, Send], Awaitable[None]]`).

    Example:
        ```python
        from fastapi import FastAPI

        from trpc_rs_py import Router, mount

        router = Router()

        @router.query
        def health() -> bool:
            return True

        app = FastAPI()
        app.mount("/trpc", mount(router))
        ```
    """
    resolved_limits = _limits_or_default(limits)
    # Build validators once for procedures registered at construction. Later
    # registrations use the legacy signature pre-check.
    validators = {
        name: (
            build_input_validator(proc.handler),
            build_output_validator(
                proc.handler, for_subscription=proc.kind is ProcedureKind.SUBSCRIPTION
            ),
        )
        for name, proc in router.procedures.items()
    }

    async def app(scope: Any, receive: Receive, send: Send) -> None:
        """Serve one ASGI request against the router."""
        scope_type: str = scope["type"]
        if scope_type == "http":
            await _serve_trpc(
                scope,
                receive,
                send,
                router=router,
                limits=resolved_limits,
                production=production,
                procedure_path=_procedure_path(scope, path),
                validators=validators,
                sse_ping_interval=sse_ping_interval,
                max_sse_event_size=max_sse_event_size,
            )
        elif scope_type == "websocket":
            # A standalone adapter has nothing to hand a websocket to; close
            # it explicitly with 1008 (policy violation) instead of hanging
            # the connection open.
            await send({"type": "websocket.close", "code": 1008})
        elif scope_type == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    return app


def attach(
    app: ASGIApp,
    router: Router,
    *,
    path: str = "/trpc",
    limits: Limits | None = None,
    production: bool = False,
    sse_ping_interval: float | None = None,
    max_sse_event_size: int = _DEFAULT_MAX_SSE_EVENT_SIZE,
    rest_path: str | None = None,
) -> ASGIApp:
    """Wrap an existing ASGI app so `router` serves the tRPC prefix.

    Requests whose path equals `path` or starts with `path + "/"` are handled
    by the router (the remainder is the procedure path); every other scope —
    including `websocket` and `lifespan` — is forwarded to the wrapped app
    untouched. Validators are built once for procedures registered when
    `attach` is called; procedures added later dispatch with legacy signature
    checking until a new adapter is constructed. Annotated inputs use
    Pydantic v2's default lax coercion and reject unknown keys. For a ``*args``-only
    procedure, a JSON list is unpacked as positional arguments, a scalar is one
    positional argument, and absent/null input is zero arguments. Production
    error policy (`production=True`): exceptions
    escaping a procedure handler and `TRPCError`s with code
    `INTERNAL_SERVER_ERROR` are sent with the fixed message `"Internal server
    error"` so internal details never leak; `TRPCError`s with any other code
    pass through unchanged (deliberate client-facing API errors), and with
    `production=False` (development, the default) all messages pass through,
    including the `repr` of unexpected exceptions.

    Args:
        app: The ASGI app to wrap; non-tRPC requests pass through to it.
        router: The router whose procedures are served.
        path: tRPC endpoint prefix. Defaults to `/trpc`.
        limits: Request limits (body size, batch size, JSON depth); protocol
            defaults when `None`.
        production: See the production error policy above; `False` (default)
            is for development.
        sse_ping_interval: Seconds between SSE `ping` keep-alive frames while
            a subscription generator is idle, or `None` (default) to disable
            keep-alives — the same default as the official server.
        max_sse_event_size: Largest single encoded SSE frame in bytes before
            the stream ends with an error; defaults to 1 MiB.
        rest_path: Optional prefix (e.g. `/api`) under which procedures
            carrying `RestRoute` metadata are served as REST endpoints plus
            `GET <rest_path>/openapi.json` and `GET <rest_path>/docs`
            (docs disabled in production unless explicitly enabled). `None`
            (default) serves tRPC only; use `mount_rest` directly for a
            standalone REST mount.

    Returns:
        A new ASGI application wrapping `app`.

    Example:
        ```python
        from fastapi import FastAPI

        from trpc_rs_py import Router, attach

        router = Router()

        @router.query
        def health() -> bool:
            return True

        app = attach(FastAPI(), router, path="/api/trpc")
        # tRPC lives at /api/trpc/*; every other route hits FastAPI normally.
        ```
    """
    resolved_limits = _limits_or_default(limits)
    # Build validators once for procedures registered at construction. Later
    # registrations use the legacy signature pre-check.
    validators = {
        name: (
            build_input_validator(proc.handler),
            build_output_validator(
                proc.handler, for_subscription=proc.kind is ProcedureKind.SUBSCRIPTION
            ),
        )
        for name, proc in router.procedures.items()
    }
    rest_app: ASGIApp | None = None
    if rest_path is not None:
        from .rest import serve_attached

        rest_app = serve_attached(
            router, prefix=rest_path, production=production, limits=resolved_limits
        )

    async def middleware(scope: Any, receive: Receive, send: Send) -> None:
        """Route tRPC requests to the router, forwarding everything else."""
        if scope["type"] == "http" and _is_trpc_path(scope, path):
            await _serve_trpc(
                scope,
                receive,
                send,
                router=router,
                limits=resolved_limits,
                production=production,
                procedure_path=_procedure_path(scope, path),
                validators=validators,
                sse_ping_interval=sse_ping_interval,
                max_sse_event_size=max_sse_event_size,
            )
            return
        if (
            scope["type"] == "http"
            and rest_app is not None
            and rest_path is not None
            and _is_trpc_path(scope, rest_path)
        ):
            await rest_app(scope, receive, send)
            return
        await app(scope, receive, send)

    return middleware


def _is_trpc_path(scope: Any, path: str) -> bool:
    """Return whether an HTTP scope targets the tRPC endpoint prefix.

    Args:
        scope: Raw ASGI scope for an `http` request.
        path: The configured tRPC prefix, e.g. `/api/trpc`.

    Returns:
        `True` when the scope path equals `path` or starts with `path + "/"`.

    Example:
        ```python
        from trpc_rs_py._asgi import _is_trpc_path

        assert _is_trpc_path({"path": "/api/trpc/post.list"}, "/api/trpc")
        assert not _is_trpc_path({"path": "/api/health"}, "/api/trpc")
        ```
    """
    request_path: str = scope["path"]
    return request_path == path or request_path.startswith(f"{path}/")
