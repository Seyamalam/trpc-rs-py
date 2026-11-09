# Typed errors: `TRPCError` subclasses with declared data models — the
# `errors=[...]` declaration API, wire-envelope propagation (JSON + SSE +
# REST), reserved-key enforcement, and the discriminated-union codegen.

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.testclient import TestClient

from trpc_rs_py import (
    RestRoute,
    Router,
    TRPCError,
    TRPCErrorCode,
    mount,
    mount_rest,
)
from trpc_rs_py._core import build_response
from trpc_rs_py.asgi import _envelope_data, _run_call
from trpc_rs_py.codegen import generate_types
from trpc_rs_py.errors import error_data_payload


class PostRef(BaseModel):
    post_id: int


class RateLimitInfo(BaseModel):
    retry_after: float


class PostNotFound(TRPCError):
    code = TRPCErrorCode.NOT_FOUND
    data_model = PostRef


class RateLimited(TRPCError):
    code = TRPCErrorCode.TOO_MANY_REQUESTS
    data_model = RateLimitInfo


class ForbiddenWord(TRPCError):
    code = TRPCErrorCode.FORBIDDEN


class SpoofedData(TRPCError):
    code = TRPCErrorCode.NOT_FOUND
    data_model = None


# --- TRPCError subclass API -------------------------------------------------


def test_subclass_code_falls_back_to_class_attribute() -> None:
    error = PostNotFound("missing", data=PostRef(post_id=3))
    assert error.code is TRPCErrorCode.NOT_FOUND
    assert error.data_code == -32004
    assert error.data_model is PostRef


def test_constructor_code_kwarg_wins_over_class_attribute() -> None:
    error = PostNotFound("missing", code=TRPCErrorCode.CONFLICT, data=None)
    assert error.code is TRPCErrorCode.CONFLICT
    assert error.data_code == -32009


def test_base_class_defaults_to_internal_server_error() -> None:
    error = TRPCError("boom")
    assert error.code is TRPCErrorCode.INTERNAL_SERVER_ERROR
    assert error.data_model is None


def test_class_without_data_model_serializes_no_payload() -> None:
    error = ForbiddenWord("nope")
    assert error_data_payload(error) is None


# --- error_data_payload ------------------------------------------------------


def test_payload_extracts_pydantic_model_as_json_dict() -> None:
    error = PostNotFound("x", data=PostRef(post_id=7))
    assert error_data_payload(error) == {"post_id": 7}


def test_payload_passes_plain_dicts_and_other_values_through() -> None:
    assert error_data_payload(TRPCError("x", data={"a": 1})) == {"a": 1}
    assert error_data_payload(TRPCError("x", data="raw")) == "raw"
    assert error_data_payload(TRPCError("x")) is None


def test_payload_dumps_model_in_json_mode() -> None:
    class HasDate(BaseModel):
        when: str

    error = TRPCError("x", data=HasDate(when="now"))
    assert error_data_payload(error) == {"when": "now"}


# --- errors=[...] registration ----------------------------------------------


def test_errors_declaration_stores_classes_on_the_procedure() -> None:
    router = Router()

    @router.query(errors=[PostNotFound, RateLimited])
    def post() -> str:
        raise PostNotFound("x", data=PostRef(post_id=1))

    assert router.procedures["post"].errors == (PostNotFound, RateLimited)


def test_errors_declaration_defaults_to_empty() -> None:
    router = Router()

    @router.query
    def ok() -> str:
        return "ok"

    assert router.procedures["ok"].errors == ()


def test_errors_declaration_rejects_non_trpc_error_entries() -> None:
    router = Router()

    with pytest.raises(TypeError, match="TRPCError subclasses"):

        @router.query(errors=[ValueError])
        def proc() -> str:
            return ""


def test_errors_declaration_rejects_non_trpc_error_code() -> None:
    class BadCode(TRPCError):
        code = "NOT_FOUND"  # type: ignore[assignment]

    router = Router()
    with pytest.raises(TypeError, match="must be a TRPCErrorCode"):

        @router.query(errors=[BadCode])
        def proc() -> str:
            return ""


def test_errors_declaration_rejects_non_model_data_model() -> None:
    class BadModel(TRPCError):
        code = TRPCErrorCode.NOT_FOUND
        data_model = int  # type: ignore[assignment]

    router = Router()
    with pytest.raises(TypeError, match="BaseModel subclass or None"):

        @router.query(errors=[BadModel])
        def proc() -> str:
            return ""


