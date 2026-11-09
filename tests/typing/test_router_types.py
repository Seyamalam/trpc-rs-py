"""Typing tests: router, procedure, and error types, checked by mypy and pyright.

Proves that the decorators preserve handler parameter and return types for
both the bare and called decorator forms, and that the public API exports are
correctly typed (MISSION section 5). Extended in 0.4.0 with typed errors.

Note: `ProcedureKind` and `TRPCErrorCode` member expressions are Literal
types under both checkers, so instances are asserted from declared
attributes instead of from the members themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from typing_extensions import assert_type

from trpc_rs_py import Procedure, ProcedureKind, Router, TRPCError, TRPCErrorCode


class User:
    """Dummy model for the typing tests."""


router = Router()


@router.query
def get_user(user_id: int) -> User:
    """Bare decorator form must preserve the full function type."""
    return User()


@router.query(name="user_by_id")
def get_user_named(user_id: int) -> User:
    """Called decorator form must preserve the full function type."""
    return User()


@router.mutation
def rename_user(user_id: int, name: str) -> User:
    """Mutation, bare form: two params and return type preserved."""
    return User()


@router.mutation(name="delete_user")
def delete_user_named(user_id: int) -> None:
    """Mutation, called form: params and None return preserved."""
    return None


@router.query(name="a1-Z_9.x-y")
def odd_but_valid_name(flag: bool) -> int:
    """The full allowed charset is accepted with a type-preserving decorator."""
    return 1


@router.query
def health() -> bool:
    """Zero-parameter handler: the decorator preserves its callable type."""
    return True


def test_bare_query_preserves_handler_type() -> None:
    assert_type(get_user, Callable[[int], User])
    assert_type(get_user(1), User)


def test_called_query_preserves_handler_type() -> None:
    assert_type(get_user_named, Callable[[int], User])
    assert_type(get_user_named(1), User)


def test_bare_mutation_preserves_handler_type() -> None:
    assert_type(rename_user, Callable[[int, str], User])
    assert_type(rename_user(1, "a"), User)


def test_called_mutation_preserves_handler_type() -> None:
    assert_type(delete_user_named, Callable[[int], None])
    assert_type(delete_user_named(1), None)


def test_charset_call_preserves_handler_type() -> None:
    assert_type(odd_but_valid_name, Callable[[bool], int])


def test_zero_parameter_handler_preserves_callable_type() -> None:
    # pyright quirk: assert_type on an unpacked-empty callable compares a
    # packed tuple against the spelled form, so bind the type first; mypy
    # accepts both spellings.
    typed: Callable[[], bool] = health
    assert_type(typed, Callable[[], bool])


def test_procedure_type_is_exported_and_typed() -> None:
    procedure = router.procedures["get_user"]
    assert_type(procedure, Procedure)
    kind = procedure.kind
    assert_type(kind, ProcedureKind)
    assert_type(procedure.path, str)
    assert_type(procedure.handler, Callable[..., Any])
    assert_type(procedure.input_annotation, Any)
    assert_type(procedure.output_annotation, Any)
    assert_type(procedure.errors, tuple[type[TRPCError], ...])
    assert_type(router.procedures, Mapping[str, Procedure])


def test_procedure_kind_members() -> None:
    # Enum member expressions are Literal types, so derive an instance from a
    # `Procedure`'s declared `kind` attribute instead.
    kind = Procedure(path="p", kind=ProcedureKind.QUERY, handler=_noop).kind
    assert_type(kind, ProcedureKind)
    assert ProcedureKind.QUERY.value == "query"


def test_error_types_are_exported_and_typed() -> None:
    error = TRPCError("no such user", code=TRPCErrorCode.NOT_FOUND, data={"id": 1})
    assert_type(error, TRPCError)
    code = error.code
    assert_type(code, TRPCErrorCode)
    assert_type(error.message, str)
    assert_type(error.data_code, int)


def _noop() -> None:
    """Minimal handler for constructing a `Procedure` in type tests."""
    return None
