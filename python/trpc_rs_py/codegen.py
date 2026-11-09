"""TypeScript declaration generation from a Python `Router`.

`generate_types` walks a `Router`'s procedures and emits the source text of a
TypeScript declaration (`.d.ts`) module that the official `@trpc/client` v11
can consume as its `AppRouter`. Query and mutation paths appear as literal
properties, and every input/output type is mapped from the handler's Python
type annotations — including Pydantic v2 models — to JSON-compatible
TypeScript types. An annotation that cannot be mapped is a hard
`TypeError` naming the procedure; the generator never silently emits `any`.

The generated module imports the router and procedure base interfaces from
`@trpc/server/unstable-core-do-not-import` — the same module the official
docs point at for type inference — so `createTRPCClient<AppRouter>` from
`@trpc/client` (which depends on `@trpc/server` in every tRPC project) gets
fully typed `.query()` and `.mutate()` calls.

Example:
    ```python
    from trpc_rs_py import Router, generate_types

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"

    source = generate_types(router)
    assert "export interface AppRouter" in source
    ```

Write the returned text to an `app-router.d.ts` file and point the client at
it. See `docs/codegen.md` for the full type-mapping table and limitations.
"""

from __future__ import annotations

# The two router helpers below are the same ones the registration path used,
# imported deliberately so codegen cannot drift from the runtime's view of a
# handler's annotations.
# pyright: reportPrivateUsage=false
import datetime
import inspect
import json
import math
import re
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, PlainSerializer, RootModel, SerializeAsAny, WrapSerializer

from .router import (
    Procedure,
    ProcedureKind,
    Router,
    _signature_of,
    _unwrap_awaitable,
    type_hints_of,
)
from .streaming import Tracked

__all__ = ["generate_types"]

_MISSING: Any = object()

_TS_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")
_LEADING_IDENT_CHAR = re.compile(r"[A-Za-z_$]")
_ILLEGAL_IDENT_CHARS = re.compile(r"[^A-Za-z0-9_$]")
_ANNOTATION_LIMIT = 200

# Words TypeScript/JavaScript reserves, which are invalid or dangerous as
# generated `interface`/`type` declaration names (property keys are
# unaffected; reserved words are legal there). This covers:
#
# - ECMAScript reserved words and the strict-mode-only reserved words
#   (declaration files are always strict), plus `await`/`async`.
# - Keyword tokens tsc rejects outright as interface names (`keyof`, `infer`,
#   `readonly`, `typeof`, ...) and the type keywords tsc refuses under TS2427
#   (`never`, `undefined`, `null`, `this`, ...).
# - The built-in type names used by the generated annotations themselves
#   (`Array`, `Record`, `object`, ...) and the common built-in utility/generic
#   type names (`Partial`, `Pick`, `ReadonlyArray`, ...). A generated
#   `export interface Array {}` would silently capture every `Array<T>`
#   reference in the module and break the emitted generic types.
_TS_RESERVED_WORDS = frozenset(
    {
        "Array",
        "Awaited",
        "Capitalize",
        "Exclude",
        "Extract",
        "InstanceType",
        "Lowercase",
        "NonNullable",
        "Omit",
        "Parameters",
        "Partial",
        "Pick",
        "PropertyKey",
        "ReadonlyArray",
        "Record",
        "ReturnType",
        "ThisType",
        "Uncapitalize",
        "Uppercase",
        "accessor",
        "any",
        "asserts",
        "async",
        "await",
        "bigint",
        "boolean",
        "break",
        "case",
        "catch",
        "class",
        "const",
        "continue",
        "debugger",
        "default",
        "delete",
        "do",
        "else",
        "enum",
        "export",
        "extends",
        "false",
        "finally",
        "for",
        "function",
        "if",
        "implements",
        "import",
        "in",
        "infer",
        "instanceof",
        "interface",
        "is",
        "keyof",
        "let",
        "never",
        "new",
        "null",
        "number",
        "object",
        "package",
        "private",
        "protected",
        "public",
        "readonly",
        "return",
        "satisfies",
        "static",
        "string",
        "super",
        "switch",
        "symbol",
        "this",
        "throw",
        "true",
        "try",
        "typeof",
        "undefined",
        "unique",
        "unknown",
        "using",
        "var",
        "void",
        "while",
        "with",
        "yield",
    }
)

_LIST_LIKE = (list, Sequence)
_SET_LIKE = (set, frozenset)
_DICT_LIKE = (dict, Mapping)
_STRING_TYPES = (datetime.datetime, datetime.date, datetime.time, uuid.UUID)

# Origins accepted as a subscription's return annotation; the first type
# argument is the yielded item type (`AsyncGenerator`'s send type is ignored,
# matching how `AsyncGenerator[T, S]` maps to the item type `T`).
_ASYNC_ITERABLE_ORIGINS = (AsyncIterator, AsyncIterable, AsyncGenerator)
# Wrappers an `async def` handler may annotate when it returns a prebuilt
# async iterable: `Awaitable[AsyncIterator[T]]` / `Coroutine[*, *, X]`.
_AWAITABLE_ORIGINS = (Awaitable, Coroutine)

# Names the generated module itself defines or imports; user model classes
# and procedures must never shadow them.
_RESERVED = frozenset(
    {
        "AppRouter",
        "AppRouterRecord",
        "AppRouterRootTypes",
        "DefaultErrorShape",
        "MutationProcedure",
        "ProcedureMap",
        "QueryProcedure",
        "Router",
        "RouterInputs",
        "RouterOutputs",
        "SubscriptionProcedure",
        # Inference helpers imported by non-empty generated routers.
        "inferRouterInputs",
        "inferRouterOutputs",
    }
)

_HEADER = """\
// Generated by trpc-rs-py. Do not edit by hand; regenerate with
// `generate_types(router)` from the `trpc_rs_py` package (docs/codegen.md).
//
// The base interfaces come from the official tRPC v11 server types, so
// `createTRPCClient<AppRouter>` / `createTRPCProxyClient<AppRouter>` from
// `@trpc/client` infer exact input and output types for every procedure.
// Inputs are validated server-side with Pydantic's default lax coercion and
// outputs are JSON-serialized, so every type below is JSON-compatible."""

