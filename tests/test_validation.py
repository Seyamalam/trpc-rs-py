"""Unit tests for Pydantic validators built from procedure annotations."""

from enum import Enum
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field, field_serializer, field_validator

from trpc_rs_py import TRPCError, TRPCErrorCode
from trpc_rs_py.validation import build_input_validator, build_output_validator


class Profile(BaseModel):
    """Nested profile data for validator tests."""

    name: str
    age: int


class Shade(Enum):
    """Color enum used in a validation union."""

    BLUE = "blue"


class FailingSerializerOutput(BaseModel):
    """Output model whose serializer fails with a sensitive detail."""

    value: str

    @field_serializer("value")
    def serialize_value(self, value: str) -> str:
        """Raise a private serialization failure for error normalization tests."""
        raise ValueError("secret serializer token")


def test_input_builder_none_for_zero_parameter_or_no_annotated_fields() -> None:
    def empty() -> str:
        return "ok"

    def unannotated(value: Any) -> Any:
        return value

    unannotated.__annotations__.clear()

    assert build_input_validator(empty) is None
    passthrough = build_input_validator(unannotated)
    assert passthrough is not None
    assert passthrough({"value": "ok"}) == ((), {"value": "ok"})


def test_required_optional_and_defaults() -> None:
    def handler(required: int, optional: str = "default") -> str:
        return f"{required}:{optional}"

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate({"required": "4"}) == (
        (),
        {"required": 4, "optional": "default"},
    )
    with pytest.raises(TRPCError) as raised:
        validate(None)
    assert "required" in str(raised.value)
    assert "Field required" in str(raised.value)


def test_optional_only_typed_parameter_uses_its_default_for_null_input() -> None:
    def handler(label: str = "default") -> str:
        return label

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate(None) == ((), {"label": "default"})


def test_signature_input_uses_python_parameter_names_not_pydantic_aliases() -> None:
    def handler(user_name: Annotated[str, Field(alias="userName")]) -> str:
        return user_name

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate({"user_name": "Ada"}) == ((), {"user_name": "Ada"})
    with pytest.raises(TRPCError) as raised:
        validate({"userName": "Ada"})
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert raised.value.message == "Invalid input: input: Extra inputs are not permitted"


def test_extra_keys_are_forbidden_and_dont_echo_values() -> None:
    def handler(count: int) -> int:
        return count

    validate = build_input_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate({"count": 1, "secret": "do-not-echo"})
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert str(raised.value) == "Invalid input: input: Extra inputs are not permitted"
    assert "do-not-echo" not in str(raised.value)


def test_validation_messages_never_echo_invalid_values() -> None:
    def handler(secret: int) -> int:
        return secret

    validate = build_input_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate({"secret": "private-input-value"})
    assert raised.value.message == "Invalid input: secret: Invalid integer"
    assert "private-input-value" not in raised.value.message
    assert "input_value" not in raised.value.message


def test_custom_validator_and_untrusted_locations_do_not_leak_input() -> None:
    class Credential(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value: str) -> str:
            raise ValueError(f"credential rejected: {value!r}")

    def handler(nested: Credential, mapping: dict[str, int]) -> None:
        del nested, mapping

    validate = build_input_validator(handler)
    assert validate is not None
    credential = "credential\nsecond-line"
    secret_key = "dict-key-secret\nsecond-line"
    with pytest.raises(TRPCError) as raised:
        validate(
            {
                "nested": {"value": credential},
                "mapping": {secret_key: "not-an-integer"},
            }
        )

    message = raised.value.message
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert "nested: Invalid value" in message
    assert "mapping: Invalid integer" in message
    assert credential not in message
    assert "credential rejected" not in message
    assert secret_key not in message
    assert "dict-key-secret" not in message
    assert "second-line" not in message


def test_strict_annotated_field_rejects_lax_coercion_without_echoing_value() -> None:
    def handler(value: Annotated[int, Field(strict=True)]) -> int:
        return value

    validate = build_input_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate({"value": "private-123"})
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert raised.value.message == "Invalid input: value: Invalid integer"
    assert "private-123" not in raised.value.message


