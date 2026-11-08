"""Pydantic v2 validators built from procedure handler annotations.

Input validation uses a model created once per handler, with Pydantic's
normal (lax) coercion. Unknown object keys are rejected unless the handler
accepts ``**kwargs``; unannotated parameters and extra keys accepted through
``**kwargs`` are passed through without validation. Validators return a pair
of positional and keyword arguments; ``*args``-only handlers accept a JSON
list as positional arguments, a scalar as one argument, and null as no args.
Validation errors omit
input values and context. Output errors deliberately discard Pydantic details.

Example:
    ```python
    from trpc_rs_py.validation import build_input_validator

    def greet(name: str) -> str:
        return name

    validate = build_input_validator(greet)
    assert validate is not None
    assert validate({"name": "Ada"}) == ((), {"name": "Ada"})
    ```
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from typing import Any, TypeAlias, cast

from pydantic import ConfigDict, TypeAdapter, ValidationError, create_model

from .errors import TRPCError, TRPCErrorCode

__all__ = ["InputValidator", "OutputValidator", "build_input_validator", "build_output_validator"]

InputValidator: TypeAlias = Callable[[Any], tuple[tuple[Any, ...], dict[str, Any]]]
"""Callable that validates parsed input and returns an ``(args, kwargs)`` pair.

Example:
    ```python
    validator: InputValidator
    ```
"""
OutputValidator: TypeAlias = Callable[[Any], Any]
"""Callable that validates a procedure's return value.

Example:
    ```python
    validator: OutputValidator
    ```