def test_errors_declaration_works_on_mutations_and_subscriptions() -> None:
    router = Router()

    @router.mutation(errors=[PostNotFound])
    def mutate() -> str:
        raise PostNotFound("x", data=PostRef(post_id=1))

    @router.subscription(errors=[PostNotFound])
    async def sub() -> AsyncIterator[int]:
        yield 1
        raise PostNotFound("x", data=PostRef(post_id=2))

    assert router.procedures["mutate"].errors == (PostNotFound,)
    assert router.procedures["sub"].errors == (PostNotFound,)


# --- _envelope_data merge ----------------------------------------------------


def test_envelope_data_merges_path_and_dict_payload() -> None:
    assert _envelope_data({"post_id": 5}, "posts.byId") == {
        "path": "posts.byId",
        "post_id": 5,
    }


def test_envelope_data_drops_reserved_keys_from_user_payloads() -> None:
    payload = {"code": "SPOOFED", "httpStatus": 200, "path": "fake", "stack": "x", "ok": 1}
    assert _envelope_data(payload, "posts.byId") == {"path": "posts.byId", "ok": 1}


def test_envelope_data_wraps_non_dict_payloads_under_data() -> None:
    assert _envelope_data("raw", "p") == {"path": "p", "data": "raw"}


def test_envelope_data_returns_none_when_empty() -> None:
    assert _envelope_data(None, None) is None


def test_envelope_data_path_only() -> None:
    assert _envelope_data(None, "a.b") == {"path": "a.b"}


# --- Wire envelope propagation ----------------------------------------------


def make_error_router() -> Router:
    router = Router()

    @router.query(errors=[PostNotFound])
    def post_or_fail(post_id: int) -> str:
        raise PostNotFound("no such post", data=PostRef(post_id=post_id))

    @router.query(errors=[SpoofedData])
    def spoof() -> str:
        raise SpoofedData(
            "x", data={"code": "SPOOFED", "httpStatus": 200, "path": "fake", "extra": 1}
        )

    @router.query
    def internal_with_path() -> str:
        raise RuntimeError("secret")

    return router


def test_declared_error_envelope_carries_typed_fields_and_path() -> None:
    procedure = make_error_router().procedures["post_or_fail"]
    result = asyncio.run(_run_call(procedure, {"post_id": 9}, production=False))
    _, body = build_response([result], False)
    data = json.loads(body)["error"]["data"]
    assert data["code"] == "NOT_FOUND"
    assert data["httpStatus"] == 404
    assert data["path"] == "post_or_fail"
    assert data["post_id"] == 9


def test_reserved_keys_cannot_be_overridden() -> None:
    procedure = make_error_router().procedures["spoof"]
    result = asyncio.run(_run_call(procedure, None, production=False))
    _, body = build_response([result], False)
    data = json.loads(body)["error"]["data"]
    assert data["code"] == "NOT_FOUND"
    assert data["httpStatus"] == 404
    assert data["path"] == "spoof"
    assert data["extra"] == 1
    assert "stack" not in data


def test_unexpected_errors_carry_path_but_no_payload() -> None:
    procedure = make_error_router().procedures["internal_with_path"]
    result = asyncio.run(_run_call(procedure, None, production=True))
    _, body = build_response([result], False)
    error = json.loads(body)["error"]
    assert error["message"] == "Internal server error"
    assert error["data"]["path"] == "internal_with_path"


def test_path_miss_envelope_carries_attempted_path() -> None:
    app = FastAPI()
    app.mount("/trpc", mount(make_error_router()))
    client = TestClient(app)
    response = client.get("/trpc/missing_proc")
    assert response.status_code == 404
    assert response.json()["error"]["data"]["path"] == "missing_proc"


def test_full_request_round_trip_typed_error() -> None:
    app = FastAPI()
    app.mount("/trpc", mount(make_error_router()))
    client = TestClient(app)
    response = client.get("/trpc/post_or_fail?input=%7B%22post_id%22%3A4%7D")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["data"]["code"] == "NOT_FOUND"
    assert error["data"]["post_id"] == 4
    assert error["data"]["path"] == "post_or_fail"


def test_batched_error_envelopes_carry_their_own_paths() -> None:
    app = FastAPI()
    app.mount("/trpc", mount(make_error_router()))
    client = TestClient(app)
    response = client.get(
        "/trpc/post_or_fail,missing_proc?batch=1&input=%7B%220%22%3A%7B%22post_id%22%3A1%7D%7D"
    )
    errors = [item["error"] for item in response.json()]
    assert errors[0]["data"]["path"] == "post_or_fail"
    assert errors[0]["data"]["post_id"] == 1
    assert errors[1]["data"]["path"] == "missing_proc"


# --- SSE serialized-error propagation ----------------------------------------


def _scope(method: str, path: str, *, query: bytes = b"") -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": path,
        "query_string": query,
        "headers": [],
    }


