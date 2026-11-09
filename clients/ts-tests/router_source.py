"""The representative Python Router behind the committed TS fixture.

`generate-fixture.py` regenerates `src/app-router.d.ts` from the router built
here, and `tests/test_codegen.py` asserts the committed fixture still matches
`generate_types(build_router())` byte for byte, so the fixture can never
drift from the generator. The router exercises every supported annotation
family: primitives, defaults, unions, literals, enums, lists, dicts, tuples,
nested and recursive Pydantic models, dotted/nested paths, and path parts
that need TypeScript key quoting.

Example:
    ```python
    from clients.ts-tests.router_source import build_router

    assert len(build_router().procedures) > 0
    ```
"""

from __future__ import annotations

import enum
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, Tracked, tracked


class Color(enum.Enum):
    """Tag colors, serialized as their string values."""

    RED = "red"
    BLUE = "blue"


class Tag(BaseModel):
    """A post tag with an optional weight."""

    label: str
    weight: float = 1.0


class Post(BaseModel):
    """A post with nested tags and optional metadata."""

    title: str
    tags: list[Tag]
    color: Color
    note: Annotated[str, Field(max_length=40)] | None = None
    meta: dict[str, int] = {}


class Author(BaseModel):
    """An author whose posts are nested models."""

    name: str
    posts: list[Post] = []


class Node(BaseModel):
    """A recursive tree node."""

    value: int
    children: list[Node] = []
    parent: Node | None = None


# `class` is a Python keyword, so the model is created dynamically; its name
# is a TypeScript reserved word, exercising the generator's keyword
# sanitization (`export interface _class`).
KeywordModel: type[BaseModel] = type(
    "class",
    (BaseModel,),
    {"__annotations__": {"value": int}, "__doc__": "A model named after a TS reserved word."},
)

# `never` and `undefined` are type keyword/global names tsc refuses as
# interface names (TS2427); they are created dynamically like `class` and must
# sanitize to `_never` / `_undefined`.
NeverModel: type[BaseModel] = type(
    "never",
    (BaseModel,),
    {"__annotations__": {"value": int}, "__doc__": "A model named after the TS `never` type."},
)
UndefinedModel: type[BaseModel] = type(
    "undefined",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model named after the TS `undefined` global.",
    },
)
InferRouterInputsModel: type[BaseModel] = type(
    "inferRouterInputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the generated inference helper.",
    },
)
InferRouterOutputsModel: type[BaseModel] = type(
    "inferRouterOutputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the generated inference helper.",
    },
)
InferRouterInputsModel: type[BaseModel] = type(
    "inferRouterInputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the imported inferRouterInputs helper.",
    },
)
InferRouterOutputsModel: type[BaseModel] = type(
    "inferRouterOutputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the imported inferRouterOutputs helper.",
    },
)
InferRouterInputsModel: type[BaseModel] = type(
    "inferRouterInputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the imported inference helper.",
    },
)
InferRouterOutputsModel: type[BaseModel] = type(
    "inferRouterOutputs",
    (BaseModel,),
    {
        "__annotations__": {"value": int},
        "__doc__": "A model that must not shadow the imported inference helper.",
    },
)


class Array(BaseModel):
    """A model named after the TypeScript `Array` built-in generic.

    The generated module itself uses `Array<...>` annotations, so this name
    must sanitize to `_Array` or every emitted generic would resolve to this
    interface instead of the built-in.
    """

    items: list[int]


class Record(BaseModel):
    """A model named after the TypeScript `Record` utility type.

    Same reason as `Array`: the generated `Record<string, ...>` annotations
    must keep referring to the built-in.
    """

    entries: dict[str, str]


class infer(BaseModel):
    """A model named after the type-context keyword `infer`."""

    value: int


class keyof(BaseModel):
    """A model named after the type-context keyword `keyof`."""

    value: str


class readonly(BaseModel):
    """A model named after the type-context keyword `readonly`."""

    value: int


class unique(BaseModel):
    """A model named after the type-context keyword `unique`."""

    value: bool


class WireAlias(BaseModel):
    """A model whose single wire name comes from `serialize_by_alias`.

    Input validation accepts only `wire_value` (the alias, no
    `populate_by_name`) and `serialize_by_alias=True` makes serialization emit
    the same key, so the generated property is `wire_value` in both
    directions.
    """

    model_config = ConfigDict(serialize_by_alias=True)

    value: str = Field(alias="wire_value")


