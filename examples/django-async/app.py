"""django-async: a minimal Django (ASGI) app serving a tRPC router.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/django-async && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then call it with the official `@trpc/client` (see README.md) at
`http://127.0.0.1:8000/trpc`; REST + OpenAPI live under `/api`.

The async views run the tRPC dispatch directly on Django's event loop, so
SSE subscriptions stream with real backpressure — no worker threads.
"""

from __future__ import annotations

import os

import django
from django.conf import settings

settings.configure(
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    SECRET_KEY="django-async-example",
    DATABASES={},
    INSTALLED_APPS=["django.contrib.contenttypes"],
)
django.setup()

from django.urls import path  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from trpc_rs_py import RestRoute, Router, TRPCError, TRPCErrorCode, mount_rest  # noqa: E402
from trpc_rs_py.django import asgi_view_async  # noqa: E402
from trpc_rs_py.django import urlpatterns as trpc_urlpatterns  # noqa: E402


class CreateUserInput(BaseModel):
    """Input for the `create_user` mutation."""

    name: str
    age: int


class User(BaseModel):
    """Output of both user procedures."""

    id: int
    name: str
    age: int


_USERS: dict[int, User] = {}
_NEXT_ID = 1


def build_router() -> Router:
    """Build the example tRPC router (also used to generate the TS types)."""
    router = Router()

    @router.query(rest=RestRoute("GET", "/greeting/{name}"))
    def greeting(name: str) -> str:
        """A query reachable via tRPC and REST at /api/greeting/<name>."""
        return f"Hello, {name}!"

    @router.query
    def user_by_id(user_id: int) -> User:
        """Fetch a user; raises NOT_FOUND for unknown ids."""
        user = _USERS.get(user_id)
        if user is None:
            raise TRPCError(f"User {user_id} not found", code=TRPCErrorCode.NOT_FOUND)
        return user

    @router.mutation
    def create_user(user: CreateUserInput) -> User:
        """Create a user; Pydantic validates and coerces the input."""
        global _NEXT_ID
        user = User(id=_NEXT_ID, name=user.name, age=user.age)
        _NEXT_ID += 1
        _USERS[user.id] = user
        return user

    return router


ROUTER = build_router()
REST = mount_rest(ROUTER, title="django-async", version="0.1.0")

urlpatterns = [
    *trpc_urlpatterns(ROUTER, asynchronous=True),  # async view — /trpc/*
    path("api/<path:path>", asgi_view_async(REST)),  # REST + OpenAPI — /api/*
]


def asgi_app() -> object:
    """Return the Django ASGI application (configured in-module)."""
    from django.core.asgi import get_asgi_application

    return get_asgi_application()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        asgi_app(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        log_level="error",
    )