async def _collect(app: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    await app(scope, receive, send)
    return sent


def test_sse_serialized_error_carries_typed_fields_and_path() -> None:
    router = Router()

    @router.subscription(errors=[PostNotFound])
    async def live() -> AsyncIterator[int]:
        yield 0
        raise PostNotFound("gone", data=PostRef(post_id=3))

    app = mount(router)
    sent = asyncio.run(_collect(app, _scope("GET", "/live")))
    bodies = [
        message["body"]
        for message in sent
        if message["type"] == "http.response.body" and message["body"]
    ]
    error_frame = next(b for b in bodies if b"serialized-error" in b)
    # The serialized-error `data:` payload is the flat official error shape.
    payload = json.loads(error_frame.split(b"data: ", 1)[1])
    data = payload["data"]
    assert data["code"] == "NOT_FOUND"
    assert data["httpStatus"] == 404
    assert data["path"] == "live"
    assert data["post_id"] == 3


# --- REST propagation --------------------------------------------------------


def test_rest_error_data_carries_typed_payload() -> None:
    router = Router()

    @router.query(errors=[PostNotFound], rest=RestRoute("GET", "/posts/{post_id}"))
    def rest_post(post_id: int) -> str:
        raise PostNotFound("no such post", data=PostRef(post_id=post_id))

    app = FastAPI()
    app.mount("/api", mount_rest(router))
    client = TestClient(app)
    response = client.get("/api/posts/9")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "NOT_FOUND"
    assert body["data"] == {"post_id": 9}


# --- Codegen -----------------------------------------------------------------


def test_codegen_emits_error_shape_union_and_reserved_names() -> None:
    router = Router()

    @router.query(errors=[PostNotFound, RateLimited])
    def post_or_fail(post_id: int) -> str:
        raise PostNotFound("x", data=PostRef(post_id=1))

    @router.query
    def plain() -> bool:
        return True

    source = generate_types(router)
    assert "export type AppRouterErrorShape" in source
    assert "errorShape: AppRouterErrorShape;" in source
    assert '"NOT_FOUND" | "TOO_MANY_REQUESTS"' in source
    assert 'code: "NOT_FOUND"; httpStatus: 404' in source
    assert "& PostRef" in source
    assert "& RateLimitInfo" in source
    assert "TRPCErrorShape" in source
    assert "TRPC_ERROR_CODE_KEY" in source
    # Undeclared procedures fall back to the router-wide union.
    assert "PlainProcedure" in source and "errorShape: AppRouterErrorShape" in source


def test_codegen_dedupes_shared_error_classes_in_router_union() -> None:
    router = Router()

    @router.query(errors=[PostNotFound])
    def first() -> str:
        raise PostNotFound("x", data=PostRef(post_id=1))

    @router.query(errors=[PostNotFound])
    def second() -> str:
        raise PostNotFound("x", data=PostRef(post_id=2))

    source = generate_types(router)
    # The router union holds the shared (code, model) member exactly once.
    union = source.split("export type AppRouterErrorShape =", 1)[1].split("\n\n", 1)[0]
    assert union.count("PostRef") == 1


def test_codegen_without_declared_errors_emits_default_shape() -> None:
    router = Router()

    @router.query
    def ok() -> bool:
        return True

    source = generate_types(router)
    assert "AppRouterErrorShape" not in source
    assert "errorShape: DefaultErrorShape;" in source
    assert "TRPCErrorShape" not in source


def test_codegen_error_member_without_model_has_no_intersection() -> None:
    router = Router()

    @router.query(errors=[ForbiddenWord])
    def deny() -> str:
        raise ForbiddenWord("no")

    source = generate_types(router)
    assert 'code: "FORBIDDEN"; httpStatus: 403' in source
    assert " & ForbiddenWord" not in source


def test_codegen_rejects_bad_error_class_code() -> None:
    class Sneaky(TRPCError):
        pass

    router = Router()

    @router.query(errors=[Sneaky])
    def proc() -> str:
        raise Sneaky("x")

    _ = proc  # decorator returns the handler; registration is what matters
    # Registration validated the class as declared; codegen re-checks because
    # class attributes can change between decoration and generation.
    Sneaky.code = "X"
    with pytest.raises(TypeError, match="TRPCErrorCode"):
        generate_types(router)


def test_codegen_rejects_bad_error_class_data_model() -> None:
    class Sneaky(TRPCError):
        pass

    router = Router()

    @router.query(errors=[Sneaky])
    def proc() -> str:
        raise Sneaky("x")

    _ = proc
    Sneaky.data_model = int
    with pytest.raises(TypeError, match="BaseModel"):
        generate_types(router)