_IMPORTS = """\
import type {{
  {names},
}} from "@trpc/server/unstable-core-do-not-import";"""

_ROOT_TYPES = """\
export type AppRouterRootTypes = {
  ctx: object;
  meta: object;
  errorShape: DefaultErrorShape;
  // The Python server sends plain JSON (identity transformer), so the client
  // sees exactly the JSON-serialized values described here.
  transformer: false;
};"""


def _limit(text: str) -> str:
    """Return `text` truncated to a bounded length for error messages.

    Example:
        ```python
        from trpc_rs_py.codegen import _limit

        assert len(_limit("x" * 500)) == 200
        ```
    """
    return text if len(text) <= _ANNOTATION_LIMIT else f"{text[: _ANNOTATION_LIMIT - 3]}..."


def _is_one_of(value: object, options: tuple[type, ...]) -> bool:
    """Return whether `value` is one of `options` by identity.

    Example:
        ```python
        from trpc_rs_py.codegen import _is_one_of

        assert _is_one_of(list, (list, dict))
        assert not _is_one_of(int, (list, dict))
        ```
    """
    return any(value is option for option in options)


def _ts_identifier(name: str) -> str:
    """Reduce `name` to a safe TypeScript identifier fragment.

    TypeScript reserved words are prefixed with `_` so a model called
    `class` becomes `_class` instead of the invalid declaration
    `export interface class`.

    Args:
        name: A Python-side name (class name or path fragment).

    Returns:
        An identifier fragment safe to embed in generated TypeScript.

    Example:
        ```python
        from trpc_rs_py.codegen import _ts_identifier

        assert _ts_identifier("post-list") == "post_list"
        assert _ts_identifier("9lives") == "_9lives"
        assert _ts_identifier("class") == "_class"
        ```
    """
    cleaned = _ILLEGAL_IDENT_CHARS.sub("_", name)
    if not cleaned or not _LEADING_IDENT_CHAR.match(cleaned[0]):
        cleaned = f"_{cleaned}"
    if cleaned in _TS_RESERVED_WORDS:
        cleaned = f"_{cleaned}"
    return cleaned


def _ts_key(name: str) -> str:
    r"""Render `name` as a TypeScript property key, quoted when not an identifier.

    Args:
        name: A Python-side property name (parameter, field, or path part).

    Returns:
        The key text; string-escaped (via `json.dumps`) when needed, so
        arbitrary names can never inject TypeScript source.

    Example:
        ```python
        from trpc_rs_py.codegen import _ts_key

        assert _ts_key("post_id") == "post_id"
        assert _ts_key('weird"key') == '"weird\\"key"'
        ```
    """
    return name if _TS_IDENTIFIER.fullmatch(name) else json.dumps(name)


