"""react-zod-form: a FastAPI backend whose validated mutation drives a React form.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/react-zod-form && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then start the frontend (`pnpm --dir frontend dev`, see README.md) — the React
form validates with the Zod schema generated from `RegisterInput`
(`app-schemas.ts`, produced by `generate_zod`), submits the `register`
mutation through `@trpc/react-query`, and displays the server's error when
Pydantic still rejects the payload.
"""

from __future__ import annotations

import os
from typing import Annotated

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field
from pydantic.types import StringConstraints

from trpc_rs_py import Router, mount
from trpc_rs_py.cors import cors

# Origins the Vite dev server serves the frontend from.
_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


class RegisterInput(BaseModel):
    """Registration form fields, validated on both sides.

    Pydantic checks them server-side and the generated Zod schema checks
    them client-side — same constraints, one source. (`StringConstraints`
    carries the regex through to `generate_zod`; `Field(pattern=...)` is
    Pydantic-internal and does not reach Zod.)
    """

    username: Annotated[
        str, StringConstraints(min_length=3, max_length=20, pattern=r"^[a-z0-9_]+$")
    ]
    email: Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    age: int = Field(ge=18, le=150)
    newsletter: bool = False


class Registration(BaseModel):
    """Output of the `register` mutation and the `registrations` query."""

    id: int
    username: str
    email: str
    age: int
    newsletter: bool


_REGISTRATIONS: list[Registration] = []
_NEXT_ID = 1


def build_router() -> Router:
    """Build the example tRPC router (also used to generate TS + Zod output)."""
    router = Router()

    @router.query
    def registrations() -> list[Registration]:
        """List registrations so far (newest last)."""
        return list(_REGISTRATIONS)

    @router.mutation
    def register(form: RegisterInput) -> Registration:
        """Register a user; invalid payloads map to HTTP 400 (BAD_REQUEST)."""
        global _NEXT_ID
        registration = Registration(id=_NEXT_ID, **form.model_dump())
        _NEXT_ID += 1
        _REGISTRATIONS.append(registration)
        return registration

    return router


def build_app() -> FastAPI:
    """Build the FastAPI app with the tRPC router at /trpc, CORS-enabled."""
    app = FastAPI()
    app.mount("/trpc", cors(mount(build_router()), allow_origins=_FRONTEND_ORIGINS))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
