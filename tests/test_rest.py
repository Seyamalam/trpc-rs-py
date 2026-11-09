"""REST endpoints and OpenAPI output tests.

Covers `RestRoute` metadata validation, `mount_rest` dispatch (path/query/body
parameter assembly, method matching, error shapes, production redaction, docs
UI gating), `generate_openapi` document structure, `merge_openapi` for FastAPI
apps, `attach(rest_path=...)`, and the `codegen openapi` CLI command.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest
from pydantic import BaseModel

from trpc_rs_py import (
    RestRoute,
    Router,
    TRPCError,
    TRPCErrorCode,
    attach,
    generate_openapi,
    mount_rest,
)
from trpc_rs_py.cli import main as cli_main


class Post(BaseModel):
    """Output model used across the REST tests."""

    id: int
    title: str


def _router() -> Router:
    """Router with query/mutation REST routes and a non-REST procedure."""
    router = Router()

    @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
    def by_id(post_id: int, verbose: bool = False) -> dict[str, Any]:
        return {"id": post_id, "verbose": verbose}

    @router.mutation(rest=RestRoute("POST", "/posts"))
    def create(title: str) -> Post:
        return Post(id=1, title=title)

    @router.mutation(rest=RestRoute("DELETE", "/posts/{post_id}"))
    def delete(post_id: int) -> dict[str, Any]:
        return {"deleted": post_id}

    @router.query
    def trpc_only() -> str:
        return "nope"

    return router


async def _call(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    query_string: bytes = b"",
    root_path: str = "",
) -> tuple[int, str, list[tuple[bytes, bytes]]]:
    """Invoke an ASGI app and return `(status, body_text, headers)`."""
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": body or b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        if messages:
            return messages.pop(0)
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": [],
        "root_path": root_path,
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], payload.decode(), start["headers"]


def _json(body: str) -> Any:
    return json.loads(body)


class TestRestRoute:
    """`RestRoute` metadata validation."""

    def test_method_normalized(self) -> None:
        assert RestRoute("get", "/x").method == "GET"

    def test_bad_method_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported REST method"):
            RestRoute("OPTIONS", "/x")

    def test_path_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            RestRoute("GET", "posts")

    def test_repeated_placeholder_rejected(self) -> None:
        with pytest.raises(ValueError, match="repeats"):
            RestRoute("GET", "/{a}/{a}")

    def test_malformed_placeholder_rejected(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            RestRoute("GET", "/posts/{}")

    def test_path_parameters_and_regex(self) -> None:
        route = RestRoute("GET", "/posts/{post_id}/files/{name}")
        assert route.path_parameters == ("post_id", "name")
        match = route.regex().match("/posts/3/files/a.txt")
        assert match is not None
        assert match.groupdict() == {"post_id": "3", "name": "a.txt"}
        assert route.regex().match("/posts/3") is None

    def test_unknown_path_param_rejected_at_registration(self) -> None:
        router = Router()
        with pytest.raises(ValueError, match="no such parameters"):

            @router.query(rest=RestRoute("GET", "/posts/{nope}"))
            def by_id(post_id: int) -> int:
                return post_id


class TestMountRest:
    """`mount_rest` dispatch behavior."""

    def test_path_param_coerced(self) -> None:
        status, body, _ = asyncio.run(_call(mount_rest(_router()), "GET", "/posts/7"))
        assert status == 200
        assert _json(body) == {"id": 7, "verbose": False}

    def test_query_param_coerced(self) -> None:
        status, body, _ = asyncio.run(
            _call(mount_rest(_router()), "GET", "/posts/7", query_string=b"verbose=true")
        )
        assert status == 200
        assert _json(body) == {"id": 7, "verbose": True}

    def test_post_json_body(self) -> None:
        status, body, headers = asyncio.run(
            _call(mount_rest(_router()), "POST", "/posts", body=b'{"title": "hi"}')
        )
        assert status == 200
        assert _json(body) == {"id": 1, "title": "hi"}
        assert (b"content-type", b"application/json") in headers

    def test_delete_route(self) -> None:
        status, body, _ = asyncio.run(_call(mount_rest(_router()), "DELETE", "/posts/9"))
        assert status == 200
        assert _json(body) == {"deleted": 9}

    def test_unknown_path_404(self) -> None:
        status, body, _ = asyncio.run(_call(mount_rest(_router()), "GET", "/nope"))
        assert status == 404
        assert _json(body)["error"]["code"] == "NOT_FOUND"

    def test_wrong_method_405(self) -> None:
        status, body, _ = asyncio.run(_call(mount_rest(_router()), "PATCH", "/posts/7"))
        assert status == 405
        assert _json(body)["error"]["code"] == "METHOD_NOT_SUPPORTED"

    def test_bad_input_400(self) -> None:
        status, body, _ = asyncio.run(
            _call(mount_rest(_router()), "POST", "/posts", body=b'{"wrong": 1}')
        )
        assert status == 400
        assert _json(body)["error"]["code"] == "BAD_REQUEST"

    def test_invalid_json_body_400(self) -> None:
        status, _, _ = asyncio.run(_call(mount_rest(_router()), "POST", "/posts", body=b"not json"))
        assert status == 400

    def test_trpc_error_status_mapping(self) -> None:
        router = Router()

        @router.query(rest=RestRoute("GET", "/missing"))
        def missing() -> str:
            raise TRPCError("nothing here", code=TRPCErrorCode.NOT_FOUND)

        status, body, _ = asyncio.run(_call(mount_rest(router), "GET", "/missing"))
        assert status == 404
        assert _json(body) == {"error": {"code": "NOT_FOUND", "message": "nothing here"}}

    def test_production_redacts_unexpected_errors(self) -> None:
        router = Router()

        @router.query(rest=RestRoute("GET", "/boom"))
        def boom() -> str:
            raise RuntimeError("secret internals")

        dev_status, dev_body, _ = asyncio.run(_call(mount_rest(router), "GET", "/boom"))
        prod_status, prod_body, _ = asyncio.run(
            _call(mount_rest(router, production=True), "GET", "/boom")
        )
        assert dev_status == prod_status == 500
        assert "secret internals" in dev_body
        assert _json(prod_body)["error"]["message"] == "Internal server error"

    def test_duplicate_route_rejected(self) -> None:
        router = Router()

        @router.query(rest=RestRoute("GET", "/dup"))
        def first() -> int:
            return 1

        @router.query(rest=RestRoute("GET", "/dup"))
        def second() -> int:
            return 2

        with pytest.raises(ValueError, match="Duplicate REST route"):
            mount_rest(router)

    def test_non_rest_procedures_not_served(self) -> None:
        status, _, _ = asyncio.run(_call(mount_rest(_router()), "GET", "/trpc_only"))
        assert status == 404

    def test_root_path_stripped(self) -> None:
        status, _, _ = asyncio.run(
            _call(mount_rest(_router()), "GET", "/api/posts/4", root_path="/api")
        )
        assert status == 200

    def test_openapi_json_served(self) -> None:
        status, body, _ = asyncio.run(
            _call(mount_rest(_router(), title="Blog", version="2.0.0"), "GET", "/openapi.json")
        )
        assert status == 200
        doc = _json(body)
        assert doc["openapi"] == "3.1.0"
        assert doc["info"] == {"title": "Blog", "version": "2.0.0"}

    def test_docs_page_served_in_dev(self) -> None:
        status, body, headers = asyncio.run(_call(mount_rest(_router()), "GET", "/docs"))
        assert status == 200
        assert b"text/html" in dict(headers)[b"content-type"]
        assert "openapi.json" in body

    def test_docs_off_in_production_by_default(self) -> None:
        app = mount_rest(_router(), production=True)
        docs_status, _, _ = asyncio.run(_call(app, "GET", "/docs"))
        spec_status, _, _ = asyncio.run(_call(app, "GET", "/openapi.json"))
        assert docs_status == 404
        assert spec_status == 404

    def test_docs_explicitly_enabled_in_production(self) -> None:
        app = mount_rest(_router(), production=True, docs=True)
        docs_status, _, _ = asyncio.run(_call(app, "GET", "/docs"))
        spec_status, _, _ = asyncio.run(_call(app, "GET", "/openapi.json"))
        assert docs_status == 200
        assert spec_status == 200


class TestGenerateOpenapi:
    """`generate_openapi` document structure."""

    def test_document_validates_against_openapi_spec(self) -> None:
        openapi_spec_validator = pytest.importorskip("openapi_spec_validator")
        doc = generate_openapi(_router(), title="Blog", version="1.0.0")
        openapi_spec_validator.validate(doc)

    def test_paths_and_parameters(self) -> None:
        doc = generate_openapi(_router())
        get = doc["paths"]["/posts/{post_id}"]["get"]
        assert get["operationId"] == "by_id"
        params = {p["name"]: p for p in get["parameters"]}
        assert params["post_id"]["in"] == "path"
        assert params["post_id"]["required"] is True
        assert params["post_id"]["schema"] == {"type": "integer"}
        assert params["verbose"]["in"] == "query"
        assert params["verbose"]["required"] is False

    def test_request_body_and_components(self) -> None:
        doc = generate_openapi(_router())
        post = doc["paths"]["/posts"]["post"]
        schema = post["requestBody"]["content"]["application/json"]["schema"]
        assert schema["type"] == "object"
        assert schema["required"] == ["title"]
        # The `Post` output model is hoisted into components.
        response_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": "#/components/schemas/Post"}
        assert doc["components"]["schemas"]["Post"]["properties"]["title"]["type"] == "string"

    def test_error_response_shape(self) -> None:
        doc = generate_openapi(_router())
        default = doc["paths"]["/posts"]["post"]["responses"]["default"]
        schema = default["content"]["application/json"]["schema"]
        assert schema["required"] == ["error"]

    def test_non_rest_procedures_absent(self) -> None:
        doc = generate_openapi(_router())
        assert "/trpc_only" not in doc["paths"]

    def test_empty_router(self) -> None:
        doc = generate_openapi(Router())
        assert doc["paths"] == {}
        assert "components" not in doc


class TestMergeOpenapi:
    """`merge_openapi` FastAPI integration."""

    def test_paths_merged_into_fastapi_document(self) -> None:
        pytest.importorskip("fastapi")
        from fastapi import FastAPI

        from trpc_rs_py import merge_openapi

        app = FastAPI(title="Host", version="9.9.9")

        @app.get("/native")
        def native() -> dict[str, bool]:
            return {"ok": True}

        merge_openapi(app, _router())
        doc = app.openapi()
        assert "/native" in doc["paths"]
        assert "/posts/{post_id}" in doc["paths"]
        assert doc["info"]["title"] == "Host"


class TestAttachRestPath:
    """`attach(..., rest_path=...)` serves REST + tRPC over one wrapper."""

    async def _sink(self, scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"native":true}'})

    def test_rest_and_trpc_both_served(self) -> None:
        app = attach(self._sink, _router(), path="/trpc", rest_path="/api")
        rest_status, rest_body, _ = asyncio.run(_call(app, "GET", "/api/posts/5"))
        assert rest_status == 200
        assert _json(rest_body) == {"id": 5, "verbose": False}
        spec_status, _, _ = asyncio.run(_call(app, "GET", "/api/openapi.json"))
        assert spec_status == 200

    def test_trpc_path_still_works(self) -> None:
        from urllib.parse import quote

        app = attach(self._sink, _router(), path="/trpc", rest_path="/api")
        query = f"input={quote(json.dumps({'post_id': 7}))}".encode()
        status, body, _ = asyncio.run(_call(app, "GET", "/trpc/by_id", query_string=query))
        assert status == 200
        assert _json(body)["result"]["data"] == {"id": 7, "verbose": False}

    def test_non_rest_path_passes_through(self) -> None:
        app = attach(self._sink, _router(), path="/trpc", rest_path="/api")
        status, body, _ = asyncio.run(_call(app, "GET", "/other"))
        assert status == 200
        assert _json(body) == {"native": True}


class TestCodegenOpenapiCli:
    """`trpc-py codegen openapi` command."""

    def test_writes_document(self, tmp_path: Any, monkeypatch: Any) -> None:
        module_path = tmp_path / "rest_router_mod.py"
        module_path.write_text(
            "from trpc_rs_py import Router, RestRoute\n"
            "router = Router()\n"
            "@router.query(rest=RestRoute('GET', '/ping'))\n"
            "def ping() -> bool:\n"
            "    return True\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        sys.modules.pop("rest_router_mod", None)
        out = tmp_path / "openapi.json"
        code = cli_main(
            [
                "codegen",
                "openapi",
                "--app",
                "rest_router_mod:router",
                "--out",
                str(out),
                "--title",
                "Ping API",
            ]
        )
        assert code == 0
        doc = json.loads(out.read_text())
        assert doc["info"]["title"] == "Ping API"
        assert "/ping" in doc["paths"]
