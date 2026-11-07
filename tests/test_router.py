"""Unit tests for the Python router and procedure API."""

from __future__ import annotations

from typing import Any

import pytest

from trpc_rs_py import ProcedureKind, Router


class User:
    """Dummy return type for annotation checks."""


def test_bare_query_decorator_registers_under_function_name() -> None:
    router = Router()

    @router.query
    def get_user(user_id: int) -> User:
        return User()

    procedure = router.procedures["get_user"]
    assert procedure.path == "get_user"
    assert procedure.kind is ProcedureKind.QUERY
    assert procedure.handler is get_user
    # The bare decorator must not swallow the function: it stays callable.
    assert isinstance(get_user(user_id=1), User)


def test_bare_mutation_decorator_registers_under_function_name() -> None:
    router = Router()

    @router.mutation
    def rename_user(user_id: int) -> str:
        return "ok"

    procedure = router.procedures["rename_user"]
    assert procedure.kind is ProcedureKind.MUTATION
    assert procedure.handler is rename_user


def test_called_decorator_with_name_override() -> None:
    router = Router()

    @router.query(name="user_by_id")
    def get_user(user_id: int) -> User:
        return User()

    assert "user_by_id" in router.procedures
    assert "get_user" not in router.procedures
    assert router.procedures["user_by_id"].handler is get_user

    @router.mutation(name="delete_user")
    def remove(user_id: int) -> None:
        return None

    assert router.procedures["delete_user"].kind is ProcedureKind.MUTATION
    assert router.procedures["delete_user"].handler is remove


def test_annotations_are_captured() -> None:
    router = Router()

    @router.query
    def get_user(user_id: int) -> User:
        return User()

    procedure = router.procedures["get_user"]
    assert procedure.input_annotation is int
    assert procedure.output_annotation is User


def test_unannotated_handler_has_none_annotations() -> None:
    router = Router()

    @router.query
    def no_hints(x):
        return x  # type: ignore[no-untyped-def] # runtime test file, not type-checked

    procedure = router.procedures["no_hints"]
    assert procedure.input_annotation is None
    assert procedure.output_annotation is None


def test_errors_tuple_is_empty_for_now() -> None:
    router = Router()

    @router.query
    def anything() -> int:
        return 0

    assert router.procedures["anything"].errors == ()


def test_duplicate_registration_raises() -> None:
    router = Router()

    @router.query
    def get_user(user_id: int) -> User:
        return User()

    with pytest.raises(ValueError, match="already registered"):

        @router.query
        def get_user(user_id: int) -> User:  # deliberate redefinition
            return User()


def test_duplicate_registration_across_kinds_raises() -> None:
    router = Router()

    @router.query
    def same_path() -> int:
        return 0

    with pytest.raises(ValueError, match="already registered"):

        @router.mutation
        def same_path() -> int:  # deliberate redefinition
            return 0


@pytest.mark.parametrize(
    ("bad_name", "what"),
    [
        ("", "procedure path"),
        ("has space", "procedure path"),
        ("héllo", "procedure path"),
        ("a/b", "procedure path"),
        ("a:b", "procedure path"),
        ("emoji🎉", "procedure path"),
    ],
)
def test_invalid_procedure_path_raises(bad_name: str, what: str) -> None:
    router = Router()
    with pytest.raises(ValueError, match="Invalid procedure path"):
        router.query(name=bad_name)(lambda: 1)  # type: ignore[arg-type]


def test_valid_path_characters_are_accepted() -> None:
    router = Router()

    @router.query(name="a1-Z_9.x-y")
    def proc() -> int:
        return 0

    assert "a1-Z_9.x-y" in router.procedures


def test_merge_replaces_duplicate_paths() -> None:
    base, other = Router(), Router()

    @base.query
    def health() -> bool:
        return True

    @base.query
    def list_users() -> list[str]:
        return []

    @other.query(name="health")
    def health_v2() -> bool:
        return False

    @other.query
    def extra() -> int:
        return 1

    base.merge(other)
    assert base.procedures["health"].handler is health_v2
    assert base.procedures["list_users"].handler is list_users
    assert base.procedures["extra"].handler is extra
    assert set(base.procedures) == {"health", "list_users", "extra"}


def test_nest_composes_dotted_paths() -> None:
    posts, app = Router(), Router()

    @posts.query
    def by_id(post_id: int) -> str:
        return "post"

    @posts.mutation
    def create(title: str) -> str:
        return "created"

    app.nest("posts", posts)
    assert set(app.procedures) == {"posts.by_id", "posts.create"}
    assert app.procedures["posts.by_id"].kind is ProcedureKind.QUERY
    assert app.procedures["posts.by_id"].handler is by_id
    assert app.procedures["posts.create"].kind is ProcedureKind.MUTATION


def test_nested_nest_composes_deeper_paths() -> None:
    leaf, mid, app = Router(), Router(), Router()

    @leaf.query
    def deep() -> int:
        return 1

    mid.nest("sub", leaf)
    app.nest("top", mid)
    assert "top.sub.deep" in app.procedures


def test_nest_empty_prefix_raises() -> None:
    router, other = Router(), Router()
    with pytest.raises(ValueError, match="Invalid nest prefix"):
        router.nest("", other)


def test_nest_invalid_prefix_raises() -> None:
    router, other = Router(), Router()
    for bad_prefix in ("has space", "a/b", "héllo"):
        with pytest.raises(ValueError, match="Invalid nest prefix"):
            router.nest(bad_prefix, other)


def test_procedures_mapping_is_read_only() -> None:
    router = Router()

    @router.query
    def health() -> bool:
        return True

    with pytest.raises(TypeError):
        router.procedures["injected"] = router.procedures["health"]  # type: ignore[index]


def test_procedures_mapping_contents() -> None:
    router = Router()

    @router.query
    def get_user(user_id: int) -> User:
        return User()

    procedures: dict[str, Any] = dict(router.procedures)
    assert set(procedures) == {"get_user"}
    procedure = procedures["get_user"]
    assert procedure.path == "get_user"
    assert procedure.kind is ProcedureKind.QUERY
    assert procedure.input_annotation is int
    assert procedure.output_annotation is User


def test_non_callable_positional_argument_raises() -> None:
    router = Router()
    with pytest.raises(TypeError, match="expects a function"):
        router.query("not_a_function")  # type: ignore[call-overload]


def test_routers_are_independent() -> None:
    first, second = Router(), Router()

    @first.query
    def health() -> bool:
        return True

    assert second.procedures == {}
    assert "health" in first.procedures


def test_nesting_a_router_into_itself_is_safe() -> None:
    """Self-nesting must not mutate the mapping being iterated (review round 1)."""
    from trpc_rs_py import Router

    router = Router()

    @router.query
    def health() -> bool:
        return True

    router.nest("self", router)
    assert "health" in router.procedures
    assert "self.health" in router.procedures
