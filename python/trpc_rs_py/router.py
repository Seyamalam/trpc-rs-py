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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
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
    """Whether a procedure is a side-effect-free query or a mutation.

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


def _type_hints_of(handler: Callable[..., Any]) -> dict[str, Any]:
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
        from trpc_rs_py._router import _type_hints_of

        def f(x: int) -> str:
            return str(x)

        assert _type_hints_of(f)["return"] is str
        ```
    """
    try:
        return typing.get_type_hints(handler)
    except (NameError, TypeError, ValueError):
        return dict(getattr(handler, "__annotations__", {}))


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

        from trpc_rs_py._router import _input_annotation_of, _type_hints_of

        def f(x: int) -> str:
            return str(x)

        assert _input_annotation_of(_type_hints_of(f), inspect.signature(f)) is int
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
        self, *, name: str | None = ...
    ) -> Callable[[Callable[[Unpack[_Ts]], _R]], Callable[[Unpack[_Ts]], _R]]: ...

    def query(self, *args: Any, name: str | None = None) -> Callable[..., Any]:
        """Register a query procedure; usable bare or with a `name` override.

        Args:
            *args: The decorated function when used bare (`@router.query`).
                At most one positional argument is accepted.
            name: Explicit procedure name; defaults to the function's
                `__name__`.

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
        return self._register(ProcedureKind.QUERY, args, name)

    @overload
    def mutation(self, func: Callable[[Unpack[_Ts]], _R], /) -> Callable[[Unpack[_Ts]], _R]: ...

    @overload
    def mutation(
        self, *, name: str | None = ...
    ) -> Callable[[Callable[[Unpack[_Ts]], _R]], Callable[[Unpack[_Ts]], _R]]: ...

    def mutation(self, *args: Any, name: str | None = None) -> Callable[..., Any]:
        """Register a mutation procedure; usable bare or with a `name` override.

        Args:
            *args: The decorated function when used bare (`@router.mutation`).
                At most one positional argument is accepted.
            name: Explicit procedure name; defaults to the function's
                `__name__`.

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
        return self._register(ProcedureKind.MUTATION, args, name)

    def _register(
        self,
        kind: ProcedureKind,
        args: tuple[Any, ...],
        name: str | None,
    ) -> Callable[..., Any]:
        """Shared registration path for `query` and `mutation`.

        Args:
            kind: Query or mutation semantics for the procedure.
            args: Decorator positional arguments; at most one, the handler
                when the decorator is used bare.
            name: Explicit name override, or `None` to use `__name__`.

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
            hints = _type_hints_of(handler)
            signature = _signature_of(handler)
            self._procedures[path] = Procedure(
                path=path,
                kind=kind,
                handler=handler,
                input_annotation=_input_annotation_of(hints, signature),
                output_annotation=hints.get("return"),
                errors=(),
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
