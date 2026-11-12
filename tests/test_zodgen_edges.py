"""Edge-path tests for `trpc_rs_py.zodgen` — defaults, discriminators, refusals.

Covers the branches `test_zodgen.py` does not: model field default
serialization (`_wrap_default`), discriminator detection misses, the
`*args`/`**kwargs`/uninspectable-handler rejection set, union-of-None,
bare containers, non-string mapping keys, interval/length constraints, and
Pydantic-internal metadata handling.
"""

from __future__ import annotations

import datetime
import types
from collections.abc import AsyncIterator
from enum import Enum
from types import NoneType
from typing import Annotated, Literal

import pytest
from annotated_types import Doc, Interval, MaxLen, MinLen
from pydantic import BaseModel, Discriminator, Field, RootModel, StringConstraints

from trpc_rs_py import Router
from trpc_rs_py.router import Procedure, ProcedureKind
from trpc_rs_py.zodgen import _ZodGenerator, generate_zod


class Inner(BaseModel):
    """Nested model referenced twice to hit the registered-name cache."""

    x: int


class Cat(BaseModel):
    """Union member with a `kind` discriminator field."""

    kind: Literal["cat"]
    meows: bool


class Bird(BaseModel):
    """Union member whose discriminator has a different name."""

    species: Literal["bird"]


class Plain(BaseModel):
    """Union member with no Literal field at all."""

    a: int


class Digits(Enum):
    """Int-valued enum — members render as literal unions, not z.enum."""

    ONE = 1
    TWO = 2


class Empty(Enum):
    """Memberless enum — rejected."""


class Root(RootModel[int]):
    """RootModel subclass — rejected: bare scalar wire shape."""


class Defaults(BaseModel):
    """Model exercising every `_wrap_default` branch."""

    req: int
    maybe: str | None = None
    plain_none: str = None  # type: ignore[assignment] - unvalidated default
    factory: list[int] = Field(default_factory=list)
    nested: Inner = Inner(x=0)
    nested_again: Inner | None = None
    count: int = 5
    items: list[int] = [1, 2]
    blob: str = b"raw"  # type: ignore[assignment] - non-JSON default
    note: Annotated[str, StringConstraints(strip_whitespace=True)] = "x"


_BAD_LIST = types.GenericAlias(list, (int, str))
"""`list[int, str]` is impossible via `[]` syntax — built for the arity error."""

_BAD_DICT = types.GenericAlias(dict, (str,))
"""`dict[str]` likewise — one argument short of a mapping."""


def test_null_time_and_bare_containers() -> None:
    """`None`, `time`, `timedelta`, and bare `list`/`set`/`dict` annotations."""
    router = Router()

    @router.query
    def f(
        nothing: None,
        at: datetime.time,
        span: datetime.timedelta,
        xs: list,
        ys: set,
        m: dict,
    ) -> str:
        return ""

    out = generate_zod(router)
    assert "nothing: z.null()," in out
    assert "at: z.iso.time()," in out
    assert "span: z.iso.duration()," in out
    assert "xs: z.array(z.unknown())," in out
    assert "ys: z.array(z.unknown())," in out
    assert "m: z.record(z.string(), z.unknown())," in out


def test_lt_interval_and_length_constraints() -> None:
    """`lt`, `Interval` bounds, `MinLen`/`MaxLen`, and int-valued float bounds."""
    router = Router()

    @router.query
    def f(
        x: Annotated[float, Field(ge=2.0, lt=9.5)],
        y: Annotated[int, Interval(gt=1, ge=1, lt=9, le=9)],
        xs: Annotated[list[int], MinLen(2), MaxLen(4)],
    ) -> str:
        return ""

    out = generate_zod(router)
    assert "x: z.number().gte(2).lt(9.5)," in out
    assert "y: z.number().int().gt(1).gte(1).lt(9).lte(9)," in out
    assert "xs: z.array(z.number().int()).min(2).max(4)," in out


