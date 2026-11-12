"""Edge-case tests for `trpc_rs_py.pyclient` — annotation/default rendering.

Covers the paths `test_pyclient.py` misses: `_py_identifier`'s digit
prefixing, colliding method names, union/Literal/Callable/Annotated
rendering, the `None` fallbacks for unrenderable annotations (locals,
>32-deep generics, opaque origins), exotic parameter defaults, and the
positional-only/keyword-only/`**kwargs` signature kinds.

No `from __future__ import annotations` here on purpose: handler
annotations must evaluate to real objects so locally-defined classes reach
the generator as `<locals>` types.
"""

import math
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Annotated, Any, Concatenate, Literal, ParamSpec, Protocol

import pytest

from trpc_rs_py import Router
from trpc_rs_py.pyclient import generate_python_client


class Shade(Enum):
    """Module-level enum for `Literal[Shade.RED]` member rendering."""

    RED = "red"


P = ParamSpec("P")

_DEEP: Any = int
for _ in range(40):
    _DEEP = list[_DEEP]
"""A >32-deep nested generic — exceeds the recursion cap in _annotation_source."""


def test_digit_path_and_colliding_method_names() -> None:
    """Digit-leading paths get `_`-prefixed; colliding names get suffixes."""
    router = Router()

    @router.query(name="9lives")
    def nine() -> str:
        return "x"

    @router.query(name="a.b")
    def dotted() -> str:
        return "x"

    @router.query(name="a_b")
    def underscored() -> str:
        return "x"

    source = generate_python_client(router)
    assert "def _9lives(self)" in source
    assert "def a_b(self)" in source
    assert "def a_b_2(self)" in source


def test_annotation_source_unions_literals_callables() -> None:
    """Union/Annotated/Literal/Callable/Iterable/Protocol/str/None rendering."""
    router = Router()

    @router.query
    def f(
        u: int | str,
        meta: Annotated[int, "opaque constraint"],
        lit: Literal["a", 1, Shade.RED],
        cb: Callable[[int, str], bool],
        any_cb: Callable[..., int],
        it: Iterable,
        proto: Protocol,
        opt: "nope",  # noqa: F821 - deliberately unresolvable forward ref
        flag: None = None,
    ) -> None:
        return None

    source = generate_python_client(router)
    signature_line = next(line for line in source.splitlines() if line.startswith("    def f("))
    assert "u: int | str" in signature_line
    assert "meta: int" in signature_line  # Annotated unwraps to the base
    assert "lit: Literal['a', 1, test_pyclient_edges.Shade.RED]" in signature_line
    assert "cb: Callable[[int, str], bool]" in signature_line
    assert "any_cb: Callable[..., int]" in signature_line
    assert "it: Iterable" in signature_line
    assert "proto: Protocol" in signature_line
    assert "opt: 'nope'" in signature_line  # unresolved strings pass through
    assert "flag: None = None" in signature_line
    assert signature_line.endswith("-> None:")
    # The enum member and the ABC names become real imports.
    assert "import test_pyclient_edges" in source
    assert "Callable" in source.split("from collections.abc import")[1].splitlines()[0]
    assert "Iterable" in source.split("from collections.abc import")[1].splitlines()[0]
    typing_line = next(
        line for line in source.splitlines() if line.startswith("from typing import")
    )
    assert "Literal" in typing_line
    assert "Protocol" in typing_line


def test_unrenderable_annotations_omit_the_annotation() -> None:
    """`<locals>` types, >32-deep generics, and opaque origins → bare params."""
    router = Router()

    class Local:
        """A class whose qualname can never be written as module source."""

    @router.query
    def f(
        x: Local,
        deep: _DEEP,
        cc: Concatenate[int, P],
        cb: Callable[..., Local],
        cb2: Callable[[Local], int],
    ) -> str:
        return ""

    source = generate_python_client(router)
    assert "def f(self, x, deep, cc, cb, cb2) -> str:" in source


def test_local_literal_member_drops_the_annotation() -> None:
    """A Literal member without an importable qualname → untyped param."""
    router = Router()

    class Shade2(Enum):
        GREEN = "green"

    @router.query
    def f(lit: Literal[Shade2.GREEN]) -> str:
        return ""

    assert "def f(self, lit) -> str:" in generate_python_client(router)


def test_defaults_render_to_source() -> None:
    """Finite floats, Ellipsis, and literal-able containers render verbatim."""
    router = Router()

    @router.query
    def f(
        ratio: float = 0.5,
        marker: Any = ...,
        pair: tuple[int, int] = (1, 2),
        table: dict[str, int] = {"a": 1},  # noqa: B006 - testing mutable default rendering
    ) -> str:
        return ""

    source = generate_python_client(router)
    signature_line = next(line for line in source.splitlines() if line.startswith("    def f("))
    assert "ratio: float = 0.5" in signature_line
    assert "marker: Any = ..." in signature_line
    assert "pair: tuple[int, int] = (1, 2)" in signature_line
    assert "table: dict[str, int] = {'a': 1}" in signature_line


def test_nonfinite_and_unrenderable_defaults_rejected() -> None:
    """`nan`/`inf` and non-`literal_eval` defaults refuse with procedure context."""
    router = Router()

    @router.query
    def f(ratio: float = math.nan) -> str:
        return ""

    with pytest.raises(TypeError, match="f"):
        generate_python_client(router)

    bad = Router()

    @bad.query
    def g(frozen: frozenset[int] = frozenset({1})) -> str:
        return ""

    with pytest.raises(TypeError, match="g"):
        generate_python_client(bad)


def test_self_named_parameter_renamed() -> None:
    """A `self` parameter collides with the receiver — renamed `self_`."""
    router = Router()

    @router.query
    def f(self: int) -> int:
        return self

    source = generate_python_client(router)
    assert "def f(self, self_: int) -> int:" in source
    assert 'self._client.query("f", {"self": self_})' in source


def test_parameter_kinds_preserved() -> None:
    """Positional-only `/` and keyword-only `*` markers survive into the stub."""
    router = Router()

    @router.mutation
    def f(a: int, /, b: str, *, c: float) -> str:
        return ""

    source = generate_python_client(router)
    assert "def f(self, a: int, /, b: str, *, c: float) -> str:" in source
    assert 'self._client.mutation("f", {"a": a, "b": b, "c": c})' in source


def test_named_params_plus_kwargs_merge_into_input() -> None:
    """`def g(a, **rest)` packs named keys then spreads the kwargs catch-all."""
    router = Router()

    @router.mutation
    def g(a: int, **rest: Any) -> dict[str, Any]:
        return rest

    source = generate_python_client(router)
    assert "def g(self, a: int, **rest) -> dict[str, Any]:" in source
    assert 'self._client.mutation("g", {"a": a, **rest})' in source