class PostRef(BaseModel):
    """Typed payload of `PostNotFound`: which post id was missing."""

    post_id: int


class RateLimitInfo(BaseModel):
    """Typed payload of `RateLimited`: seconds until retry is allowed."""

    retry_after: float


class PostNotFound(TRPCError):
    """Declared typed error: the post does not exist."""

    code = TRPCErrorCode.NOT_FOUND
    data_model = PostRef


class RateLimited(TRPCError):
    """Declared typed error: the caller was rate limited."""

    code = TRPCErrorCode.TOO_MANY_REQUESTS
    data_model = RateLimitInfo


def build_router() -> Router:
    """Build the representative router for the TypeScript fixture.

    Returns:
        A `Router` covering every supported annotation family.

    Example:
        ```python
        from clients.ts-tests.router_source import build_router

        assert "posts.by_id" in build_router().procedures
        ```
    """
    router = Router()

    @router.query
    def health() -> bool:
        """No-input query."""
        return True

    @router.query
    def post_by_id(post_id: int) -> Post:
        """Single-annotated-parameter query returning a nested model."""
        raise NotImplementedError

    @router.query(errors=[PostNotFound, RateLimited])
    def post_by_id_or_fail(post_id: int) -> Post:
        """Query with declared typed errors — `error.data.code` narrows to a
        discriminated union carrying `PostRef`/`RateLimitInfo` payloads."""
        raise PostNotFound("missing", data=PostRef(post_id=post_id))

    @router.mutation
    def create_post(post: Post, author: Author, source: Literal["web", "app"] = "web") -> Post:
        """Multi-parameter mutation with literal and model inputs."""
        raise NotImplementedError

    @router.query(name="search-items")
    def search(q: str, limit: int = 10) -> list[str]:
        """Query whose path needs TypeScript key quoting."""
        raise NotImplementedError

    @router.mutation
    def no_input() -> str:
        """Mutation without input."""
        raise NotImplementedError

    @router.query
    def echo_all(payload: int | str | bool | None) -> tuple[int, str] | None:
        """Union input and tuple-or-null output."""
        raise NotImplementedError

    @router.query
    def node_tree(root: Node) -> Node:
        """Recursive model input and output."""
        raise NotImplementedError

    @router.query
    def keyword_ping(item: KeywordModel) -> KeywordModel:  # type: ignore[valid-type]
        """Round-trip a model whose name is a TypeScript reserved word."""
        raise NotImplementedError

    @router.query
    def keyword_round_trip(item: NeverModel) -> UndefinedModel:  # type: ignore[valid-type]
        """Round-trip models whose names tsc rejects as interface names."""
        raise NotImplementedError

    @router.query
    def helper_names(
        item: InferRouterInputsModel,
    ) -> InferRouterOutputsModel:
        """Avoid shadowing imported router input/output helper names."""
        raise NotImplementedError

    @router.mutation
    def wire_alias(item: WireAlias) -> WireAlias:
        """Round-trip a model whose wire name comes from `serialize_by_alias`."""
        raise NotImplementedError

    @router.query
    def builtin_shadow(item: Array) -> Record:
        """Round-trip models named after TS built-in generic type names."""
        raise NotImplementedError

    @router.query
    def type_keyword(first: infer, second: keyof, third: readonly) -> unique:
        """Round-trip models named after TypeScript type-context keywords."""
        raise NotImplementedError

    @router.subscription
    async def ticks() -> AsyncIterator[int]:
        """No-input subscription yielding untracked items."""
        yield 0
        raise NotImplementedError

    @router.subscription
    async def chat(room: str, lastEventId: str | None = None) -> AsyncIterator[Tracked[Post]]:
        """Tracked subscription: `lastEventId` is protocol resume metadata,
        never part of the emitted input type; `Tracked[Post]` items reach the
        client as `{id, data}` pairs."""
        yield tracked("0", Post(title="t", tags=[], color=Color.RED))
        raise NotImplementedError

    nested = Router()

    @nested.query
    def by_id(node_id: int) -> Node:
        """Procedure under a nested namespace."""
        raise NotImplementedError

    router.nest("tree", nested)
    return router
