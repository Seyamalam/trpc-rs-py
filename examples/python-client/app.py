"""python-client: a uvicorn-served router called by the tRPC Python client.

`trpc_rs_py.client.create_client` speaks the same tRPC v11 wire protocol as
the official TypeScript client, so Python services can call a `trpc_rs_py`
router — or any tRPC server — with the same envelopes and error shapes.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/python-client && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then run the client demo in another shell:

    cd examples/python-client && uv run --project ../.. python client.py
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode, mount


class Post(BaseModel):
    """A blog post returned by the post procedures."""

    id: int
    title: str
    priority: int


class PostRef(BaseModel):
    """Typed payload carried by `PostNotFound` errors."""

    post_id: int


class PostNotFound(TRPCError):
    """Typed error raised when a post id is unknown (ADR 0009)."""

    code = TRPCErrorCode.NOT_FOUND
    data_model = PostRef


_POSTS: dict[int, Post] = {}
_NEXT_ID = 1


def build_router() -> Router:
    """Build the example tRPC router (also used for both code generators)."""
    router = Router()

    @router.query
    def greeting(name: str) -> str:
        """A trivial query for client smoke coverage."""
        return f"Hello, {name}!"

    @router.query
    def list_posts() -> list[Post]:
        """A no-input query returning every post."""
        return list(_POSTS.values())

    @router.query(errors=[PostNotFound])
    def post_by_id(post_id: int) -> Post:
        """Fetch a post; raises the declared typed error for unknown ids."""
        post = _POSTS.get(post_id)
        if post is None:
            raise PostNotFound(f"Post {post_id} not found", data=PostRef(post_id=post_id))
        return post

    @router.mutation
    def create_post(title: str, priority: int = 0) -> Post:
        """Create a post; the default exercises stub default rendering."""
        global _NEXT_ID
        post = Post(id=_NEXT_ID, title=title, priority=priority)
        _NEXT_ID += 1
        _POSTS[post.id] = post
        return post

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
