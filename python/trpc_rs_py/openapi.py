"""OpenAPI 3.1 document generation for `RestRoute`-annotated procedures.

Procedures opt in with ``rest=RestRoute(method, path)`` on the `query` and
`mutation` decorators; `generate_openapi` turns those procedures into an
OpenAPI 3.1 document using the same Pydantic v2 annotations that drive input
and output validation, so the document and the runtime never disagree.

Example:
    ```python
    from trpc_rs_py import RestRoute, Router, generate_openapi

    router = Router()

    @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
    def by_id(post_id: int) -> str:
        return str(post_id)

    document = generate_openapi(router, title="Blog API", version="1.0.0")
    assert "/posts/{post_id}" in document["paths"]
    ```
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict, TypeAdapter, create_model

from .router import type_hints_of

if TYPE_CHECKING:
    from .router import Procedure, Router

__all__ = ["generate_openapi", "merge_openapi"]

_DEFAULT_TITLE = "trpc-rs-py API"

# Error shape served by `mount_rest` on failure; kept in sync with
# `_error_object` in `trpc_rs_py.rest`.
_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["code", "message"],
        }
    },
    "required": ["error"],
}

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def generate_openapi(
    router: Router,
    *,
    title: str = _DEFAULT_TITLE,
    version: str = "0.0.0",
    description: str | None = None,
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document from `router`'s `RestRoute` procedures.

    Path placeholders (`/posts/{post_id}`) become `path` parameters bound to
    same-named handler parameters. Remaining handler parameters become
    `query` parameters for GET and DELETE routes, or a JSON `requestBody`
    object for POST, PUT and PATCH routes. Schemas come from
    `pydantic.TypeAdapter(...).json_schema()`; nested model definitions are
    hoisted into `components.schemas`. Procedures without `rest` metadata are
    absent from the document.

    Args:
        router: Router to inspect.
        title: `info.title` for the document.
        version: `info.version` for the document.
        description: Optional `info.description`.

    Returns:
        The OpenAPI document as a JSON-ready `dict`.

    Example:
        ```python
        from trpc_rs_py import RestRoute, Router, generate_openapi

        router = Router()

        @router.mutation(rest=RestRoute("POST", "/posts"))
        def create(title: str) -> str:
            return title

        doc = generate_openapi(router, title="Blog", version="1.0.0")
        assert doc["paths"]["/posts"]["post"]["requestBody"]
        ```
    """
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    for procedure in router.procedures.values():
        if procedure.rest is None:
            continue
        operation = _operation(procedure, components)
        paths.setdefault(procedure.rest.path, {})[procedure.rest.method.lower()] = operation
    info: dict[str, Any] = {"title": title, "version": version}
    if description is not None:
        info["description"] = description
    document: dict[str, Any] = {"openapi": "3.1.0", "info": info, "paths": paths}
    if components:
        document["components"] = {"schemas": components}
    return document


def merge_openapi(
    app: Any,
    router: Router,
    *,
    title: str = _DEFAULT_TITLE,
    version: str = "0.0.0",
    description: str | None = None,
) -> None:
    """Merge generated `RestRoute` paths into a FastAPI app's `/openapi.json`.

    Wraps the app's ``app.openapi`` attribute — FastAPI's documented
    customization point — so `RestRoute` procedures appear in the host app's
    own OpenAPI document and Swagger UI alongside its native routes. The
    standalone document served by `mount_rest` remains authoritative; this is
    for FastAPI apps that explicitly want one merged surface. Requires the
    host app to expose `app.openapi` and `app.openapi_schema` (FastAPI does).

    Args:
        app: A FastAPI application instance.
        router: Router whose `RestRoute` procedures are merged in.
        title: Document title used only if the host document lacks one.
        version: API version for the generated paths' document section.
        description: Optional description; unused when the host has one.

    Returns:
        `None`. Mutates `app.openapi` in place.

    Example:
        ```python
        from fastapi import FastAPI

        from trpc_rs_py import RestRoute, Router, merge_openapi

        router = Router()

        @router.query(rest=RestRoute("GET", "/health"))
        def health() -> bool:
            return True

        app = FastAPI()
        merge_openapi(app, router)
        # GET /openapi.json now includes /health.
        ```
    """
    generated = generate_openapi(router, title=title, version=version, description=description)
    original = app.openapi

    def merged() -> dict[str, Any]:
        """Return the host app's document with generated paths merged once."""
        if app.openapi_schema is None:
            document: dict[str, Any] = original()
            document.setdefault("paths", {}).update(generated["paths"])
            schemas = document.setdefault("components", {}).setdefault("schemas", {})
            schemas.update(generated.get("components", {}).get("schemas", {}))
            app.openapi_schema = document
        return cast(dict[str, Any], app.openapi_schema)

    app.openapi = merged