def _ts_literal(value: object, where: str) -> str:
    """Render one JSON scalar as a TypeScript literal token.

    Args:
        value: A `str`, `int`, `float`, `bool`, or `None` value.
        where: Human-readable location used in error messages.

    Returns:
        The literal token, e.g. `"a b"` or `true`.

    Raises:
        TypeError: If the value is not a JSON scalar (or a non-finite float).

    Example:
        ```python
        from trpc_rs_py.codegen import _ts_literal

        assert _ts_literal("a b", "where") == '"a b"'
        assert _ts_literal(True, "where") == "true"
        ```
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"{where}: literal {value!r} is not a JSON number"
            raise TypeError(msg)
        return repr(value)
    msg = f"{where}: literal of type {type(value).__name__} is not JSON-compatible"
    raise TypeError(msg)


class _Generator:
    """Accumulates interface names and model bodies while mapping annotations.

    Example:
        ```python
        from trpc_rs_py.codegen import _Generator

        generator = _Generator()
        assert generator.map_annotation(int, "where") == "number"
        ```
    """

    def __init__(self) -> None:
        """Create an empty generator state.

        Example:
            ```python
            from trpc_rs_py.codegen import _Generator

            assert _Generator().model_sources == []
            ```
        """
        self._used_names: set[str] = set(_RESERVED)
        self._model_names: dict[int, str] = {}
        self._model_bodies: list[tuple[str, list[str]]] = []
        self._registered_models: list[type[BaseModel]] = []

    @property
    def model_sources(self) -> list[tuple[str, list[str]]]:
        """The collected `(interface name, member lines)` pairs, in discovery order.

        Example:
            ```python
            from trpc_rs_py.codegen import _Generator

            assert _Generator().model_sources == []
            ```
        """
        return self._model_bodies

    def unique_name(self, raw: str) -> str:
        """Return a sanitized, collision-free TypeScript identifier for `raw`.

        Args:
            raw: A Python-side name (class name or procedure path fragment).

        Returns:
            An identifier never returned before and never in the reserved set.

        Example:
            ```python
            from trpc_rs_py.codegen import _Generator

            generator = _Generator()
            assert generator.unique_name("post-list") == "post_list"
            assert generator.unique_name("post-list") == "post_list_2"
            ```
        """
        base = _ts_identifier(raw)
        name = base
        counter = 2
        while name in self._used_names:
            name = f"{base}_{counter}"
            counter += 1
        self._used_names.add(name)
        return name

    def map_annotation(self, annotation: object, where: str) -> str:
        """Map a Python type annotation to its JSON-compatible TypeScript type.

        Supports the primitives, `Literal`, `Enum`, unions (including
        `Optional`), lists/sequences/sets, tuples, string-keyed dicts and
        mappings, the ISO-string date/UUID family, and Pydantic v2 models
        (nested models, defaults, and `Annotated` metadata). Anything else
        raises `TypeError`; `any` is never emitted silently.

        Args:
            annotation: The resolved annotation object.
            where: Human-readable location used in error messages.

        Returns:
            TypeScript type text, e.g. `"Array<string>"`.

        Raises:
            TypeError: If the annotation has no accurate JSON-compatible
                TypeScript mapping.

        Example:
            ```python
            from trpc_rs_py.codegen import _Generator

            generator = _Generator()
            assert generator.map_annotation(int | None, "where") == "number | null"
            ```
        """
        origin = get_origin(annotation)
        annotation_repr = repr(annotation)
        if origin is Annotated:
            args = get_args(annotation)
            # Validation-only metadata (Field() constraints, validators) does
            # not change the JSON shape; serializer metadata would, so it is
            # rejected. The underlying type decides the mapping.
            self._check_annotated_metadata(args[1:], where)
            return self.map_annotation(args[0], where)
        if annotation is None or annotation is NoneType:
            return "null"
        if origin is Union or origin is UnionType:
            members = [self.map_annotation(arg, where) for arg in get_args(annotation)]
            return " | ".join(dict.fromkeys(members))
        if annotation is bool:
            return "boolean"
        if annotation is int:
            return "number"
        if annotation is float:
            return "number"
        if annotation is str:
            return "string"
        if _is_one_of(annotation, _LIST_LIKE) or _is_one_of(annotation, _SET_LIKE):
            return "Array<unknown>"
        if _is_one_of(annotation, _DICT_LIKE):
            return "Record<string, unknown>"
        if _is_one_of(annotation, _STRING_TYPES):
            return "string"
        args = get_args(annotation)
        if origin is Literal:
            if not args:
                msg = f"{where}: empty Literal"
                raise TypeError(msg)
            tokens = [_ts_literal(value, where) for value in args]
            return " | ".join(dict.fromkeys(tokens))
        if origin in _LIST_LIKE or origin in _SET_LIKE:
            if len(args) != 1:
                msg = f"{where}: unsupported container annotation {_limit(repr(annotation))}"
                raise TypeError(msg)
            return f"Array<{self.map_annotation(args[0], where)}>"
        if origin is tuple:
            if not args or args == (Ellipsis,):
                msg = f"{where}: unsupported tuple annotation {_limit(repr(annotation))}"
                raise TypeError(msg)
            if args[-1] is Ellipsis:
                if len(args) != 2:
                    msg = f"{where}: unsupported tuple annotation {_limit(repr(annotation))}"
                    raise TypeError(msg)
                return f"Array<{self.map_annotation(args[0], where)}>"
            elements = ", ".join(self.map_annotation(arg, where) for arg in args)
            return f"[{elements}]"
        if origin in _DICT_LIKE:
            if len(args) != 2:
                msg = f"{where}: unsupported mapping annotation {_limit(repr(annotation))}"
                raise TypeError(msg)
            if self.map_annotation(args[0], where) != "string":
                msg = (
                    f"{where}: mapping keys must be strings for JSON objects; "
                    f"got {_limit(repr(annotation))}"
                )
                raise TypeError(msg)
            return f"Record<string, {self.map_annotation(args[1], where)}>"
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            tokens = [
                _ts_literal(member.value, where) for member in annotation.__members__.values()
            ]
            unique = dict.fromkeys(tokens)
            if not unique:
                msg = f"{where}: enum {annotation.__name__!r} has no members"
                raise TypeError(msg)
            return " | ".join(unique)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if issubclass(annotation, RootModel):
                msg = (
                    f"{where}: RootModel subclasses serialize as a bare JSON "
                    "scalar or array, not a JSON object, so no object interface "
                    "can describe them; annotate the handler with the root "
                    "type of the RootModel directly"
                )
                raise TypeError(msg)
            return self._register_model(annotation, where)
        msg = (
            f"{where}: unsupported annotation {_limit(annotation_repr)}; "
            "no JSON-compatible TypeScript type exists for it"
        )
        raise TypeError(msg)

    def _register_model(self, model: type[BaseModel], where: str) -> str:
        """Register a Pydantic model interface and recursively map its fields.

        Args:
            model: The Pydantic v2 model class.
            where: Human-readable location used in error messages.

        Returns:
            The interface name assigned to the model.

        Raises:
            TypeError: If a field annotation cannot be mapped, if the field is
                excluded from serialization, if the model carries a custom
                serializer, computed field, or ``RootModel`` shape change, or
                if its input and output wire names cannot be represented by
                one TypeScript property.

        Example:
            ```python
            from pydantic import BaseModel

            from trpc_rs_py.codegen import _Generator

            class Post(BaseModel):
                title: str

            generator = _Generator()
            assert generator._register_model(Post, "where") == "Post"
            ```
        """
        existing = self._model_names.get(id(model))
        if existing is not None:
            return existing
        name = self.unique_name(model.__name__)
        self._model_names[id(model)] = name
        self._check_model_shape(model)
        lines: list[str] = []
        for field_name, info in model.model_fields.items():
            annotation = info.annotation
            if annotation is None:
                msg = f"model {model.__name__!r} field {field_name!r}: untyped field"
                raise TypeError(msg)
            field_where = f"model {model.__name__!r} field {field_name!r}"
            if self._contains_temporal(annotation):
                temporal_mode = model.model_config.get("ser_json_temporal")
                if temporal_mode not in (None, "iso8601"):
                    msg = (
                        f"{field_where}: ser_json_temporal={temporal_mode!r} changes "
                        "date/time values from ISO strings to numbers, which "
                        "TypeScript codegen cannot describe accurately. Use the "
                        "default 'iso8601' mode."
                    )
                    raise TypeError(msg)
            self._check_field_serialization(model, field_name, info, field_where)
            key = self._field_wire_name(model, field_name, info, field_where)
            mapped = self.map_annotation(annotation, field_where)
            optional = "" if info.is_required() else "?"
            lines.append(f"  {_ts_key(key)}{optional}: {mapped};")
        self._model_bodies.append((name, lines))
        return name

    def _contains_temporal(self, annotation: object) -> bool:
        """Return whether an annotation contains a date/time wire value.

        Example:
            ```python
            from datetime import datetime

            from trpc_rs_py.codegen import _Generator

            assert _Generator()._contains_temporal(list[datetime])
            ```
        """
        origin = get_origin(annotation)
        if origin is Annotated:
            return self._contains_temporal(get_args(annotation)[0])
        if _is_one_of(annotation, _STRING_TYPES):
            return annotation is not uuid.UUID
        if origin is Union or origin is UnionType:
            return any(self._contains_temporal(item) for item in get_args(annotation))
        return any(self._contains_temporal(item) for item in get_args(annotation))

    def _check_annotated_metadata(self, metadata: tuple[Any, ...], where: str) -> None:
        """Reject `Annotated` metadata that changes JSON serialization shape.

        Pydantic `PlainSerializer` and `WrapSerializer` metadata can change the
        serialized type without changing the validation annotation. Validation
        metadata such as `Field(gt=0)` does not affect the wire type and remains
        supported.

        Args:
            metadata: The extras attached to the `Annotated` type.
            where: Human-readable annotation location.

        Raises:
            TypeError: If serializer metadata is present.

        Example:
            ```python
            from pydantic import PlainSerializer

            from trpc_rs_py.codegen import _Generator

            generator = _Generator()
            try:
                generator._check_annotated_metadata(
                    (PlainSerializer(str, return_type=str),), "procedure value"
                )
            except TypeError as error:
                assert "serializer" in str(error)
            ```
        """
        if any(
            isinstance(item, (PlainSerializer, WrapSerializer)) or type(item) is SerializeAsAny
            for item in metadata
        ):
            msg = (
                f"{where}: Annotated Pydantic serialization metadata can change "
                "the JSON wire type or shape, which TypeScript codegen cannot infer safely. "
                "Remove the serializer or annotate a plain wire type."
            )
            raise TypeError(msg)

    def _check_model_shape(self, model: type[BaseModel]) -> None:
        """Reject model-level features that change the serialized JSON shape.

        The generated interface lists exactly the declared ``model_fields`` as
        object properties. Model-level features that alter that premise or
        change serialized field types are rejected instead of emitting an
        inaccurate type:

        - Custom ``@field_serializer`` / ``@model_serializer`` decorators can
          change the JSON type or shape of a field or of the whole model; the
          generator cannot verify what they emit. ``__pydantic_decorators__``
          aggregates decorators from the whole MRO, so inherited serializers
          are detected too.
        - ``@computed_field`` properties appear in serialized output but have
          no input counterpart, so the interface would miss a wire property.
        - ``extra='allow'`` preserves undeclared fields in output;
          ``json_encoders`` and non-ISO temporal modes can change field wire
          types. These are rejected conservatively.

        Args:
            model: The Pydantic v2 model class about to be mapped.

        Raises:
            TypeError: If the model allows extras, uses custom encoders or
                non-ISO temporal serialization, or carries custom serializers
                or computed fields.

        Example:
            ```python
            from pydantic import BaseModel, computed_field

            from trpc_rs_py.codegen import _Generator

            class Computed(BaseModel):
                value: int

                @computed_field
                @property
                def double(self) -> int:
                    return self.value * 2

            generator = _Generator()
            try:
                generator._check_model_shape(Computed)
            except TypeError as error:
                assert "computed field" in str(error)
            ```
        """
        config = model.model_config
        if config.get("extra") == "allow":
            msg = (
                f"model {model.__name__!r}: extra='allow' preserves undeclared "
                "input fields in serialized output, but the generated interface "
                "only describes declared fields. Use extra='ignore' or 'forbid' "
                "for an accurate TypeScript type."
            )
            raise TypeError(msg)
        if config.get("json_encoders"):
            msg = (
                f"model {model.__name__!r}: custom json_encoders can change "
                "serialized field types, which TypeScript codegen cannot "
                "describe safely. Remove json_encoders or use a separate plain "
                "model for the wire."
            )
            raise TypeError(msg)

        decorators = model.__pydantic_decorators__
        serializer_names = sorted((*decorators.field_serializers, *decorators.model_serializers))
        if serializer_names:
            listed = ", ".join(repr(name) for name in serializer_names)
            msg = (
                f"model {model.__name__!r}: custom serializer(s) {listed} can "
                "change the serialized JSON type or shape of fields or of the "
                "whole model, which the generated interface cannot describe. "
                "Remove the serializer or use a separate plain model for the "
                "wire."
            )
            raise TypeError(msg)
        computed_names = sorted(model.model_computed_fields)
        if computed_names:
            listed = ", ".join(repr(name) for name in computed_names)
            msg = (
                f"model {model.__name__!r}: computed field(s) {listed} appear "
                "in serialized output but are not model fields, so the "
                "generated interface would omit them from the wire. Remove "
                "the computed field or use a separate plain model for the "
                "wire."
            )
            raise TypeError(msg)

    def _check_field_serialization(
        self,
        model: type[BaseModel],
        field_name: str,
        info: Any,
        where: str,
    ) -> None:
        """Reject fields the runtime omits from serialized output.

        The runtime serializes procedure outputs with Pydantic, so a field
        carrying ``Field(exclude=True)`` disappears from the wire while the
        generated interface would still declare it — a required property that
        is never sent. ``Field(exclude_if=...)`` (Pydantic 2.12+) omits the
        field conditionally, which no single TypeScript property can describe
        either, so it is rejected the same way.

        Args:
            model: The Pydantic v2 model class owning the field.
            field_name: The Python field name.
            info: The field's `FieldInfo`.
            where: Human-readable location used in the error message.

        Raises:
            TypeError: If the field is excluded from serialization, always or
                conditionally.

        Example:
            ```python
            from pydantic import BaseModel, Field

            from trpc_rs_py.codegen import _Generator

            class Hidden(BaseModel):
                secret: str = Field(default="x", exclude=True)
                keep: str = "visible"

            generator = _Generator()
            try:
                generator._register_model(Hidden, "where")
            except TypeError as error:
                assert "exclude" in str(error)
            ```
        """
        if info.exclude:
            msg = (
                f"{where}: Field(exclude=True) is not supported by TypeScript "
                "codegen; the runtime omits this field from serialized output, "
                "so no TypeScript property can describe it. Remove exclude=True "
                "or use a separate model for output."
            )
            raise TypeError(msg)
        if getattr(info, "exclude_if", None) is not None:
            msg = (
                f"{where}: Field(exclude_if=...) is not supported by TypeScript "
                "codegen; the runtime omits this field from serialized output "
                "whenever its predicate matches, so no single TypeScript "
                "property can describe it. Remove exclude_if or use a "
                "separate model for output."
            )
            raise TypeError(msg)

    def _field_wire_name(
        self,
        model: type[BaseModel],
        field_name: str,
        info: Any,
        where: str,
    ) -> str:
        """Return the TypeScript property key that describes the field's wire name.

        A single interface property is accurate for both directions exactly
        when one key is both accepted at input and used by serialization:

        - Input validation of a nested model follows the model's effective
          ``validate_by_alias`` / ``validate_by_name`` config flags
          (``validate_by_alias`` defaults to true; ``validate_by_name``
          falls back to the legacy ``populate_by_name``). With an alias, the
          accepted input keys are the alias when ``validate_by_alias`` and
          the Python field name when ``validate_by_name``. Without an alias,
          the field name is always accepted. A configuration that accepts no
          plain key at all is rejected.
        - Output serialization emits the Python field name, unless the model
          sets ``serialize_by_alias=True``, in which case it emits
          ``serialization_alias`` (falling back to ``alias``).

        The key is the serialization name, which must be one of the accepted
        input names. Asymmetric configurations (for example
        ``validate_by_alias=False`` with ``serialize_by_alias=True``, where
        input takes the field name but output emits the alias, or
        ``Field(serialization_alias="w")`` on a model with
        ``serialize_by_alias=True`` but no matching input alias) are rejected
        with a `TypeError`. Non-string ``validation_alias`` values
        (`AliasPath`/`AliasChoices`) are always rejected: they make the
        runtime accept several or nested input keys, which no single property
        can describe.

        Args:
            model: The Pydantic v2 model class owning the field.
            field_name: The Python field name.
            info: The field's `FieldInfo`.
            where: Human-readable location used in the error message.

        Returns:
            The wire name to use as the generated property key.

        Raises:
            TypeError: If the input and output wire names cannot be
                represented by a single TypeScript property.

        Example:
            ```python
            from pydantic import BaseModel, ConfigDict, Field

            from trpc_rs_py.codegen import _Generator

            class Symmetric(BaseModel):
                model_config = ConfigDict(serialize_by_alias=True)

                value: str = Field(default="x", alias="wireValue")

            generator = _Generator()
            info = Symmetric.model_fields["value"]
            assert generator._field_wire_name(
                Symmetric, "value", info, "where"
            ) == "wireValue"
            ```
        """
        input_names = self._input_wire_names(model, field_name, info)
        if input_names is None:
            msg = (
                f"{where}: validation alias {info.validation_alias!r} is not "
                "supported by TypeScript codegen; AliasPath and AliasChoices "
                "make the runtime accept several or nested input keys, so no "
                "single TypeScript property describes them. Use a plain string "
                "alias or remove it."
            )
            raise TypeError(msg)
        if not input_names:
            msg = (
                f"{where}: the model's alias configuration accepts no plain "
                "string input key for this field, so no TypeScript property "
                "describes it. Enable validate_by_name (or the legacy "
                "populate_by_name) or validate_by_alias."
            )
            raise TypeError(msg)
        output_name = self._output_wire_name(model, field_name, info)
        if output_name is None:
            msg = (
                f"{where}: serialization alias {info.serialization_alias!r} is "
                "not a plain string, which TypeScript codegen cannot represent"
            )
            raise TypeError(msg)
        if output_name not in input_names:
            accepted = ", ".join(sorted(repr(name) for name in input_names))
            msg = (
                f"{where}: input and output wire names differ. Input "
                f"validation accepts {accepted}; serialization emits "
                f"{output_name!r}, so no single TypeScript property describes "
                "both directions. Make the alias match the field name, enable "
                "validate_by_name (or the legacy populate_by_name), or align "
                "serialize_by_alias with the accepted input name."
            )
            raise TypeError(msg)
        return output_name

    def _input_wire_names(
        self,
        model: type[BaseModel],
        field_name: str,
        info: Any,
    ) -> frozenset[str] | None:
        """Return the input keys the runtime accepts for one field.

        The accepted keys follow the model's effective Pydantic config flags
        (verified against the installed Pydantic's nested-model validation):

        - ``validate_by_alias``: defaults to true; only an explicit false
          disables the alias key.
        - ``validate_by_name``: defaults to false, falling back to the legacy
          ``populate_by_name`` flag when unset. In Pydantic 2.11+ both
          ``validate_by_name`` and ``populate_by_name`` make the Python field
          name accepted alongside the alias.

        ``model_config`` carries the whole MRO's merged config, so flags
        inherited from base models are honored.

        Args:
            model: The Pydantic v2 model class owning the field.
            field_name: The Python field name.
            info: The field's `FieldInfo`.

        Returns:
            The accepted input keys (possibly empty when the configuration
            accepts no plain string key), or `None` when the alias is a
            structured `AliasPath`/`AliasChoices`.

        Example:
            ```python
            from pydantic import BaseModel, Field

            from trpc_rs_py.codegen import _Generator

            class Named(BaseModel):
                value: str = Field(default="x", alias="v")

            generator = _Generator()
            info = Named.model_fields["value"]
            assert generator._input_wire_names(Named, "value", info) == frozenset({"v"})
            ```
        """
        config = model.model_config
        validate_by_alias = config.get("validate_by_alias")
        if validate_by_alias is None:
            validate_by_alias = True
        validate_by_name = config.get("validate_by_name")
        if validate_by_name is None:
            # The legacy pre-2.11 switch: populate_by_name=True makes the
            # Python field name accepted alongside the alias.
            validate_by_name = config.get("populate_by_name", False)
        alias = info.validation_alias
        if alias is None:
            alias = info.alias
        if alias is None:
            return frozenset({field_name})
        if not isinstance(alias, str):
            return None
        names: set[str] = set()
        if validate_by_alias:
            names.add(alias)
        if validate_by_name:
            names.add(field_name)
        return frozenset(names)

    def _output_wire_name(
        self,
        model: type[BaseModel],
        field_name: str,
        info: Any,
    ) -> str | None:
        """Return the key serialization emits for one field, or `None`.

        The field name, unless the model sets ``serialize_by_alias=True``, in
        which case ``serialization_alias`` or ``alias`` wins. `None` means a
        non-string serialization alias, which cannot be represented.

        Args:
            model: The Pydantic v2 model class owning the field.
            field_name: The Python field name.
            info: The field's `FieldInfo`.

        Returns:
            The serialization wire name, or `None` when unrepresentable.

        Example:
            ```python
            from pydantic import BaseModel, Field

            from trpc_rs_py.codegen import _Generator

            class Plain(BaseModel):
                value: str = Field(default="x", serialization_alias="v")

            generator = _Generator()
            info = Plain.model_fields["value"]
            assert generator._output_wire_name(Plain, "value", info) == "value"
            ```
        """
        if model.model_config.get("serialize_by_alias", False):
            alias = info.serialization_alias
            if alias is None:
                alias = info.alias
            if alias is not None:
                return alias if isinstance(alias, str) else None
        return field_name


def _build_tree(router: Router) -> dict[str, Any]:
    """Nest the router's dotted procedure paths into a record tree.

    Args:
        router: The router whose procedures are generated.

    Returns:
        A nested mapping; leaves are `Procedure` objects.

    Raises:
        TypeError: If one path is both a procedure and a procedure prefix,
            which cannot be represented as a nested TypeScript record.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.codegen import _build_tree

        router = Router()

        @router.query
        def health() -> bool:
            return True

        assert isinstance(_build_tree(router)["health"], object)
        ```
    """
    tree: dict[str, Any] = {}
    for path, procedure in router.procedures.items():
        parts = path.split(".")
        node = tree
        for index, part in enumerate(parts[:-1]):
            child = node.get(part)
            if isinstance(child, Procedure):
                prefix = ".".join(parts[: index + 1])
                msg = (
                    f"procedure {path!r} cannot be generated: procedure "
                    f"{prefix!r} is registered both as a procedure and as a "
                    "namespace for deeper procedure paths"
                )
                raise TypeError(msg)
            if child is None:
                created: dict[str, Any] = {}
                node[part] = created
                child = created
            node = child
        leaf = parts[-1]
        if leaf in node:
            msg = (
                f"procedure {path!r} cannot be generated: a procedure or "
                f"namespace already exists at path {path!r}"
            )
            raise TypeError(msg)
        node[leaf] = procedure
    return tree


def _procedure_name(generator: _Generator, path: str) -> str:
    """Assign a deterministic TypeScript interface name for a procedure path.

    Args:
        generator: Shared name registry.
        path: The procedure's full dotted path.

    Returns:
        A unique interface name, e.g. `PostsBy_idProcedure`.

    Example:
        ```python
        from trpc_rs_py.codegen import _Generator, _procedure_name

        assert _procedure_name(_Generator(), "posts.by_id") == "PostsBy_idProcedure"
        ```
    """
    raw = "".join(part[:1].upper() + part[1:] for part in path.split("."))
    return generator.unique_name(f"{raw}Procedure")


def _input_members(
    generator: _Generator,
    path: str,
    kind: ProcedureKind,
    handler: Callable[..., Any],
    hints: dict[str, Any],
    indent: str,
) -> str | None:
    r"""Derive the TypeScript input type for one procedure.

    Mirrors the runtime invocation contract in `validation.py`: annotated
    ordinary parameters become object properties (optional when defaulted);
    a handler with no ordinary parameters takes no input. Unannotated
    parameters, `**kwargs` (arbitrary extra keys) and `*args`-only handlers
    (dynamic list/scalar/null input) are rejected instead of being typed
    loosely. For subscriptions, a `lastEventId` parameter is protocol
    resume metadata injected by the adapter — never a client input — so it
    is excluded from the input type entirely.

    Args:
        generator: Shared name/annotation state.
        path: The procedure's full dotted path.
        kind: Query, mutation, or subscription.
        handler: The procedure handler.
        hints: Resolved type hints of the handler.
        indent: Indentation prefix for the emitted property lines.

    Returns:
        TypeScript type text, or `None` when the procedure takes no input.

    Raises:
        TypeError: If the signature cannot be typed exactly.

    Example:
        ```python
        from trpc_rs_py import ProcedureKind
        from trpc_rs_py.codegen import _Generator, _input_members

        def greet(name: str, punctuation: str = "!") -> str:
            return name + punctuation

        hints = {"name": str, "punctuation": str}
        assert _input_members(
            _Generator(), "greet", ProcedureKind.QUERY, greet, hints, "    "
        ) == "{\n      name: string;\n      punctuation?: string;\n    }"
        ```
    """
    signature = _signature_of(handler)
    if signature is None:
        msg = (
            f"procedure {path!r}: cannot inspect the handler's signature; "
            "codegen requires an introspectable function"
        )
        raise TypeError(msg)
    has_var_keyword = False
    has_var_positional = False
    ordinary: list[inspect.Parameter] = []
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
        elif kind is ProcedureKind.SUBSCRIPTION and parameter.name == "lastEventId":
            continue
        else:
            ordinary.append(parameter)
    if has_var_keyword:
        msg = (
            f"procedure {path!r}: **kwargs accepts arbitrary extra keys, which "
            "has no exact TypeScript type; declare explicit parameters instead"
        )
        raise TypeError(msg)
    if has_var_positional and not ordinary:
        msg = (
            f"procedure {path!r}: a *args-only handler accepts a list, a scalar "
            "or null input, which has no exact TypeScript type; declare "
            "explicit parameters instead"
        )
        raise TypeError(msg)
    if not ordinary:
        return None
    lines: list[str] = []
    for parameter in ordinary:
        annotation = hints.get(parameter.name, _MISSING)
        if annotation is _MISSING or annotation is inspect.Parameter.empty:
            msg = (
                f"procedure {path!r}: parameter {parameter.name!r} is "
                "unannotated; add a type annotation so the input can be typed"
            )
            raise TypeError(msg)
        where = f"procedure {path!r} parameter {parameter.name!r}"
        mapped = generator.map_annotation(annotation, where)
        optional = "" if parameter.default is inspect.Parameter.empty else "?"
        lines.append(f"{indent}  {_ts_key(parameter.name)}{optional}: {mapped};")
    body = "\n".join(lines)
    return f"{{\n{body}\n{indent}}}"


def _subscription_item_type(
    generator: _Generator,
    path: str,
    annotation: object,
) -> str:
    """Map one yielded subscription item annotation to its TypeScript type.

    `Tracked[X]` maps to `{id: string; data: X}` — the `TrackedData<X>` shape
    the official client delivers to `onData` for tracked events (the SSE
    `id:` field becomes the `id` member); a bare `Tracked` maps to
    `{id: string; data: unknown}`. The mapping applies at the top level and
    through `Union`/`Annotated` members — a `Tracked` nested deeper (for
    example `list[Tracked[int]]`) is rejected by `map_annotation`, matching
    the runtime where only top-level yielded values are unwrapped.

    Args:
        generator: Shared name/annotation state.
        path: The procedure's full dotted path.
        annotation: The yielded item annotation.

    Returns:
        TypeScript type text for the item.

    Raises:
        TypeError: If the item annotation is unsupported.

    Example:
        ```python
        from collections.abc import AsyncIterator

        from trpc_rs_py import Tracked
        from trpc_rs_py.codegen import _Generator, _subscription_item_type

        assert _subscription_item_type(_Generator(), "s", Tracked[int]) == (
            "{ id: string; data: number; }"
        )
        assert _subscription_item_type(_Generator(), "s", Tracked) == (
            "{ id: string; data: unknown; }"
        )
        ```
    """
    where = f"procedure {path!r} yielded item"
    origin = get_origin(annotation)
    if annotation is Tracked:
        return "{ id: string; data: unknown; }"
    if origin is Tracked:
        args = get_args(annotation)
        if not args or args[0] is Any:
            return "{ id: string; data: unknown; }"
        data = generator.map_annotation(args[0], where)
        return f"{{ id: string; data: {data}; }}"
    if origin is Annotated:
        args = get_args(annotation)
        generator._check_annotated_metadata(args[1:], where)
        return _subscription_item_type(generator, path, args[0])
    if origin is Union or origin is UnionType:
        members = [_subscription_item_type(generator, path, arg) for arg in get_args(annotation)]
        return " | ".join(dict.fromkeys(members))
    if annotation is Any:
        return "unknown"
    return generator.map_annotation(annotation, where)


def _subscription_output_type(
    generator: _Generator,
    path: str,
    annotation: object,
) -> str:
    """Derive the TypeScript `output` type for one subscription procedure.

    The emitted type is `AsyncIterable<T>` so the official client's
    `inferSubscriptionOutput` unwraps it to the yielded item type — the same
    shape a native `@trpc/server` resolver produces. `Awaitable`/`Coroutine`
    wrappers are unwrapped first (an `async def` that returns a prebuilt
    async iterable). Sync `Iterator`/`Iterable`/`Generator` annotations are
    rejected: the adapter requires an async iterable at runtime.

    Args:
        generator: Shared name/annotation state.
        path: The procedure's full dotted path.
        annotation: The handler's return annotation.

    Returns:
        TypeScript type text `AsyncIterable<...>`.

    Raises:
        TypeError: If the annotation is not an async iterable type.

    Example:
        ```python
        from collections.abc import AsyncIterator

        from trpc_rs_py.codegen import _Generator, _subscription_output_type

        assert _subscription_output_type(
            _Generator(), "ticks", AsyncIterator[int]
        ) == "AsyncIterable<number>"
        ```
    """
    unwrapped = _unwrap_awaitable(annotation)
    origin = get_origin(unwrapped)
    if origin in (Iterator, Iterable, Generator) or unwrapped in (Iterator, Iterable, Generator):
        msg = (
            f"procedure {path!r}: subscription handlers must return an async "
            "iterable (annotate `AsyncIterator[T]`, `AsyncIterable[T]` or "
            "`AsyncGenerator[T, S]`); a synchronous iterable cannot be served"
        )
        raise TypeError(msg)
    if origin not in _ASYNC_ITERABLE_ORIGINS:
        msg = (
            f"procedure {path!r}: subscription handlers must return an async "
            "iterable; annotate the return type as `AsyncIterator[T]`, "
            "`AsyncIterable[T]` or `AsyncGenerator[T, S]`, got "
            f"{_limit(repr(annotation))}"
        )
        raise TypeError(msg)
    args = get_args(unwrapped)
    if not args:
        return "AsyncIterable<unknown>"
    return f"AsyncIterable<{_subscription_item_type(generator, path, args[0])}>"


def _output_type(
    generator: _Generator,
    path: str,
    kind: ProcedureKind,
    hints: dict[str, Any],
) -> str:
    """Derive the TypeScript output type for one procedure.

    Args:
        generator: Shared name/annotation state.
        path: The procedure's full dotted path.
        kind: Query, mutation, or subscription.
        hints: Resolved type hints of the handler.

    Returns:
        TypeScript type text; `null` for handlers returning `None`.
        Subscriptions emit `AsyncIterable<item>`.

    Raises:
        TypeError: If the return annotation is missing or unsupported.

    Example:
        ```python
        from trpc_rs_py import ProcedureKind
        from trpc_rs_py.codegen import _Generator, _output_type

        assert _output_type(_Generator(), "f", ProcedureKind.QUERY, {"return": int}) == "number"
        ```
    """
    annotation = hints.get("return", _MISSING)
    if annotation is _MISSING or annotation is inspect.Signature.empty:
        msg = (
            f"procedure {path!r}: return value is unannotated; add a return "
            "annotation so the output can be typed"
        )
        raise TypeError(msg)
    if kind is ProcedureKind.SUBSCRIPTION:
        return _subscription_output_type(generator, path, annotation)
    return generator.map_annotation(annotation, f"procedure {path!r} return")


def _flatten(tree: dict[str, Any], prefix: str = "") -> list[tuple[str, Procedure]]:
    """Flatten the record tree into `(full path, procedure)` pairs, DFS order.

    Tree paths are used instead of `Procedure.path` because `nest` and
    `merge` re-key procedures without rewriting their stored path.

    Args:
        tree: The nested tree from `_build_tree`.
        prefix: Dotted prefix accumulated while descending.

    Returns:
        One `(path, procedure)` pair per leaf, depth-first.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.codegen import _build_tree, _flatten

        router = Router()

        @router.query
        def health() -> bool:
            return True

        assert [path for path, _ in _flatten(_build_tree(router))] == ["health"]
        ```
    """
    flat: list[tuple[str, Procedure]] = []
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Procedure):
            flat.append((path, value))
        else:
            flat.extend(_flatten(value, path))
    return flat


def _emit_record(
    names: Mapping[str, str],
    tree: dict[str, Any],
    indent: str,
    prefix: str = "",
) -> list[str]:
    """Render the nested procedure record as TypeScript object type lines.

    Args:
        names: Assigned interface name per full procedure path (from
            `_procedure_name`), shared with the interface declarations so the
            record references the same names.
        tree: The nested tree from `_build_tree`.
        indent: Current indentation prefix.
        prefix: Dotted path prefix of `tree` while descending.

    Returns:
        The lines of the record body (excluding the outer braces).

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.codegen import _Generator, _build_tree, _emit_record, _procedure_name

        router = Router()

        @router.query
        def health() -> bool:
            return True

        tree = _build_tree(router)
        names = {"health": _procedure_name(_Generator(), "health")}
        lines = _emit_record(names, tree, "")
        assert lines == ["  health: HealthProcedure;"]
        ```
    """
    lines: list[str] = []
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Procedure):
            lines.append(f"{indent}  {_ts_key(key)}: {names[path]};")
        else:
            nested = _emit_record(names, value, f"{indent}  ", path)
            lines.append(f"{indent}  {_ts_key(key)}: {{")
            lines.extend(nested)
            lines.append(f"{indent}  }};")
    return lines


def _emit_procedure_interface(
    procedure: Procedure,
    name: str,
    input_type: str | None,
    output_type: str,
) -> list[str]:
    """Render one procedure's interface declaration.

    Args:
        procedure: The registered procedure.
        name: The assigned TypeScript interface name.
        input_type: Input type text, or `None` for no-input procedures.
        output_type: Output type text.

    Returns:
        The interface's source lines.

    Example:
        ```python
        from trpc_rs_py import Procedure, ProcedureKind
        from trpc_rs_py.codegen import _emit_procedure_interface

        def health() -> bool:
            return True

        proc = Procedure(path="health", kind=ProcedureKind.QUERY, handler=health)
        lines = _emit_procedure_interface(proc, "HealthProcedure", None, "boolean")
        assert lines[0] == "export interface HealthProcedure"
        ```
    """
    base = {
        ProcedureKind.QUERY: "QueryProcedure",
        ProcedureKind.MUTATION: "MutationProcedure",
        ProcedureKind.SUBSCRIPTION: "SubscriptionProcedure",
    }[procedure.kind]
    input_text = input_type if input_type is not None else "undefined"
    return [
        f"export interface {name}",
        f"  extends {base}<{{",
        "    meta: unknown;",
        f"    input: {input_text};",
        f"    output: {output_type};",
        "    errorShape: DefaultErrorShape;",
        "  }> {}",
    ]


def _imports(flat: list[tuple[str, Procedure]]) -> str:
    """Build the type-import block, including only the names actually used.

    Args:
        flat: The `(path, procedure)` pairs from `_flatten`.

    Returns:
        The import statement text.

    Example:
        ```python
        from trpc_rs_py.codegen import _imports

        assert "Router" in _imports([])
        ```
    """
    names = ["DefaultErrorShape", "Router"]
    if any(procedure.kind is ProcedureKind.QUERY for _, procedure in flat):
        names.append("QueryProcedure")
    if any(procedure.kind is ProcedureKind.MUTATION for _, procedure in flat):
        names.append("MutationProcedure")
    if any(procedure.kind is ProcedureKind.SUBSCRIPTION for _, procedure in flat):
        names.append("SubscriptionProcedure")
    if flat:
        names.append("inferRouterInputs")
        names.append("inferRouterOutputs")
    return _IMPORTS.format(names=",\n  ".join(sorted(names)))


def generate_types(router: Router) -> str:
    """Generate the source text of an `AppRouter` TypeScript declaration module.

    The returned text is the content of a `.d.ts` file exporting `AppRouter`
    (usable directly by the official `@trpc/client` v11), plus
    `ProcedureMap`, `RouterInputs` and `RouterOutputs` for standalone type
    inference. Generation is deterministic: the same router always produces
    byte-identical output. Unrepresentable annotations raise `TypeError`
    naming the procedure — the generator never emits `any`.

    Args:
        router: The router whose procedures are generated.

    Returns:
        TypeScript declaration source, ready to write to `app-router.d.ts`.

    Raises:
        TypeError: If any procedure has an unannotated input or return, an
            unsupported annotation, an uninspectable handler, or a path that
            is both a procedure and a prefix of another.

    Example:
        ```python
        from trpc_rs_py import Router, generate_types

        router = Router()

        @router.query
        def health() -> bool:
            return True

        source = generate_types(router)
        assert "health: HealthProcedure;" in source
        ```
    """
    generator = _Generator()
    tree = _build_tree(router)
    flat = _flatten(tree)

    # Assign every procedure interface name up front so the interface
    # declarations and the record reference identical, stable names. Keys are
    # the full tree paths, not `Procedure.path`, because `nest` and `merge`
    # re-key procedures without rewriting their stored path.
    names = {path: _procedure_name(generator, path) for path, _ in flat}

    interfaces: list[str] = []
    for path, procedure in flat:
        hints = type_hints_of(procedure.handler)
        input_type = _input_members(
            generator, path, procedure.kind, procedure.handler, hints, "    "
        )
        output_type = _output_type(generator, path, procedure.kind, hints)
        interfaces.append(
            "\n".join(_emit_procedure_interface(procedure, names[path], input_type, output_type))
        )

    sections: list[str] = [_HEADER, _imports(flat), _ROOT_TYPES]
    for name, lines in generator.model_sources:
        if lines:
            sections.append(f"export interface {name} {{\n" + "\n".join(lines) + "\n}")
        else:
            sections.append(f"export interface {name} {{}}")
    for text in interfaces:
        sections.append(text)
    record_lines = _emit_record(names, tree, "")
    if record_lines:
        sections.append("export type AppRouterRecord = {\n" + "\n".join(record_lines) + "\n};")
    else:
        sections.append("export type AppRouterRecord = {};")
    sections.append(
        "export interface AppRouter extends Router<AppRouterRootTypes, AppRouterRecord> {}"
    )
    sections.append("export type ProcedureMap = AppRouterRecord;")
    if flat:
        sections.append("export type RouterInputs = inferRouterInputs<AppRouter>;")
        sections.append("export type RouterOutputs = inferRouterOutputs<AppRouter>;")
    return "\n\n".join(sections) + "\n"
