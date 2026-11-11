"""HTTP clients for calling a tRPC v11 server from Python (ADR 0014).

`create_client` returns a synchronous client built only on the standard
library (`urllib.request`/`http.client`) — it works in any Python process
with no extra dependencies. `create_async_client` returns an
`httpx`-backed async client; `httpx` is imported lazily inside the factory,
so the package still ships with pydantic as its only hard dependency.

Both clients speak the wire protocol served by `trpc_rs_py.mount`: queries
are `GET {url}/{path}?input=<json>`, mutations are `POST`ed JSON bodies,
batches are a single `?batch=1` GET, and subscriptions are SSE streams whose
`data:` events decode to `SubscriptionEvent` objects. Error envelopes raise
`TrpcClientError` — the same shape `testing.TrpcTestClientError` uses, so
in-process and over-the-wire failures look identical.

Example:
    ```python
    from trpc_rs_py.client import create_client

    client = create_client("http://localhost:8000/trpc")
    assert client.query("greeting", {"name": "Ada"}) == "Hello Ada!"
    client.close()
    ```
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

__all__ = [
    "AsyncClient",
    "Client",
    "SubscriptionEvent",
    "TrpcClientError",
    "create_async_client",
    "create_client",
]

_DEFAULT_TIMEOUT = 30.0


def _dumps(value: Any) -> str:
    """Serialize `value` as compact JSON (the tRPC `input` wire form)."""
    return json.dumps(value, separators=(",", ":"))


class TrpcClientError(Exception):
    """A tRPC error envelope raised by the client.

    `query`, `mutation`, and `subscribe` raise it; `batch` returns it
    per-call in the results list so a partial batch failure is inspectable
    without exception handling. It is also used for HTTP-level failures the
    server answered without an envelope (`code` is `"UNKNOWN"` then, and
    `http_status` carries the real status).

    Attributes:
        code: The official string code from `error.data.code`
            (`"NOT_FOUND"`, `"BAD_REQUEST"`, ...).
        message: The `error.message` text.
        data: The `error.data` mapping (holds `code`, `httpStatus`,
            `path`, and any typed-error extras), or `None`.
        http_status: `error.data.httpStatus` when present, else the HTTP
            status of the response for envelope-less failures.

    Example:
        ```python
        import pytest

        with pytest.raises(TrpcClientError) as excinfo:
            client.query("missing")
        assert excinfo.value.code == "NOT_FOUND"
        ```
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: Any = None,
        http_status: int | None = None,
    ) -> None:
        """Initialize from the wire error shape.

        Args:
            code: The official tRPC error code name (or `"UNKNOWN"` when the
                failure never produced a tRPC envelope).
            message: Client-facing error message.
            data: The `error.data` mapping, or `None`.
            http_status: `error.data.httpStatus`, or the raw HTTP status.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.http_status = http_status

    def __str__(self) -> str:
        """Return `"CODE: message"`."""
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class SubscriptionEvent:
    """One decoded data event from a subscription stream.

    Attributes:
        data: The JSON-decoded event payload.
        id: The SSE `id` for `tracked()` events, else `None`. Hand it back
            to `subscribe(last_event_id=...)` to resume after a disconnect.

    Example:
        ```python
        events = list(client.subscribe("ticks"))
        assert events[0].data == {"n": 0}
        assert events[0].id == "0"
        ```
    """

    data: Any
    id: str | None = None


class _StreamResponse(Protocol):
    """The response surface `Client` streams SSE from.

    Satisfied by `http.client.HTTPResponse` (which `urllib.request.urlopen`
    returns for HTTP URLs); declared so the streaming loop stays typed
    without depending on urllib's concrete return type.
    """

    status: int

    def read(self, amt: int = -1) -> bytes: ...

    def read1(self, amt: int) -> bytes: ...

    def getheader(self, name: str) -> str | None: ...

    def close(self) -> None: ...


def _error_from_envelope(envelope: Mapping[str, Any]) -> TrpcClientError:
    """Build a `TrpcClientError` from a wire `{"error": ...}` member.

    Also accepts the flat `serialized-error` SSE shape, which carries the
    same `message`/`code`/`data` fields unwrapped.
    """
    raw_data = envelope.get("data")
    data = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else {}
    message = envelope.get("message")
    code = data.get("code", envelope.get("code", "UNKNOWN"))
    return TrpcClientError(
        str(code),
        str(message) if message is not None else "tRPC call failed",
        data=data or None,
        http_status=data.get("httpStatus"),
    )


def _error_from_http(error: urllib.error.HTTPError) -> TrpcClientError:
    """Turn an `HTTPError` into a `TrpcClientError`.

    The error response body is read once: a tRPC error envelope yields its
    precise code and fields, anything else produces an `UNKNOWN` error that
    still reports the real HTTP status.
    """
    body = error.read()
    payload: Any = None
    if body:
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
    if isinstance(payload, dict):
        envelope = cast("dict[str, Any]", payload).get("error")
        if isinstance(envelope, dict):
            return _error_from_envelope(cast("dict[str, Any]", envelope))
    reason = error.reason or ""
    detail = f"HTTP {error.code} {reason}".rstrip()
    return TrpcClientError("UNKNOWN", detail, http_status=error.code)


def _unwrap(payload: Any) -> Any:
    """Return `result.data` from an envelope; raise on an error envelope."""
    if isinstance(payload, dict):
        envelope = cast("dict[str, Any]", payload)
        error = envelope.get("error")
        if isinstance(error, dict):
            raise _error_from_envelope(cast("dict[str, Any]", error))
        result = envelope.get("result")
        if isinstance(result, dict):
            return cast("dict[str, Any]", result).get("data")
    msg = f"Unexpected tRPC response: {payload!r}"
    raise RuntimeError(msg)


def _parse_batch(payload: Any) -> list[Any] | None:
    """Per-call results for a batch response list, else `None`.

    Each entry is unwrapped to its `result.data`, or to a `TrpcClientError`
    *instance* when the entry is an error envelope — partial batch failure
    is data, not an exception (a malformed entry still raises).
    """
    if not isinstance(payload, list):
        return None
    results: list[Any] = []
    for item in cast("Sequence[Any]", payload):
        if isinstance(item, dict):
            entry = cast("dict[str, Any]", item)
            error = entry.get("error")
            if isinstance(error, dict):
                results.append(_error_from_envelope(cast("dict[str, Any]", error)))
                continue
            result = entry.get("result")
            if isinstance(result, dict):
                results.append(cast("dict[str, Any]", result).get("data"))
                continue
        msg = f"Unexpected batch entry: {item!r}"
        raise RuntimeError(msg)
    return results


def _iter_lines(read: Callable[[int], bytes]) -> Iterator[str]:
    """Yield decoded lines from a streaming response, EOF-terminated.

    `read` is `HTTPResponse.read1`, which returns as soon as a single socket
    read lands — the granularity SSE needs (a blocking `read()` would
    withhold events until the buffer filled).
    """
    buffer = b""
    while True:
        chunk = read(65536)
        if not chunk:
            break
        buffer += chunk
        while True:
            index = buffer.find(b"\n")
            if index < 0:
                break
            line = buffer[:index]
            buffer = buffer[index + 1 :]
            yield line.decode("utf-8", errors="replace").rstrip("\r")
    if buffer:
        yield buffer.decode("utf-8", errors="replace").rstrip("\r")


class _SseParser:
    r"""Incremental SSE frame parser for the tRPC subscription format.

    Feed one decoded line at a time; `feed` returns a `SubscriptionEvent`
    when a data frame completes, `None` for control frames (`connected`,
    `ping`, `restore-id`, comments) and mid-frame lines, raises
    `TrpcClientError` on `serialized-error`, and sets `done` on `return`.
    A `data:` field spanning multiple lines is joined with `"\\n"` per the
    SSE spec; an `id:` line applies to the frame it appears in.
    """

    def __init__(self) -> None:
        """Create a parser with no in-flight frame."""
        self._event: str | None = None
        self._id: str | None = None
        self._data: list[str] = []
        self.done = False

    def feed(self, line: str) -> SubscriptionEvent | None:
        """Consume one line of the stream.

        Args:
            line: A line without its trailing newline/carriage return.

        Returns:
            A `SubscriptionEvent` when the line completed a data frame,
            else `None`.

        Raises:
            TrpcClientError: The frame was an `serialized-error` event.
        """
        if line == "":
            event, data, event_id = self._event, "\n".join(self._data), self._id
            self._event = None
            self._data = []
            self._id = None
            if event == "serialized-error":
                raise _error_from_envelope(cast("dict[str, Any]", json.loads(data) if data else {}))
            if event is not None:
                # connected/ping/return/restore-id are stream control; the
                # official `return` frame also marks a clean end.
                self.done = event == "return"
                return None
            if not data:
                return None
            return SubscriptionEvent(data=json.loads(data), id=event_id or None)
        if line.startswith(":"):
            return None
        name, separator, value = line.partition(":")
        if not separator:
            name, value = line, ""
        elif value.startswith(" "):
            value = value[1:]
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id":
            self._id = value
        return None


def _subscription_request(input: Any, last_event_id: str | None) -> tuple[Any, dict[str, str]]:
    """Merge `last_event_id` into the subscription input, official-style.

    The official client sends the resume id inside the input object as
    `lastEventId`; the server pops it before validation. A non-dict input
    has nowhere to carry it, so the `last-event-id` header (which the
    server reads with higher precedence anyway) takes over there.
    """
    if last_event_id is None:
        return input, {}
    if isinstance(input, dict):
        return {**cast("dict[str, Any]", input), "lastEventId": last_event_id}, {}
    if input is None:
        return {"lastEventId": last_event_id}, {}
    return input, {"last-event-id": last_event_id}


class Client:
    """Synchronous tRPC client over stdlib `urllib`, built by `create_client`.

    Each call opens a fresh connection (no pooling); `close()` aborts any
    in-flight subscription streams and marks the client unusable. The client
    is usable as a context manager. Transport failures that never produced
    an HTTP response (`urllib.error.URLError`, socket timeouts) propagate
    unchanged — only tRPC/HTTP failures become `TrpcClientError`.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = _DEFAULT_TIMEOUT,
    ) -> None:
        """Create a client; prefer `create_client` over direct construction.

        Args:
            url: The tRPC base URL, e.g. `http://localhost:8000/trpc`.
            headers: Headers merged into every request (auth etc.).
            timeout: Per-operation socket timeout in seconds applied to
                connect and every read, or `None` to block forever. For
                `subscribe` this bounds each individual read, so an idle
                stream hits it unless the server is mounted with
                `sse_ping_interval` below the timeout.
        """
        self._base = url.rstrip("/")
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._closed = False
        self._streams: set[_StreamResponse] = set()

    def __enter__(self) -> Client:
        """Return `self` for `with create_client(...) as client:` blocks."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client at block exit."""
        self.close()

    def close(self) -> None:
        """Release in-flight streams and mark the client closed.

        Any subscription generator still iterating gets its underlying
        socket closed (its next read raises `ValueError`); subsequent calls
        raise `RuntimeError`. Safe to call more than once.

        Example:
            ```python
            client = create_client("http://localhost:8000/trpc")
            client.close()
            ```
        """
        self._closed = True
        for stream in tuple(self._streams):
            stream.close()
        self._streams.clear()

    def query(self, path: str, input: Any = None) -> Any:
        """Run one query procedure over GET and return `result.data`.

        Args:
            path: Dotted procedure path (`"posts.by_id"`).
            input: JSON-ready input; `None` sends no `input` parameter.

        Returns:
            The procedure's result data.

        Raises:
            TrpcClientError: The response carried an error envelope, or the
                request failed at the HTTP level.
            RuntimeError: The response was not a tRPC envelope.

        Example:
            ```python
            client.query("posts.by_id", {"post_id": 1})
            ```
        """
        params = {} if input is None else {"input": _dumps(input)}
        response = self._request("GET", path, params)
        return _unwrap(json.loads(response.read()))

    def mutation(self, path: str, input: Any = None) -> Any:
        """Run one mutation procedure over POST and return `result.data`.

        Args:
            path: Dotted procedure path.
            input: JSON body; `None` posts the JSON `null` body.

        Returns:
            The procedure's result data.

        Raises:
            TrpcClientError: The response carried an error envelope, or the
                request failed at the HTTP level.
            RuntimeError: The response was not a tRPC envelope.

        Example:
            ```python
            client.mutation("posts.create", {"title": "hi"})
            ```
        """
        body = _dumps(input).encode("utf-8")
        response = self._request(
            "POST", path, {}, body=body, extra_headers={"content-type": "application/json"}
        )
        return _unwrap(json.loads(response.read()))

    def batch(self, calls: Sequence[Mapping[str, Any]]) -> list[Any]:
        """Run several calls in one batched GET (`?batch=1`).

        Args:
            calls: Mappings with a `"path"` key and an optional `"input"`
                key. All calls must share one procedure kind, matching the
                wire contract; subscriptions cannot be batched.

        Returns:
            Per-call list: each entry is the call's `result.data`, or a
            `TrpcClientError` *instance* for calls that failed (the list is
            returned, not raised, so partial failures are inspectable). A
            top-level error (for example a mixed-kind batch) is raised
            instead.

        Raises:
            TrpcClientError: The batch itself failed before dispatch.

        Example:
            ```python
            results = client.batch([
                {"path": "ping"},
                {"path": "echo", "input": {"message": "x"}},
            ])
            ```
        """
        paths = ",".join(str(call["path"]) for call in calls)
        input_map = {str(index): call.get("input") for index, call in enumerate(calls)}
        url = f"{self._base}/{paths}"
        params = urllib.parse.urlencode({"batch": "1", "input": _dumps(input_map)})
        request = urllib.request.Request(
            f"{url}?{params}", headers=dict(self._headers), method="GET"
        )
        # The batch response status follows its worst per-call error (a
        # NOT_FOUND entry answers HTTP 404) while the body still carries the
        # full results list — so the body must be read before status rules
        # apply.
        response = self._open(request, allow_http_error=True)
        body = response.read()
        try:
            payload: Any = json.loads(body) if body else None
        except ValueError:
            payload = None
        results = _parse_batch(payload)
        if results is not None:
            return results
        if isinstance(payload, dict):
            error = cast("dict[str, Any]", payload).get("error")
            if isinstance(error, dict):
                raise _error_from_envelope(cast("dict[str, Any]", error))
        status = getattr(response, "status", None) or getattr(response, "code", 0)
        if isinstance(status, int) and status >= 400:
            raise TrpcClientError("UNKNOWN", f"HTTP {status}", http_status=status)
        msg = f"Unexpected batch response: {payload!r}"
        raise RuntimeError(msg)

    def subscribe(
        self, path: str, input: Any = None, *, last_event_id: str | None = None
    ) -> Iterator[SubscriptionEvent]:
        """Stream a subscription's SSE data events lazily.

        The request fires when iteration starts, not at call time. The
        iterator ends when the stream closes (the server's `return` frame
        or connection end). Closing the generator — or the client — closes
        the underlying socket, so a never-ending stream is released.

        Args:
            path: Dotted subscription procedure path.
            input: JSON-ready input; `None` sends no `input` parameter.
            last_event_id: Resume id from a previous `SubscriptionEvent.id`.
                It is merged into the input object as `lastEventId`, exactly
                like the official client (the `last-event-id` header is used
                instead when `input` is not a dict).

        Yields:
            One `SubscriptionEvent` per data frame; `connected`, `ping`,
            `return`, and `restore-id` control frames are consumed
            silently.

        Raises:
            TrpcClientError: The stream ended in `serialized-error`, or the
                path answered with a JSON error envelope.
            RuntimeError: The procedure did not produce an SSE stream.

        Example:
            ```python
            for event in client.subscribe("ticks", {"count": 2}):
                print(event.data, event.id)
            ```
        """
        return self._subscribe(path, input, last_event_id=last_event_id)

    # -- internals ---------------------------------------------------------

    def _ensure_open(self) -> None:
        """Raise `RuntimeError` when `close()` has run."""
        if self._closed:
            msg = "tRPC client is closed"
            raise RuntimeError(msg)

    def _open(
        self, request: urllib.request.Request, *, allow_http_error: bool = False
    ) -> _StreamResponse:
        """Dispatch `request`, mapping HTTP errors to `TrpcClientError`.

        With `allow_http_error` the `HTTPError` itself is returned so the
        caller can read its body — batches need this because an HTTP error
        status still carries the per-call results list.
        """
        self._ensure_open()
        try:
            if self._timeout is None:
                return cast(_StreamResponse, urllib.request.urlopen(request))
            return cast(_StreamResponse, urllib.request.urlopen(request, timeout=self._timeout))
        except urllib.error.HTTPError as error:
            if allow_http_error:
                return cast(_StreamResponse, error)
            raise _error_from_http(error) from None

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str],
        *,
        body: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> _StreamResponse:
        """Build and dispatch one request; return the raw response."""
        url = f"{self._base}/{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        headers = dict(self._headers)
        headers.update(extra_headers or {})
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        return self._open(request)

    def _subscribe(
        self, path: str, input: Any, *, last_event_id: str | None
    ) -> Iterator[SubscriptionEvent]:
        """Generator body of `subscribe`: open, parse, yield, close."""
        merged_input, extra_headers = _subscription_request(input, last_event_id)
        params = {} if merged_input is None else {"input": _dumps(merged_input)}
        extra_headers["accept"] = "text/event-stream"
        url = f"{self._base}/{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        headers = dict(self._headers)
        headers.update(extra_headers)
        request = urllib.request.Request(url, headers=headers, method="GET")
        response = self._open(request)
        self._streams.add(response)
        try:
            content_type = response.getheader("content-type") or ""
            if not content_type.startswith("text/event-stream"):
                body = response.read()
                try:
                    payload: Any = json.loads(body) if body else None
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and "error" in payload:
                    raise _error_from_envelope(cast("dict[str, Any]", payload)["error"])
                msg = f'Procedure "{path}" did not produce an SSE stream'
                raise RuntimeError(msg)
            parser = _SseParser()
            for line in _iter_lines(response.read1):
                event = parser.feed(line)
                if parser.done:
                    return
                if event is not None:
                    yield event
        finally:
            self._streams.discard(response)
            response.close()