"""


def _hints(handler: Callable[..., Any]) -> dict[str, Any]:
    """Resolve handler annotations, preserving `Annotated` metadata."""
    try:
        return typing.get_type_hints(handler, include_extras=True)
    except (NameError, TypeError, ValueError):
        return dict(getattr(handler, "__annotations__", {}))


def _format_location(location: tuple[Any, ...], known_fields: set[str]) -> str:
    """Format the known handler field at a location's root only.

    Deeper components may be arbitrary dictionary keys or list indices from
    the request, so they are deliberately omitted.
    """
    if location and isinstance(location[0], str) and location[0] in known_fields:
        return location[0][:128]
    return "input"


_ERROR_DESCRIPTIONS: dict[str, str] = {
    "missing": "Field required",
    "extra_forbidden": "Extra inputs are not permitted",
    "int_parsing": "Invalid integer",
    "int_type": "Invalid integer",
    "float_parsing": "Invalid number",
    "float_type": "Invalid number",
    "string_type": "Invalid string",
    "bool_parsing": "Invalid boolean",
    "bool_type": "Invalid boolean",
}


def _error_description(error_type: Any) -> str:
    """Return a fixed, value-free description for a Pydantic error type."""
    if isinstance(error_type, str):
        return _ERROR_DESCRIPTIONS.get(error_type, "Invalid value")
    return "Invalid value"


def build_input_validator(handler: Callable[..., Any]) -> InputValidator | None:
    """Build a cached Pydantic input validator from ``handler``'s signature.

    Required annotated parameters become required model fields; parameters
    with defaults retain those defaults. Unannotated parameters are presence-
    checked and passed through unchanged. Positional-only values are returned
    in ``args``; positional-or-keyword and keyword-only values are returned in
    ``kwargs``. A ``*args``-only handler maps a JSON list to positional args,
    a scalar to one positional arg, and ``None`` to no args. Unknown object
    keys are forbidden unless the handler accepts ``**kwargs``. Pydantic's
    default lax coercion is used (for example, ``"1"`` validates as ``1`` for
    an ``int`` field). A handler with no parameters returns ``None`` and keeps
    the adapter's legacy invocation behavior.

    Args:
        handler: The registered procedure callable.

    Returns:
        A validator returning a positional/keyword invocation plan, or
        ``None`` when the handler has no parameters.

    Example:
        ```python
        from trpc_rs_py.validation import build_input_validator

        def lookup(user_id: int) -> str:
            return str(user_id)

        validator = build_input_validator(lookup)
        assert validator is not None
        assert validator({"user_id": "7"}) == ((), {"user_id": 7})
        ```
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return None

    parameters = list(signature.parameters.values())
    if not parameters:
        return None

    hints = _hints(handler)
    fields: dict[str, tuple[Any, Any]] = {}
    passed_names: set[str] = set()
    accepts_extra = False
    has_var_positional = False
    ordinary_parameters: list[inspect.Parameter] = []
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            accepts_extra = True
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
            continue
        ordinary_parameters.append(parameter)
        passed_names.add(parameter.name)
        annotation = hints.get(parameter.name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            continue
        default = parameter.default if parameter.default is not inspect.Parameter.empty else ...
        fields[parameter.name] = (annotation, default)

    annotated_names = set(fields)
    unannotated_required = {
        parameter.name
        for parameter in ordinary_parameters
        if parameter.name not in annotated_names and parameter.default is inspect.Parameter.empty
    }
    varargs_only = has_var_positional and not ordinary_parameters
    adapter: TypeAdapter[Any] | None = None
    if fields:
        model = create_model(
            f"{getattr(handler, '__name__', 'Procedure')}Input",
            __config__=ConfigDict(
                extra="allow" if accepts_extra else "forbid",
                populate_by_name=True,
            ),
            **cast(Any, fields),
        )
        adapter = TypeAdapter(model)

    def validate(input_value: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Validate JSON input and produce args/kwargs for the handler."""
        if varargs_only and not isinstance(input_value, dict):
            if input_value is None:
                return (), {}
            if isinstance(input_value, list):
                return tuple(cast(Any, input_value)), {}
            return (input_value,), {}

        if input_value is None:
            raw_dict: dict[str, Any] = {}
        elif isinstance(input_value, dict):
            raw_dict = cast(dict[str, Any], input_value)
        elif not annotated_names:
            first_positional = next(
                (
                    parameter.name
                    for parameter in ordinary_parameters
                    if parameter.kind
                    in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                ),
                None,
            )
            if first_positional is None:
                raise TRPCError("Invalid input: expected an object", code=TRPCErrorCode.BAD_REQUEST)
            raw_dict = {first_positional: input_value}
        else:
            raise TRPCError("Invalid input: expected an object", code=TRPCErrorCode.BAD_REQUEST)

        if not accepts_extra:
            unknown: list[str] = [key for key in raw_dict if key not in passed_names]
            if unknown:
                raise TRPCError(
                    "Invalid input: input: Extra inputs are not permitted",
                    code=TRPCErrorCode.BAD_REQUEST,
                )
        missing_unannotated = sorted(unannotated_required.difference(raw_dict))
        if missing_unannotated:
            name = missing_unannotated[0][:128]
            raise TRPCError(
                f"Invalid input: {name}: Field required", code=TRPCErrorCode.BAD_REQUEST
            )

        model_input: dict[str, Any] = {
            key: value for key, value in raw_dict.items() if key in annotated_names
        }
        if adapter is None:
            validated_values: dict[str, Any] = {}
        else:
            try:
                validated = adapter.validate_python(model_input)
            except ValidationError as error:
                details = cast(
                    list[dict[str, Any]],
                    error.errors(include_url=False, include_context=False, include_input=False),
                )
                summary = "; ".join(
                    f"{_format_location(tuple(item['loc']), annotated_names)}: "
                    f"{_error_description(item.get('type'))}"
                    for item in details
                )
                message = f"Invalid input: {summary or 'input does not match the expected schema'}"
                raise TRPCError(message, code=TRPCErrorCode.BAD_REQUEST) from None
            except Exception:
                # Some custom validators raise exceptions Pydantic does not
                # convert into ValidationError. They are still input failures;
                # never let their messages escape through the adapter.
                raise TRPCError(
                    "Invalid input: input: Invalid value", code=TRPCErrorCode.BAD_REQUEST
                ) from None
            validated_values = {name: getattr(validated, name) for name in annotated_names}

        kwargs: dict[str, Any] = {}
        args: list[Any] = []
        for parameter in ordinary_parameters:
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                if parameter.name in annotated_names:
                    args.append(validated_values[parameter.name])
                elif parameter.name in raw_dict:
                    args.append(raw_dict[parameter.name])
                else:
                    # Required unannotated parameters were checked above, so
                    # an absent positional-only parameter has a real default.
                    args.append(parameter.default)
            elif parameter.name in annotated_names:
                kwargs[parameter.name] = validated_values[parameter.name]
            elif parameter.name in raw_dict:
                kwargs[parameter.name] = raw_dict[parameter.name]

        # Unannotated extra keys accepted through **kwargs pass unchanged.
        if accepts_extra:
            kwargs.update(
                {key: value for key, value in raw_dict.items() if key not in passed_names}
            )
        return tuple(args), kwargs

    return validate


def build_output_validator(handler: Callable[..., Any]) -> OutputValidator | None:
    """Build a return-value validator from the handler's return annotation.

    Missing annotations and annotations containing only ``None`` disable
    output validation. Pydantic builds the ``TypeAdapter`` once. Validation
    failures intentionally discard all Pydantic details to avoid leaking
    procedure internals; the fixed client-facing message is
    ``"Invalid output from procedure"``.

    Args:
        handler: The registered procedure callable.

    Returns:
        A callable validating output, or ``None`` when no useful return
        annotation exists.

    Example:
        ```python
        from trpc_rs_py.validation import build_output_validator

        def count() -> int:
            return 3

        validator = build_output_validator(count)
        assert validator is not None and validator("3") == 3
        ```
    """
    annotation = _hints(handler).get("return", inspect.Signature.empty)
    if annotation is inspect.Signature.empty or annotation in (None, type(None)):
        return None
    adapter = TypeAdapter(annotation)

    def validate(value: Any) -> Any:
        """Validate one output, replacing validation detail with a fixed error."""
        try:
            validated = adapter.validate_python(value)
            return adapter.dump_python(validated, mode="json")
        except Exception:
            raise TRPCError(
                "Invalid output from procedure", code=TRPCErrorCode.INTERNAL_SERVER_ERROR
            ) from None

    return validate
