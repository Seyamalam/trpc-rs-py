"""Compiled extension module (built by maturin from `crates/trpc-py`).

JSON integer input beyond the JSON-safe / u64 range is currently decoded as a
float by the core parser; output conversion rejects Python integers outside
signed/unsigned 64-bit range.
"""

from typing import Any

from typing_extensions import final

__version__: str

class TrpcProtocolError(Exception):
    """Protocol failure with tRPC code and HTTP mapping metadata."""

    message: str
    code: str
    data_code: int
    http_status: int

@final
class Limits:
    """Request size, batch size, and JSON depth limits."""
    def __new__(
        cls,
        *,
        max_body_size: int | None = None,
        max_batch_size: int | None = None,
        max_json_depth: int | None = None,
    ) -> Limits: ...
    @property
    def max_body_size(self) -> int: ...
    @property
    def max_batch_size(self) -> int: ...
    @property
    def max_json_depth(self) -> int: ...

@final
class ParsedCall:
    """One parsed procedure path and its decoded JSON input.

    Input integer literals beyond the JSON-safe / u64 range arrive as floats
    due to serde_json parsing; output conversion rejects such oversized ints.
    """
    @property
    def path(self) -> str: ...
    @property
    def input(self) -> Any: ...

@final
class ParsedRequest:
    """Calls in request order and the batch flag."""
    @property
    def calls(self) -> list[ParsedCall]: ...
    @property
    def batched(self) -> bool: ...

@final
class CallResult:
    """JSON-ready successful value or an error response."""
    @staticmethod
    def ok(data: Any) -> CallResult: ...
    @staticmethod
    def error(
        message: str,
        code: str,
        data_code: int,
        http_status: int,
        code_name: str,
        data: Any | None = None,
    ) -> CallResult: ...

def parse_request(
    method: str, path: str, query: str, body: bytes | None, limits: Limits
) -> ParsedRequest: ...
def build_response(results: list[CallResult], batched: bool) -> tuple[int, str]: ...
def serve(app: Any, loop_: Any, host: str, port: int, *, max_body_size: int | None = None) -> None:
    """Serve an ASGI app on the embedded hyper server until shutdown.

    Blocks (GIL released) until Ctrl-C or until `loop_` stops running; used
    by `trpc_rs_py.serve.serve`, which owns the loop lifecycle.
    """

@final
class SseEncoder:
    r"""Stateful per-stream SSE encoder for one subscription response.

    Encodes frames in the official v11 subscription format
    (`@trpc/server` 11.25.0 `sseStreamProducer`): a `connected` event first,
    then `data` events (tracked events carry `id:`), keep-alive `ping`s, a
    final `return` on clean end, and `serialized-error` carrying the flat v11
    error envelope. Event ids containing `\n`, `\r`, or NUL are rejected;
    once an event id is established, untracked events emit an empty `id:` line
    plus a `restore-id` frame, exactly like upstream.
    """

    def __new__(cls, last_event_id: str | None = None) -> SseEncoder: ...
    @staticmethod
    def connected() -> bytes: ...
    @staticmethod
    def ping() -> bytes: ...
    @staticmethod
    def close() -> bytes: ...
    def data(self, value: Any, id: str | None = None) -> bytes: ...
    @staticmethod
    def serialized_error(message: str, code_name: str, data: Any | None = None) -> bytes: ...

@final
class Watcher:
    """Recursive filesystem watcher for `trpc-py dev` live type sync.

    `next` blocks without holding the GIL and returns one debounced batch of
    changed paths — a save burst regenerates once, not per OS event.
    """

    def __new__(cls, paths: list[str]) -> Watcher: ...
    def next(self, timeout_ms: int | None = None, debounce_ms: int = 50) -> list[str] | None: ...

__all__ = [
    "CallResult",
    "Limits",
    "ParsedCall",
    "ParsedRequest",
    "SseEncoder",
    "TrpcProtocolError",
    "Watcher",
    "__version__",
    "build_response",
    "parse_request",
    "serve",
]