def test_annotated_field_constraints_are_preserved_and_coerce_laxly() -> None:
    def handler(value: Annotated[int, Field(gt=0)]) -> int:
        return value

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate({"value": "2"}) == ((), {"value": 2})
    with pytest.raises(TRPCError) as raised:
        validate({"value": -1})
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert raised.value.message == "Invalid input: value: Invalid value"


def test_non_dict_input_is_bad_request_for_annotated_parameters() -> None:
    def handler(count: int) -> int:
        return count

    validate = build_input_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate(["not", "an", "object"])
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert "expected an object" in str(raised.value)
    assert "not" not in str(raised.value)


def test_nested_models_enum_literal_union_and_lax_coercion() -> None:
    def handler(
        profile: Profile, shade: Shade, mode: Literal["fast", "safe"], choice: int | str
    ) -> tuple[Profile, Shade, str, int | str]:
        return profile, shade, mode, choice

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate(
        {
            "profile": {"name": "Ada", "age": "37"},
            "shade": "blue",
            "mode": "fast",
            "choice": "1",
        }
    ) == (
        (),
        {
            "profile": Profile(name="Ada", age=37),
            "shade": Shade.BLUE,
            "mode": "fast",
            "choice": "1",
        },
    )


def test_invocation_plan_separates_positional_only_and_keyword_parameters() -> None:
    def handler(first: int, /, second: str, *, enabled: bool) -> str:
        return f"{first}:{second}:{enabled}"

    validate = build_input_validator(handler)
    assert validate is not None
    invocation = validate({"first": "3", "second": "x", "enabled": True})
    assert invocation == ((3,), {"second": "x", "enabled": True})


def test_unannotated_required_parameters_are_checked_and_kwargs_pass_through() -> None:
    def required(value: int, untyped) -> int:
        return value + untyped

    validate = build_input_validator(required)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate({"value": 1})
    assert raised.value.code is TRPCErrorCode.BAD_REQUEST
    assert raised.value.message == "Invalid input: untyped: Field required"
    invocation = validate({"value": "2", "untyped": 3})
    assert invocation == ((), {"value": 2, "untyped": 3})

    def accepts_extra(value: int, **extra: Any) -> int:
        return value + len(extra)

    extra_validator = build_input_validator(accepts_extra)
    assert extra_validator is not None
    assert extra_validator({"value": "2", "custom": "kept"}) == (
        (),
        {"value": 2, "custom": "kept"},
    )


def test_varargs_only_input_mapping() -> None:
    def handler(*values: Any) -> tuple[Any, ...]:
        return values

    validate = build_input_validator(handler)
    assert validate is not None
    assert validate(4) == ((4,), {})
    assert validate([1, "two"]) == ((1, "two"), {})
    assert validate(None) == ((), {})


def test_output_builder_none_for_missing_or_none_return_annotation() -> None:
    def missing() -> Any:
        return "ok"

    missing.__annotations__.pop("return")

    def returns_none() -> None:
        return None

    assert build_output_validator(missing) is None
    assert build_output_validator(returns_none) is None


def test_output_nested_model_validates() -> None:
    def handler() -> Profile:
        return Profile(name="Ada", age=37)

    validate = build_output_validator(handler)
    assert validate is not None
    assert validate({"name": "Ada", "age": "37"}) == {
        "name": "Ada",
        "age": 37,
    }


def test_output_failure_is_fixed_internal_error() -> None:
    def handler() -> Profile:
        return Profile(name="Ada", age=37)

    validate = build_output_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate({"name": "secret", "age": "not-a-number"})
    assert raised.value.code is TRPCErrorCode.INTERNAL_SERVER_ERROR
    assert raised.value.message == "Invalid output from procedure"
    assert "secret" not in raised.value.message


def test_custom_serializer_failure_is_fixed_without_chaining_details() -> None:
    def handler() -> FailingSerializerOutput:
        return FailingSerializerOutput(value="private")

    validate = build_output_validator(handler)
    assert validate is not None
    with pytest.raises(TRPCError) as raised:
        validate(FailingSerializerOutput(value="private"))
    assert raised.value.code is TRPCErrorCode.INTERNAL_SERVER_ERROR
    assert raised.value.message == "Invalid output from procedure"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "secret serializer token" not in str(raised.value)