def test_pydantic_internal_metadata_skipped() -> None:
    """`Discriminator` metadata is Pydantic-internal — ignored, not dropped."""
    router = Router()

    @router.query
    def f(tagged: Annotated[str, Discriminator(lambda value: str(value))]) -> str:
        return ""

    assert "tagged: z.string()," in generate_zod(router)


def test_unknown_metadata_dropped() -> None:
    """Non-constraint metadata like `Doc` surfaces as a `// dropped:` note."""
    router = Router()

    @router.query
    def f(x: Annotated[int, Doc("docs that zod cannot express")]) -> str:
        return ""

    out = generate_zod(router)
    assert "// dropped: Doc" in out
    assert "x: z.number().int()," in out


def test_int_enum_and_memberless_enum() -> None:
    """Non-str enums become literal unions; memberless enums are rejected."""
    router = Router()

    @router.query
    def f(d: Digits) -> str:
        return ""

    assert "d: z.union([z.literal(1), z.literal(2)])," in generate_zod(router)

    bad = Router()

    @bad.query
    def g(e: Empty) -> str:
        return ""

    with pytest.raises(TypeError, match="no members"):
        generate_zod(bad)


def test_root_model_and_opaque_annotations_rejected() -> None:
    """RootModel subclasses and un-mappable annotations raise TypeError."""
    router = Router()

    @router.query
    def f(root: Root) -> str:
        return ""

    with pytest.raises(TypeError, match="RootModel"):
        generate_zod(router)

    bad = Router()

    @bad.query
    def g(x: complex) -> str:
        return ""

    with pytest.raises(TypeError, match="unsupported annotation"):
        generate_zod(bad)


def test_empty_literal_rejected() -> None:
    """`Literal[()]` carries no members — a refusal, not an empty union."""
    router = Router()

    @router.query
    def f(k: Literal[()]) -> str:
        return ""

    with pytest.raises(TypeError, match="empty Literal"):
        generate_zod(router)


def test_container_arity_rejections() -> None:
    """Generics with the wrong argument count raise TypeError."""
    router = Router()

    @router.query
    def f(xs: _BAD_LIST) -> str:  # type: ignore[valid-type]
        return ""

    with pytest.raises(TypeError, match="unsupported container annotation"):
        generate_zod(router)

    router = Router()

    @router.query
    def g(m: _BAD_DICT) -> str:  # type: ignore[valid-type]
        return ""

    with pytest.raises(TypeError, match="unsupported mapping annotation"):
        generate_zod(router)


def test_mapping_key_must_be_string() -> None:
    """`dict[int, ...]` cannot map to a JSON object — refused."""
    router = Router()

    @router.query
    def f(m: dict[int, str]) -> str:
        return ""

    with pytest.raises(TypeError, match="mapping keys must be strings"):
        generate_zod(router)


def test_variadic_tuple_maps_and_bad_tuples_rejected() -> None:
    """`tuple[T, ...]` → `z.array`; bare/ellipsized/mixed tuples refuse."""
    router = Router()

    @router.query
    def f(xs: tuple[int, ...]) -> str:
        return ""

    assert "xs: z.array(z.number().int())," in generate_zod(router)

    bare = Router()

    @bare.query
    def g(t: tuple[...]) -> str:  # type: ignore[valid-type]
        return ""

    with pytest.raises(TypeError, match="unsupported tuple annotation"):
        generate_zod(bare)

    mixed = Router()

    @mixed.query
    def h(t: tuple[int, str, ...]) -> str:
        return ""

    with pytest.raises(TypeError, match="unsupported tuple annotation"):
        generate_zod(mixed)


def test_union_of_only_none_rejected() -> None:
    """A union collapsing to nothing refuses inside `_map_union`."""
    generator = _ZodGenerator()
    with pytest.raises(TypeError, match="only None"):
        generator._map_union((NoneType, NoneType), "probe", [])  # pyright: ignore[reportPrivateUsage]


