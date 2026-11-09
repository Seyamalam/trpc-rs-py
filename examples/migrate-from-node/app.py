"""migrate-from-node: a FastAPI app holding the procedures already moved to Python.

Two procedures of the original Node tRPC app (see `node-server.ts`) have been
reimplemented here:

- `posts.byId` keeps its original wire path; the migration link routes it to
  Python via its explicit `procedures` list, so the frontend is untouched.
- `py.posts_search` lives under the `py.` prefix, routed by the link's
  default prefix rule.

Run it (from the repository root, after `uv run maturin develop --release`):

    uv run --project . uvicorn --app-dir examples/migrate-from-node app:app --port 8000

Then run `node client.ts` in this directory (see README.md).
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

from trpc_rs_py import Router, mount


class Post(BaseModel):
    """A blog post shared between the Node and Python servers."""

    id: int
    title: str
    author: str


_POSTS = [
    Post(id=1, title="Hello from Python", author="ada"),
    Post(id=2, title="tRPC types without a codegen step", author="grace"),
    Post(id=3, title="Python post about search", author="ada"),
]


posts = Router()


@posts.query(name="byId")
def post_by_id(id: int) -> Post:  # noqa: A002 - the wire input key is `id`
    """Return one post — the Python replacement for the Node `posts.byId`."""
    for post in _POSTS:
        if post.id == id:
            return post
    return Post(id=id, title="not found", author="nobody")


moved = Router()


@moved.query
def posts_search(q: str = Field(min_length=1)) -> list[Post]:
    """Search posts — a moved procedure under the `py.` link prefix."""
    needle = q.lower()
    return [p for p in _POSTS if needle in p.title.lower()]


def build_router() -> Router:
    """Build the example router: same-path and `py.`-prefixed procedures."""
    router = Router()
    router.nest("posts", posts)
    router.nest("py", moved)
    return router


app = FastAPI()
app.mount("/trpc", mount(build_router()))

if __name__ == "__main__":
    uvicorn.run(app)
