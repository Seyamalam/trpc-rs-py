"""User-facing router and procedure API.

A `Router` collects procedures registered with the `@router.query` and
`@router.mutation` decorators. Procedures are registered under dotted paths;
routers compose with `merge` (later entries replace duplicate paths) and
`nest` (a dotted prefix is prepended), mirroring the Rust `Router` in
`crates/trpc-core/src/protocol.rs`.

Example:
    ```python
    from trpc_rs_py import Router

    posts = Router()

    @posts.query
    def by_id(post_id: int) -> str:
        return f"post {post_id}"

    app = Router()
    app.nest("posts", posts)
    assert "posts.by_id" in app.procedures
    ```
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import typing
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Mapping,
)
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType, UnionType
from typing import TYPE_CHECKING, Any, TypeVar, overload

if TYPE_CHECKING:
    from typing_extensions import TypeVarTuple, Unpack

    _Ts = TypeVarTuple("_Ts")

__all__ = ["Procedure", "ProcedureKind", "Router"]

# Same charset as the Rust path parser: ASCII alphanumerics plus '.', '-', '_'.
# `fullmatch` on a `+` quantifier also rejects the empty string.
_PATH_PATTERN = re.compile(r"[A-Za-z0-9._-]+")

_R = TypeVar("_R")
_HandlerT = TypeVar("_HandlerT", bound=Callable[..., Any])


class ProcedureKind(Enum):
    """Whether a procedure is a query, a mutation, or a subscription.

    Mirrors `ProcedureKind` in `crates/trpc-core/src/protocol.rs`; the string
    value is the lowercase wire name used by the code generators.

    Example:
        ```python
        from trpc_rs_py import ProcedureKind

        assert ProcedureKind.QUERY.value == "query"
        ```
    """

    QUERY = "query"
    MUTATION = "mutation"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True)
class Procedure:
    """One registered procedure.

    Captured at registration time so the adapter and code generators never
    need to re-inspect the handler. `input_annotation` and `output_annotation`
    are the handler's type-hint objects (resolved through
    `typing.get_type_hints`), or `None` where the handler is unannotated.

    Attributes:
        path: Full dotted path, e.g. `posts.by_id`.
        kind: Query or mutation semantics.
        handler: The decorated Python callable.
        input_annotation: Type hint of the handler's first parameter, or
            `None` when unannotated.
        output_annotation: Type hint of the handler's return, or `None` when
            unannotated.
        errors: Exception types the procedure may raise. Empty for now; typed
            errors land in 0.4.0.
        rest: Optional `RestRoute` metadata opting the procedure into REST
            endpoints and the generated OpenAPI document.

    Example:
        ```python
        from trpc_rs_py import Procedure, ProcedureKind

        def handler() -> int:
            return 1

        proc = Procedure(path="count", kind=ProcedureKind.QUERY, handler=handler)
        assert proc.path == "count"
        ```
    """

    path: str
    kind: ProcedureKind
    handler: Callable[..., Any]
    input_annotation: Any = None
    output_annotation: Any = None
    errors: tuple[type[Exception], ...] = ()
    rest: RestRoute | None = None

    def __replace__(self, /, **changes: Any) -> Procedure:
        """Return a copy of this procedure with the given fields replaced.

        Defined explicitly so the dataclass hook exists identically on every
        supported Python version instead of relying on the 3.13+ synthesized
        `__replace__`.

        Example:
            ```python
            from trpc_rs_py import Procedure, ProcedureKind

            def handler() -> int:
                return 1

            proc = Procedure(path="count", kind=ProcedureKind.QUERY, handler=handler)
            assert proc.__replace__(path="v2").path == "v2"
            ```
        """
        return dataclasses.replace(self, **changes)


_REST_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_PATH_PARAM_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclasses.dataclass(frozen=True)
class RestRoute:
    """Opt-in REST/OpenAPI metadata for a query or mutation procedure.

    Procedures declaring a `RestRoute` are additionally served as plain REST
    endpoints by `trpc_rs_py.mount_rest` and appear in the generated
    OpenAPI 3.1 document. Path segments written as `{name}` bind path
    parameters to same-named handler parameters; remaining parameters come
    from the query string (GET/DELETE) or the JSON request body.

    Attributes:
        method: HTTP method; one of GET, POST, PUT, PATCH or DELETE.
        path: URL path relative to the REST mount, e.g. `/posts/{post_id}`.

    Raises:
        ValueError: If the method is unsupported or the path is malformed.

    Example:
        ```python
        from trpc_rs_py import RestRoute, Router

        router = Router()

        @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
        def by_id(post_id: int) -> str:
            return str(post_id)
        ```
    """

    method: str
    path: str

    def __post_init__(self) -> None:
        """Normalize the method and validate the path template.

        Raises:
            ValueError: If the method is unsupported, the path does not start
                with `/`, or a `{name}` placeholder is invalid or repeated.
        """
        method = self.method.upper()
        if method not in _REST_METHODS:
            msg = (
                f"Unsupported REST method {self.method!r}: expected one of {sorted(_REST_METHODS)}"
            )
            raise ValueError(msg)
        object.__setattr__(self, "method", method)
        if not self.path.startswith("/"):
            msg = f"REST path must start with '/': {self.path!r}"
            raise ValueError(msg)
        names = _PATH_PARAM_PATTERN.findall(self.path)
        if len(names) != len(set(names)):
            msg = f"REST path repeats a placeholder name: {self.path!r}"
            raise ValueError(msg)
        if "{" in _PATH_PARAM_PATTERN.sub("", self.path) or "}" in _PATH_PARAM_PATTERN.sub(
            "", self.path
        ):
            msg = f"Malformed REST path placeholder: {self.path!r}"
            raise ValueError(msg)

    @property
    def path_parameters(self) -> tuple[str, ...]:
        """Return the `{name}` placeholders declared by `path`, in order."""
        return tuple(_PATH_PARAM_PATTERN.findall(self.path))

    def regex(self) -> re.Pattern[str]:
        """Compile `path` into an anchored pattern with named groups."""
        parts: list[str] = []
        last = 0
        for match in _PATH_PARAM_PATTERN.finditer(self.path):
            parts.append(re.escape(self.path[last : match.start()]))
            parts.append(f"(?P<{match.group(1)}>[^/]+)")
            last = match.end()
        parts.append(re.escape(self.path[last:]))
        return re.compile(f"^{''.join(parts)}$")


def _validate_name(name: str, *, what: str) -> None:
    """Validate a path component with the Rust parser's charset rule.

    Args:
        name: Candidate path component (procedure name or nest prefix).
        what: Human-readable role of the name, used in the error message.

    Raises:
        ValueError: If `name` is empty or contains characters outside
            ASCII alphanumerics, `.`, `-` and `_`.

    Example:
        ```python
        from trpc_rs_py._router import _validate_name

        _validate_name("posts.by_id", what="procedure path")  # ok
        ```
    """
    if not name or not _PATH_PATTERN.fullmatch(name):
        msg = (
            f"Invalid {what} {name!r}: must be a non-empty string using only "
            "ASCII letters, digits, '.', '-' and '_'"
        )
        raise ValueError(msg)


def type_hints_of(handler: Callable[..., Any]) -> dict[str, Any]:
    """Resolve a handler's type hints, tolerating unresolvable annotations.

    Args:
        handler: The decorated callable.

    Returns:
        A mapping from parameter and `return` names to annotation objects.
        Falls back to raw `__annotations__` (possibly string forms) when the
        hints cannot be resolved, and to an empty mapping when the handler's
        signature cannot be inspected at all.

    Example:
        ```python
        from trpc_rs_py._router import type_hints_of

        def f(x: int) -> str:
            return str(x)

        assert type_hints_of(f)["return"] is str
        ```
    """
    try:
        return typing.get_type_hints(handler, include_extras=True)
    except (NameError, TypeError, ValueError):
        return dict(getattr(handler, "__annotations__", {}))


# Origins accepted as a subscription handler's return annotation; the first
# type argument is the yielded item type. `AsyncGenerator[T, S]` ignores the
# send type, matching how the annotation maps to the item type `T`.
_ASYNC_ITERABLE_ORIGINS = (AsyncIterator, AsyncIterable, AsyncGenerator)
# Wrappers an `async def` handler may annotate when it returns a prebuilt
# async iterable (`Awaitable[X]`; `Coroutine[*, *, X]` uses its last arg).
_AWAITABLE_ORIGINS = (Awaitable, Coroutine)


def _unwrap_awaitable(annotation: Any) -> Any:
    """Strip `Annotated` and `Awaitable`/`Coroutine` wrappers from a return annotation.

    Args:
        annotation: A resolved return annotation object.

    Returns:
        The innermost annotation after `Annotated` metadata and at most one
        `Awaitable`/`Coroutine` layer are removed.

    Example:
        ```python
        from collections.abc import AsyncIterator

        from trpc_rs_py.router import _unwrap_awaitable
        from typing import Awaitable

        assert _unwrap_awaitable(Awaitable[AsyncIterator[int]]) is AsyncIterator[int]
        ```
    """
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    if typing.get_origin(annotation) in _AWAITABLE_ORIGINS:
        args = typing.get_args(annotation)
        if args:
            annotation = args[-1]
        while typing.get_origin(annotation) is typing.Annotated:
            annotation = typing.get_args(annotation)[0]
    return annotation


def unwrap_tracked(annotation: Any) -> Any:
    """Replace `Tracked[X]` with `X`, recursively through unions and `Annotated`.

    The SSE event id carried by `Tracked` is protocol metadata, never part of
    the JSON payload, so the wire item type unwraps to `X`. A bare `Tracked`
    (no type argument) becomes `Any` — the payload type is unknown.

    Args:
        annotation: An item annotation, possibly containing `Tracked[...]`.

    Returns:
        The annotation with every top-level `Tracked` wrapper removed —
        applied through `Union` and `Annotated` members. `Tracked` nested
        deeper (for example inside `list[Tracked[int]]`) is left untouched;
        nothing can serialize or type that shape anyway.

    Example:
        ```python
        from trpc_rs_py.router import unwrap_tracked
        from trpc_rs_py.streaming import Tracked

        assert unwrap_tracked(Tracked[int]) is int
        ```
    """
    from .streaming import Tracked

    if annotation is Tracked:
        return Any
    origin = typing.get_origin(annotation)
    if origin is Tracked:
        args = typing.get_args(annotation)
        return args[0] if args else Any
    if origin is typing.Annotated:
        args = typing.get_args(annotation)
        inner = unwrap_tracked(args[0])
        if inner is args[0]:
            return annotation
        annotated_args = (inner, *args[1:])
        return typing.Annotated[annotated_args]
    if origin in (typing.Union, UnionType):
        members = tuple(unwrap_tracked(arg) for arg in typing.get_args(annotation))
        union: Any = members[0]
        for member in members[1:]:
            union |= member
        return union
    return annotation


def subscription_item_annotation(annotation: Any) -> Any:
    """Unwrap a subscription return annotation to the yielded wire item type.

    Strips `Annotated` metadata, an optional `Awaitable`/`Coroutine` wrapper,
    and the `AsyncIterator`/`AsyncIterable`/`AsyncGenerator` container, then
    removes `Tracked` wrappers (see `unwrap_tracked`).

    Args:
        annotation: A resolved return annotation object.

    Returns:
        The wire item annotation, `Any` when the item type is unknown, or
        `None` when the annotation does not describe an async iterable.

    Example:
        ```python
        from collections.abc import AsyncIterator

        from trpc_rs_py.router import subscription_item_annotation
        from trpc_rs_py.streaming import Tracked

        assert subscription_item_annotation(AsyncIterator[Tracked[int]]) is int
        ```
    """
    unwrapped = _unwrap_awaitable(annotation)
    if typing.get_origin(unwrapped) not in _ASYNC_ITERABLE_ORIGINS:
        return None
    args = typing.get_args(unwrapped)
    if not args:
        return Any
    return unwrap_tracked(args[0])


def _signature_of(handler: Callable[..., Any]) -> inspect.Signature | None:
    """Return the handler's signature, or `None` if it cannot be inspected.

    Args:
        handler: The decorated callable.

    Returns:
        The `inspect.Signature`, or `None` for handlers such as builtins whose
        signature Python cannot determine.

    Example:
        ```python
        from trpc_rs_py._router import _signature_of

        def f(x: int) -> str:
            return str(x)

        assert _signature_of(f) is not None
        ```
    """
    try:
        return inspect.signature(handler)
    except (TypeError, ValueError):
        return None


def _input_annotation_of(hints: dict[str, Any], signature: inspect.Signature | None) -> Any:
    """Return the handler's input annotation from its first real parameter.

    Args:
        hints: Resolved type hints of the handler.
        signature: The handler's signature, or `None` when uninspectable.

    Returns:
        The annotation of the first positional or keyword-only parameter, or
        `None` when the handler has no such parameter or leaves it unannotated.

    Example:
        ```python
        import inspect

        from trpc_rs_py._router import _input_annotation_of, type_hints_of

        def f(x: int) -> str:
            return str(x)

        assert _input_annotation_of(type_hints_of(f), inspect.signature(f)) is int
        ```
    """
    if signature is None:
        return None
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return hints.get(parameter.name)
    return None


class Router:
    """A collection of procedures registered under dotted paths.

    Queries and mutations are registered with decorators that preserve the
    handler's type signature. Routers compose with `merge` and `nest`.

    Example:
        ```python
        from trpc_rs_py import Router

        router = Router()

        @router.query
        def greeting(name: str) -> str:
            return f"Hello {name}!"

        assert "greeting" in router.procedures
        ```
    """

    def __init__(self) -> None:
        """Create an empty router.

        Example:
            ```python
            from trpc_rs_py import Router

            assert len(Router().procedures) == 0
            ```
        """
        self._procedures: dict[str, Procedure] = {}

    @property
    def procedures(self) -> Mapping[str, Procedure]:
        """Read-only mapping from full dotted path to `Procedure`.

        Example:
            ```python
            from trpc_rs_py import ProcedureKind, Router

            router = Router()

            @router.query
            def health() -> bool:
                return True

            assert router.procedures["health"].kind is ProcedureKind.QUERY
            ```
        """
        return MappingProxyType(self._procedures)

    def merge(self, other: Router) -> None:
        """Merge `other` into this router; later entries replace duplicates.

        Mirrors `Router::merge` in `crates/trpc-core/src/protocol.rs`: the
        incoming router wins on path collisions.

        Args:
            other: The router whose procedures are copied in.

        Example:
            ```python
            from trpc_rs_py import Router

            a, b = Router(), Router()

            @a.query
            def health() -> bool:
                return True

            @b.query(name="health")
            def health_v2() -> bool:
                return False

            a.merge(b)
            assert a.procedures["health"].handler is health_v2
            ```
        """
        self._procedures.update(other._procedures)

    def nest(self, prefix: str, other: Router) -> None:
        """Add every procedure of `other` under a dotted `prefix`.

        Mirrors `Router::nest` in `crates/trpc-core/src/protocol.rs`. The
        prefix is validated with the same charset rule as the Rust path parser
        (ASCII alphanumerics, `.`, `-`, `_`; non-empty).

        Args:
            prefix: Dotted namespace prepended to `other`'s paths.
            other: The router to nest.

        Raises:
            ValueError: If `prefix` is empty or contains characters outside
                ASCII alphanumerics, `.`, `-` and `_`.

        Example:
            ```python
            from trpc_rs_py import Router

            posts, app = Router(), Router()

            @posts.query
            def by_id(post_id: int) -> str:
                return f"post {post_id}"

            app.nest("posts", posts)
            assert "posts.by_id" in app.procedures
            ```
        """
        _validate_name(prefix, what="nest prefix")
        # Snapshot the source entries: nesting a router into itself must not
        # mutate the mapping being iterated.
        for path, procedure in list(other._procedures.items()):
            self._procedures[f"{prefix}.{path}"] = procedure

    @overload
    def query(self, func: Callable[[Unpack[_Ts]], _R], /) -> Callable[[Unpack[_Ts]], _R]: ...

    @overload
    def query(
        self, *, name: str | None = ..., rest: RestRoute | None = ...
    ) -> Callable[[Callable[[Unpack[_Ts]], _R]], Callable[[Unpack[_Ts]], _R]]: ...

    def query(
        self, *args: Any, name: str | None = None, rest: RestRoute | None = None
    ) -> Callable[..., Any]:
        """Register a query procedure; usable bare or with a `name` override.

        Args:
            *args: The decorated function when used bare (`@router.query`).
                At most one positional argument is accepted.
            name: Explicit procedure name; defaults to the function's
                `__name__`.
            rest: Optional `RestRoute` metadata opting the procedure into the
                REST endpoints and OpenAPI document served by `mount_rest`.

        Returns:
            The function itself when used bare, otherwise a decorator.

        Raises:
            TypeError: If more than one positional argument is given.
            ValueError: If the resulting path is invalid or already taken.

        Example:
            ```python
            from trpc_rs_py import Router

            router = Router()

            @router.query(name="user_by_id")
            def get_user(user_id: int) -> str:
                return f"user {user_id}"

            assert "user_by_id" in router.procedures
            ```
        """
        return self._register(ProcedureKind.QUERY, args, name, rest)

    @overload
    def mutation(self, func: Callable[[Unpack[_Ts]], _R], /) -> Callable[[Unpack[_Ts]], _R]: ...

    @overload
    def mutation(
        self, *, name: str | None = ..., rest: RestRoute | None = ...
    ) -> Callable[[Callable[[Unpack[_Ts]], _R]], Callable[[Unpack[_Ts]], _R]]: ...

    def mutation(
        self, *args: Any, name: str | None = None, rest: RestRoute | None = None
    ) -> Callable[..., Any]:
        """Register a mutation procedure; usable bare or with a `name` override.

        Args:
            *args: The decorated function when used bare (`@router.mutation`).
                At most one positional argument is accepted.
            name: Explicit procedure name; defaults to the function's
                `__name__`.
            rest: Optional `RestRoute` metadata opting the procedure into the
                REST endpoints and OpenAPI document served by `mount_rest`.

        Returns:
            The function itself when used bare, otherwise a decorator.

        Raises:
            TypeError: If more than one positional argument is given.
            ValueError: If the resulting path is invalid or already taken.

        Example:
            ```python
            from trpc_rs_py import Router

            router = Router()

            @router.mutation
            def rename_user(user_id: int) -> str:
                return f"renamed {user_id}"

            assert "rename_user" in router.procedures
            ```
        """
        return self._register(ProcedureKind.MUTATION, args, name, rest)

    @overload
    def subscription(self, func: Callable[[Unpack[_Ts]], _R], /) -> Callable[[Unpack[_Ts]], _R]: ...

    @overload
    def subscription(
        self, *, name: str | None = ...
    ) -> Callable[[Callable[[Unpack[_Ts]], _R]], Callable[[Unpack[_Ts]], _R]]: ...

    def subscription(self, *args: Any, name: str | None = None) -> Callable[..., Any]:
        """Register a subscription procedure; usable bare or with a `name`.

        The handler must return an async iterable (typically an `async def`
        generator). Each yielded value becomes one SSE data event; yielding
        `tracked(id, value)` marks the event with an id for client resume.
        Subscriptions are served over GET only and cannot be batched —
        matching the official v11 method table.

        Args:
            *args: The decorated function when used bare
                (`@router.subscription`). At most one positional argument is
                accepted.
            name: Explicit procedure name; defaults to the function's
                `__name__`.

        Returns:
            The function itself when used bare, otherwise a decorator.

        Raises:
            TypeError: If more than one positional argument is given.
            ValueError: If the resulting path is invalid or already taken.

        Example:
            ```python
            import asyncio
            from collections.abc import AsyncIterator

            from trpc_rs_py import Router

            router = Router()

            @router.subscription
            async def ticks() -> AsyncIterator[int]:
                yield 1
            ```
        """
        return self._register(ProcedureKind.SUBSCRIPTION, args, name, None)

    def _register(
        self,
        kind: ProcedureKind,
        args: tuple[Any, ...],
        name: str | None,
        rest: RestRoute | None,
    ) -> Callable[..., Any]:
        """Shared registration path for `query` and `mutation`.

        Args:
            kind: Query or mutation semantics for the procedure.
            args: Decorator positional arguments; at most one, the handler
                when the decorator is used bare.
            name: Explicit name override, or `None` to use `__name__`.
            rest: Optional `RestRoute` REST/OpenAPI opt-in metadata.

        Returns:
            The handler unchanged when used bare, otherwise the decorator.

        Raises:
            TypeError: If more than one positional argument is given or the
                single positional argument is not callable.
            ValueError: If the path is invalid or already registered.

        Example:
            ```python
            from trpc_rs_py import ProcedureKind, Router

            router = Router()

            @router.query
            def health() -> bool:
                return True

            assert router.procedures["health"].kind is ProcedureKind.QUERY
            ```
        """
        if len(args) > 1:
            msg = (
                f"@router.{kind.value} takes at most one positional argument: "
                "the decorated function"
            )
            raise TypeError(msg)
        func: Callable[..., Any] | None = args[0] if args else None

        def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
            """Validate, build the `Procedure`, and insert it into the router."""
            path = name if name is not None else handler.__name__
            _validate_name(path, what="procedure path")
            if path in self._procedures:
                msg = f"Procedure {path!r} is already registered"
                raise ValueError(msg)
            hints = type_hints_of(handler)
            signature = _signature_of(handler)
            if rest is not None:
                missing = [
                    name
                    for name in rest.path_parameters
                    if signature is None or name not in signature.parameters
                ]
                if missing:
                    msg = (
                        f"REST path {rest.path!r} binds {missing} but "
                        f"{handler.__name__}() has no such parameters"
                    )
                    raise ValueError(msg)
            self._procedures[path] = Procedure(
                path=path,
                kind=kind,
                handler=handler,
                input_annotation=_input_annotation_of(hints, signature),
                output_annotation=hints.get("return"),
                errors=(),
                rest=rest,
            )
            return handler

        if func is None:
            return decorate
        if not callable(func):
            msg = (
                f"@router.{kind.value} expects a function; got "
                f"{type(func).__name__}. Use @router.{kind.value} or "
                f"@router.{kind.value}(name=...)"
            )
            raise TypeError(msg)
        return decorate(func)
