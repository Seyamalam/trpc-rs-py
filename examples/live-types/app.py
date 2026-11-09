"""live-types example: `trpc-py dev` keeps app-router.d.ts in sync on save.

Run the watcher while you edit this file:

    trpc-py dev --app app:router --out app-router.d.ts \
        --server "uvicorn app:app --reload"

Add or change a procedure below, save, and `app-router.d.ts` — and the types
your TypeScript app sees — update in under a second.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, mount


class Note(BaseModel):
    """A note item — becomes a named TypeScript interface."""

    id: int
    text: str


router = Router()


@router.query
def notes() -> list[Note]:
    """List notes; returns a named TypeScript interface."""
    return [Note(id=1, text="try editing me")]


@router.mutation
def add_note(text: str) -> Note:
    """Add a note."""
    return Note(id=2, text=text)


def build_router() -> Router:
    """Return the example router (used by generate_types.py and smoke.sh)."""
    return router


app = FastAPI(title="live-types")
app.mount("/trpc", mount(router))