def test_model_field_defaults_and_drops() -> None:
    """Every `_wrap_default` path plus the second-reference model cache."""
    router = Router()

    @router.mutation
    def submit(payload: Defaults) -> str:
        return ""

    out = generate_zod(router)
    body = out.split("export const Defaults = z.object({")[1]
    assert "req: z.number().int()," in body
    assert "maybe: z.string().nullable().optional()," in body
    assert "plain_none: z.string().nullish()," in body
    assert "// dropped: default_factory" in body
    assert "factory: z.array(z.number().int()).optional()," in body
    assert "// dropped: model default value" in body
    assert "nested: Inner.optional()," in body
    # `Inner` was already registered via `nested` — cached name reused.
    assert "nested_again: Inner.nullable().optional()," in body
    assert "count: z.number().int().default(5)," in body
    assert "items: z.array(z.number().int()).default([1,2])," in body
    assert "// dropped: non-JSON default value" in body
    assert "blob: z.string().optional()," in body
    assert "// dropped: StringConstraints.strip_whitespace" in body
    assert 'note: z.string().default("x"),' in body


def test_model_with_untyped_field_rejected() -> None:
    """A model field without a resolved annotation refuses inside the body."""

    class Broken(BaseModel):
        x: int

    Broken.model_fields["x"].annotation = None
    router = Router()

    def handler(b: Broken) -> str:  # pyright: ignore[reportUnusedParameter]
        return ""

    # `from __future__ import annotations` stringified the hint; restore the
    # real class object so it resolves to the mutated model.
    handler.__annotations__["b"] = Broken
    router.mutation(handler)

    with pytest.raises(TypeError, match="untyped field"):
        generate_zod(router)


def test_union_without_shared_discriminator_falls_back() -> None:
    """Missing or disjoint Literal fields leave a plain `z.union`."""
    router = Router()

    @router.mutation
    def f(pet: Cat | Plain) -> str:
        return ""

    @router.mutation
    def g(pet: Cat | Bird) -> str:
        return ""

    out = generate_zod(router)
    assert "pet: z.union([Cat, Plain])," in out
    assert "pet: z.union([Cat, Bird])," in out


def test_uninspectable_handler_rejected() -> None:
    """A handler without an inspectable signature refuses with its path."""
    router = Router()
    router._procedures["opaque"] = Procedure(  # pyright: ignore[reportPrivateUsage]
        path="opaque", kind=ProcedureKind.QUERY, handler=iter
    )
    with pytest.raises(TypeError, match="opaque"):
        generate_zod(router)


def test_kwargs_and_args_only_handlers_rejected() -> None:
    """`**kwargs` and `*args`-only handlers have no object input shape."""
    router = Router()

    @router.query
    def kw(**fields: str) -> str:
        return ""

    with pytest.raises(TypeError, match="kwargs"):
        generate_zod(router)

    star = Router()

    @star.query
    def collect(*values: int) -> str:
        return ""

    with pytest.raises(TypeError, match="args-only"):
        generate_zod(star)


def test_named_params_with_star_args_skip_the_rest() -> None:
    """`def f(a, *rest)` keeps the named params; the varargs are skipped."""
    router = Router()

    @router.query
    def f(a: int, *rest: int) -> str:
        return ""

    out = generate_zod(router)
    assert "a: z.number().int()," in out


def test_subscription_last_event_id_is_protocol_metadata() -> None:
    """`lastEventId` is injected resume metadata — never a zod input."""
    router = Router()

    @router.subscription
    def idle(lastEventId: str | None = None) -> AsyncIterator[int]:
        async def stream() -> AsyncIterator[int]:
            yield 0

        return stream()

    @router.subscription
    def feed(topic: str, lastEventId: str | None = None) -> AsyncIterator[int]:
        async def stream() -> AsyncIterator[int]:
            yield 0

        return stream()

    out = generate_zod(router)
    assert "// procedure 'idle' takes no input" in out
    assert "topic: z.string()," in out
    assert "lastEventId" not in out.split("export const FeedInput")[1]