def _operation(procedure: Procedure, components: dict[str, Any]) -> dict[str, Any]:
    """Build one OpenAPI operation object for a `RestRoute` procedure.

    Args:
        procedure: Procedure carrying `rest` metadata.
        components: Shared `components.schemas` mapping; `$defs` produced by
            Pydantic are hoisted here.

    Returns:
        The OpenAPI operation object for the procedure.
    """
    assert procedure.rest is not None  # guaranteed by the caller
    rest = procedure.rest
    handler = procedure.handler
    hints = type_hints_of(handler)
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        signature = None
    path_names = set(rest.path_parameters)
    parameters: list[dict[str, Any]] = []
    body_fields: dict[str, tuple[Any, Any]] = {}
    if signature is not None:
        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                continue
            annotation = hints.get(parameter.name, parameter.annotation)
            required = parameter.default is inspect.Parameter.empty
            schema = _schema_for(annotation, components)
            if parameter.name in path_names:
                parameters.append(
                    {"name": parameter.name, "in": "path", "required": True, "schema": schema}
                )
            elif rest.method in _BODY_METHODS:
                body_fields[parameter.name] = (
                    annotation,
                    ... if required else parameter.default,
                )
            else:
                parameters.append(
                    {
                        "name": parameter.name,
                        "in": "query",
                        "required": required,
                        "schema": schema,
                    }
                )
    operation: dict[str, Any] = {"operationId": procedure.path}
    if parameters:
        operation["parameters"] = parameters
    if body_fields:
        model = create_model(
            f"{getattr(handler, '__name__', 'Procedure')}Input",
            __config__=ConfigDict(populate_by_name=True),
            **cast(Any, body_fields),
        )
        body_schema = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
        _hoist_defs(body_schema, components)
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": body_schema}},
        }
    output_schema = _schema_for(procedure.output_annotation, components)
    success: dict[str, Any] = {"description": "Successful response"}
    if output_schema:
        success["content"] = {"application/json": {"schema": output_schema}}
    operation["responses"] = {
        "200": success,
        "default": {
            "description": "Error response",
            "content": {"application/json": {"schema": _ERROR_SCHEMA}},
        },
    }
    return operation


def _schema_for(annotation: Any, components: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON schema for `annotation`, hoisting nested `$defs`.

    Args:
        annotation: A type-hint object, or `inspect.Parameter.empty`/`None`
            for unannotated positions.
        components: Shared `components.schemas` mapping; `$defs` produced by
            Pydantic are hoisted here.

    Returns:
        A JSON schema dict; `{}` (any value) when unannotated.
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return {}
    try:
        # Wrap the annotation in a synthetic model so a `BaseModel` annotation
        # becomes a `$ref` hoisted into `components` instead of an inline copy.
        wrapper = create_model("OpenapiSchema", value=(annotation, ...))
        schema = TypeAdapter(wrapper).json_schema(ref_template="#/components/schemas/{model}")
        inner = schema["properties"]["value"]
        # The synthetic field name leaks a meaningless title; drop it.
        inner.pop("title", None)
    except Exception:
        return {}
    _hoist_defs(schema, components)
    return cast(dict[str, Any], inner)


def _hoist_defs(schema: dict[str, Any], components: dict[str, Any]) -> None:
    """Move a Pydantic schema's `$defs` into the shared components mapping.

    Args:
        schema: Schema dict mutated in place (`$defs` removed).
        components: Destination `components.schemas` mapping.
    """
    defs = schema.pop("$defs", None)
    if defs:
        for name, definition in cast(dict[str, Any], defs).items():
            if isinstance(definition, dict):
                _hoist_defs(cast(dict[str, Any], definition), components)
            components.setdefault(name, definition)
