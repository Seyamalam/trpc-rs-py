"""feature-pydantic-vs: Pydantic-declared vs hand-rolled input validation.

Two nested sub-routers serve the SAME signup mutation so both validation
styles can be compared over the wire:

- `pydantic.signup` — the input is declared as the Pydantic model
  `SignupForm`; the adapter builds a `build_input_validator` from the
  handler signature, so coercion and constraint checks run before the
  handler ever executes. Failures carry the fixed, value-free BAD_REQUEST
  message (`"Invalid input: form: Invalid value"`).
- `manual.signup` — the input is a bare `dict`, which the signature
  validator passes through as "any JSON object"; the handler checks every
  field itself and raises `TRPCError` with its own message when something
  is wrong.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/feature-pydantic-vs && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)
"""

from __future__ import annotations

import os
import re
from typing import Annotated

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import StringConstraints

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount

_USERNAME_RE = re.compile(r"[a-z0-9_]{3,20}")


class SignupForm(BaseModel):
    """The shared signup contract, declared once for the Pydantic side.

    `extra="forbid"` makes the model reject unknown keys itself — nested
    models otherwise follow Pydantic's default `ignore` behavior and
    silently drop them (the signature-level object is always forbidden-
    extra regardless).
    """

    model_config = ConfigDict(extra="forbid")

    username: Annotated[
        str, StringConstraints(min_length=3, max_length=20, pattern=r"^[a-z0-9_]+$")
    ]
    age: int = Field(ge=18, le=150)
    referral: str | None = None


class SignupResult(BaseModel):
    """Output of both signup procedures — deliberately identical."""

    username: str
    age: int
    referral: str | None = None


def build_pydantic_router() -> Router:
    """The declarative variant: all rules live in the model."""
    router = Router()

    @router.mutation
    def signup(form: SignupForm) -> SignupResult:
        """Return the validated signup unchanged.

        `form` arrives already validated and coerced — `"36"` was the
        string `age` on the wire and is the int `36` here; the handler
        only sees legal values.
        """
        return SignupResult(username=form.username, age=form.age, referral=form.referral)

    return router


def build_manual_router() -> Router:
    """Build the hand-rolled variant: signature says "any object", handler owns every check.

    `form` is annotated as a bare `dict` on purpose — `dict[str, Any]`
    expresses the same thing but `Any` has no JSON-compatible TypeScript
    type, so codegen rejects it; `dict` emits `Record<string, unknown>`,
    which is exactly what the wire contract is.
    """
    router = Router()

    @router.mutation
    def signup(form: dict) -> SignupResult:
        """Validate raw JSON by hand and return the signup.

        Each failure is a deliberate `TRPCError(BAD_REQUEST)` — the
        message is ours to write, which is the upside; keeping it
        value-free and not forgetting a check is the responsibility that
        comes with it.
        """
        username = form.get("username")
        if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
            raise TRPCError(
                "Invalid input: username: 3-20 chars of a-z, 0-9 or _ required",
                code=TRPCErrorCode.BAD_REQUEST,
            )
        age = form.get("age")
        # `isinstance(True, int)` is True — the classic hand-rolled
        # validation bug a schema library protects you from for free.
        if not isinstance(age, int) or isinstance(age, bool) or not 18 <= age <= 150:
            raise TRPCError(
                "Invalid input: age: integer between 18 and 150 required",
                code=TRPCErrorCode.BAD_REQUEST,
            )
        referral = form.get("referral")
        if referral is not None and not isinstance(referral, str):
            raise TRPCError(
                "Invalid input: referral: string required",
                code=TRPCErrorCode.BAD_REQUEST,
            )
        unknown = sorted(set(form) - {"username", "age", "referral"})
        if unknown:
            raise TRPCError(
                f"Invalid input: {unknown[0]}: unexpected field",
                code=TRPCErrorCode.BAD_REQUEST,
            )
        return SignupResult(username=username, age=age, referral=referral)

    return router


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query
    def health() -> str:
        """Liveness probe."""
        return "ok"

    router.nest("pydantic", build_pydantic_router())
    router.nest("manual", build_manual_router())
    return router


def build_app() -> FastAPI:
    """Build the FastAPI app with the tRPC router mounted at /trpc."""
    app = FastAPI()
    app.mount("/trpc", mount(build_router()))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