def create_client(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = _DEFAULT_TIMEOUT,
) -> Client:
    """Create a synchronous tRPC client for the server at `url`.

    Uses only the standard library — no new dependencies. `url` is the
    tRPC base the router is mounted under (for example
    `http://localhost:8000/trpc`), not a specific procedure.

    Args:
        url: The tRPC base URL.
        headers: Headers merged into every request (auth etc.).
        timeout: Per-operation socket timeout in seconds, or `None` to
            block forever. Defaults to 30 seconds; for `subscribe` it
            bounds each individual read.

    Returns:
        The `Client`. Call `.close()` when done (or use `with`).

    Example:
        ```python
        from trpc_rs_py.client import create_client

        with create_client("http://localhost:8000/trpc") as client:
            print(client.query("health"))
        ```
    """
    return Client(url, headers=headers, timeout=timeout)


class AsyncClient:
    """Async tRPC client over `httpx`, built by `create_async_client`.

    Mirrors `Client` method-for-method; `subscribe` yields
    `SubscriptionEvent`s from an async generator and `close` is a
    coroutine. Usable as an async context manager.
    """

    def __init__(
        self,
        url: str,
        http: Any,
        *,
        timeout: float | None = _DEFAULT_TIMEOUT,
    ) -> None:
        """Wrap an `httpx.AsyncClient`; created by `create_async_client`.

        Args:
            url: The tRPC base URL, e.g. `http://localhost:8000/trpc`.
            http: The `httpx.AsyncClient` performing requests (created by
                the factory so `httpx` stays a lazy dependency).
            timeout: Request timeout in seconds plumbed to httpx, or
                `None` for no timeout. For `subscribe` it bounds reads, so
                an idle stream hits it unless the server is mounted with
                `sse_ping_interval` below the timeout.
        """
        self._base = url.rstrip("/")
        self._http = http
        self._timeout = timeout
        self._closed = False

    async def __aenter__(self) -> AsyncClient:
        """Return `self` for `async with create_async_client(...)` blocks."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the client at block exit."""
        await self.close()

    async def close(self) -> None:
        """Close the underlying `httpx.AsyncClient` and its connections.

        Example:
            ```python
            client = create_async_client("http://localhost:8000/trpc")
            await client.close()
            ```
        """
        self._closed = True
        await self._http.aclose()

    async def query(self, path: str, input: Any = None) -> Any:
        """Run one query procedure over GET and return `result.data`.

        Args:
            path: Dotted procedure path (`"posts.by_id"`).
            input: JSON-ready input; `None` sends no `input` parameter.

        Returns:
            The procedure's result data.

        Raises:
            TrpcClientError: The response carried an error envelope.
            RuntimeError: The response was not a tRPC envelope.

        Example:
            ```python
            await client.query("posts.by_id", {"post_id": 1})
            ```
        """
        params = {} if input is None else {"input": _dumps(input)}
        response = await self._get(path, params)
        return _unwrap(response.json())

    async def mutation(self, path: str, input: Any = None) -> Any:
        """Run one mutation procedure over POST and return `result.data`.

        Args:
            path: Dotted procedure path.
            input: JSON body; `None` posts the JSON `null` body.

        Returns:
            The procedure's result data.

        Raises:
            TrpcClientError: The response carried an error envelope.
            RuntimeError: The response was not a tRPC envelope.

        Example:
            ```python
            await client.mutation("posts.create", {"title": "hi"})
            ```
        """
        self._ensure_open()
        response = await self._http.post(
            f"{self._base}/{path}",
            content=_dumps(input).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        if response.status_code >= 400:
            raise self._http_error(response)
        return _unwrap(response.json())

    async def batch(self, calls: Sequence[Mapping[str, Any]]) -> list[Any]:
        """Run several calls in one batched GET (`?batch=1`).

        Args:
            calls: Mappings with a `"path"` key and an optional `"input"`
                key. All calls must share one procedure kind, matching the
                wire contract; subscriptions cannot be batched.

        Returns:
            Per-call list of `result.data` values or `TrpcClientError`
            instances for failed calls; a top-level error is raised.

        Raises:
            TrpcClientError: The batch itself failed before dispatch.

        Example:
            ```python
            results = await client.batch([{"path": "ping"}, {"path": "boom"}])
            assert isinstance(results[1], TrpcClientError)
            ```
        """
        self._ensure_open()
        paths = ",".join(str(call["path"]) for call in calls)
        input_map = {str(index): call.get("input") for index, call in enumerate(calls)}
        # Read the body before applying status rules: the response status
        # follows the worst per-call error while the body still carries the
        # full results list.
        response = await self._http.get(
            f"{self._base}/{paths}",
            params={"batch": "1", "input": _dumps(input_map)},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        results = _parse_batch(payload)
        if results is not None:
            return results
        if isinstance(payload, dict):
            error = cast("dict[str, Any]", payload).get("error")
            if isinstance(error, dict):
                raise _error_from_envelope(cast("dict[str, Any]", error))
        if response.status_code >= 400:
            raise self._http_error(response)
        msg = f"Unexpected batch response: {payload!r}"
        raise RuntimeError(msg)

    async def subscribe(
        self, path: str, input: Any = None, *, last_event_id: str | None = None
    ) -> AsyncIterator[SubscriptionEvent]:
        """Stream a subscription's SSE data events lazily.

        The request fires on the first `__anext__`, not at call time. The
        async iterator ends at the server's `return` frame or when the
        stream closes; `aclose()`-ing the generator releases the response.

        Args:
            path: Dotted subscription procedure path.
            input: JSON-ready input; `None` sends no `input` parameter.
            last_event_id: Resume id merged into the input object as
                `lastEventId` like the official client (sent as the
                `last-event-id` header when `input` is not a dict).

        Yields:
            One `SubscriptionEvent` per data frame; control frames are
            consumed silently.

        Raises:
            TrpcClientError: The stream ended in `serialized-error`, or the
                path answered with a JSON error envelope.
            RuntimeError: The procedure did not produce an SSE stream.

        Example:
            ```python
            async for event in client.subscribe("ticks", {"count": 2}):
                print(event.data, event.id)
            ```
        """
        merged_input, extra_headers = _subscription_request(input, last_event_id)
        params = {} if merged_input is None else {"input": _dumps(merged_input)}
        extra_headers["accept"] = "text/event-stream"
        self._ensure_open()
        async with self._http.stream(
            "GET", f"{self._base}/{path}", params=params, headers=extra_headers
        ) as response:
            if response.status_code >= 400:
                # `response.json()` raises `ResponseNotRead` while the
                # streamed body is unread — drain it first.
                await response.aread()
                raise self._http_error(response)
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("text/event-stream"):
                body = await response.aread()
                try:
                    payload: Any = json.loads(body) if body else None
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and "error" in payload:
                    raise _error_from_envelope(cast("dict[str, Any]", payload)["error"])
                msg = f'Procedure "{path}" did not produce an SSE stream'
                raise RuntimeError(msg)
            parser = _SseParser()
            async for line in response.aiter_lines():
                event = parser.feed(line)
                if parser.done:
                    return
                if event is not None:
                    yield event

    # -- internals ---------------------------------------------------------

    def _ensure_open(self) -> None:
        """Raise `RuntimeError` when `close()` has run."""
        if self._closed:
            msg = "tRPC client is closed"
            raise RuntimeError(msg)

    async def _get(self, path: str, params: Mapping[str, str]) -> Any:
        """Issue a GET and return the response after the status check."""
        self._ensure_open()
        response = await self._http.get(f"{self._base}/{path}", params=params)
        if response.status_code >= 400:
            raise self._http_error(response)
        return response

    @staticmethod
    def _http_error(response: Any) -> TrpcClientError:
        """Build a `TrpcClientError` from a non-2xx httpx response."""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            envelope = cast("dict[str, Any]", payload).get("error")
            if isinstance(envelope, dict):
                return _error_from_envelope(cast("dict[str, Any]", envelope))
        reason = getattr(response, "reason_phrase", "") or ""
        detail = f"HTTP {response.status_code} {reason}".rstrip()
        return TrpcClientError("UNKNOWN", detail, http_status=response.status_code)


def create_async_client(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = _DEFAULT_TIMEOUT,
) -> AsyncClient:
    """Create an async tRPC client for the server at `url`.

    Requires the optional `httpx` package (`pip install httpx`), imported
    lazily here so the sync path stays dependency-free.

    Args:
        url: The tRPC base URL, e.g. `http://localhost:8000/trpc`.
        headers: Headers merged into every request (auth etc.).
        timeout: Request timeout in seconds plumbed to httpx (30 by
            default), or `None` for no timeout.

    Returns:
        The `AsyncClient`. Call `await .close()` when done (or use
        `async with`).

    Raises:
        RuntimeError: `httpx` is not installed.

    Example:
        ```python
        from trpc_rs_py.client import create_async_client

        async with create_async_client("http://localhost:8000/trpc") as client:
            print(await client.query("health"))
        ```
    """
    try:
        import httpx
    except ImportError as error:
        msg = "create_async_client requires the optional 'httpx' package — pip install httpx"
        raise RuntimeError(msg) from error
    http = httpx.AsyncClient(headers=dict(headers or {}), timeout=timeout)
    return AsyncClient(url, http, timeout=timeout)
