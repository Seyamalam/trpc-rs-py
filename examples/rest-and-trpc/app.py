"""rest-and-trpc: ONE router serving tRPC and REST + OpenAPI at the same time.

Procedures opt into REST with `rest=RestRoute(method, path)`. The same
router is mounted twice on one FastAPI app: `mount()` serves tRPC under
/trpc and `mount_rest()` serves REST endpoints plus `openapi.json` and a
docs page under /api — so TypeScript frontends use tRPC and every other
client uses REST, with identical validation on both.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/rest-and-trpc && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call tRPC at `http://127.0.0.1:8000/trpc`, REST at
`http://127.0.0.1:8000/api`, and browse the docs at
`http://127.0.0.1:8000/api/docs`.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import RestRoute, Router, TRPCError, TRPCErrorCode, mount, mount_rest


class CreatePostInput(BaseModel):
    """Input for the `create_post` mutation (REST body / tRPC input)."""

    title: str
    priority: int = 0


class Post(BaseModel):
    """A blog post returned by the post procedures."""

    id: int
    title: str
    priority: int


_POSTS: dict[int, Post] = {}
_NEXT_ID = 1


def build_router() -> Router:
    """Build the router; `RestRoute` opts each procedure into REST+OpenAPI."""
    router = Router()

    @router.query(rest=RestRoute("GET", "/greeting/{name}"))
    def greeting(name: str) -> str:
        """A query reachable via tRPC and REST at /api/greeting/<name>."""
        return f"Hello, {name}!"

    @router.query(rest=RestRoute("GET", "/posts/{post_id}"))
    def post_by_id(post_id: int) -> Post:
        """Fetch a post; raises NOT_FOUND for unknown ids on both protocols."""
        post = _POSTS.get(post_id)
        if post is None:
            raise TRPCError(f"Post {post_id} not found", code=TRPCErrorCode.NOT_FOUND)
        return post

    @router.mutation(rest=RestRoute("POST", "/posts"))
    def create_post(post: CreatePostInput) -> Post:
        """Create a post; REST reads the body, tRPC reads the `post` input."""
        global _NEXT_ID
        post = Post(id=_NEXT_ID, title=post.title, priority=post.priority)
        _NEXT_ID += 1
        _POSTS[post.id] = post
        return post

    return router


def build_app() -> FastAPI:
    """Build the app: one router on /trpc (tRPC) and /api (REST+OpenAPI)."""
    router = build_router()
    app = FastAPI()
    app.mount("/trpc", mount(router))
    app.mount("/api", mount_rest(router, title="rest-and-trpc", version="0.1.0"))
    return app


app = build_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
