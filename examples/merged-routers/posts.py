"""posts.py: the posts sub-router for the merged-routers example.

`posts_router()` returns a `Router` owning the `posts.*` procedures. It also
nests a `comments` router of its own, so the app ends up with the two-level
path `posts.comments.for_post` — nesting composes.
"""

from __future__ import annotations

from pydantic import BaseModel

from trpc_rs_py import Router, TRPCError, TRPCErrorCode


class Post(BaseModel):
    """A blog post returned by the posts procedures."""

    id: int
    title: str


class Comment(BaseModel):
    """A comment attached to a post."""

    post_id: int
    body: str


_POSTS: dict[int, Post] = {}
_COMMENTS: dict[int, list[Comment]] = {}
_NEXT_ID = 1


def comments_router() -> Router:
    """Build the comments router; nested under posts as `posts.comments.*`."""
    router = Router()

    @router.query
    def for_post(post_id: int) -> list[Comment]:
        """List every comment on a post."""
        return _COMMENTS.get(post_id, [])

    @router.mutation
    def add(post_id: int, body: str) -> Comment:
        """Add a comment; fails NOT_FOUND when the post does not exist."""
        if post_id not in _POSTS:
            raise TRPCError(f"Post {post_id} not found", code=TRPCErrorCode.NOT_FOUND)
        comment = Comment(post_id=post_id, body=body)
        _COMMENTS.setdefault(post_id, []).append(comment)
        return comment

    return router


def posts_router() -> Router:
    """Build the posts router; the app nests it as `posts.*`."""
    router = Router()

    @router.query
    def by_id(post_id: int) -> Post:
        """Fetch a post; raises NOT_FOUND for unknown ids."""
        post = _POSTS.get(post_id)
        if post is None:
            raise TRPCError(f"Post {post_id} not found", code=TRPCErrorCode.NOT_FOUND)
        return post

    @router.mutation
    def create(title: str) -> Post:
        """Create a post."""
        global _NEXT_ID
        post = Post(id=_NEXT_ID, title=title)
        _NEXT_ID += 1
        _POSTS[post.id] = post
        return post

    router.nest("comments", comments_router())
    return router
