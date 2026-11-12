"""django-vanilla-ts: a Django backend called by a vanilla TypeScript frontend.

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/django-vanilla-ts && uv run --project ../.. python app.py
    # PORT=9000 to override the listen port (default 8000)

Then start the frontend (`pnpm --dir frontend dev`, see README.md) — the Vite
dev server on http://localhost:5173 proxies `/trpc` to this server, so calls
are same-origin and need no CORS. Settings are configured inline to keep the
example one file.
"""

from __future__ import annotations

import os

import django
from django.conf import settings

settings.configure(
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    SECRET_KEY="django-vanilla-ts-example",
    DATABASES={},
    INSTALLED_APPS=["django.contrib.contenttypes"],
)
django.setup()

from pydantic import BaseModel  # noqa: E402

from trpc_rs_py import Router, TRPCError, TRPCErrorCode  # noqa: E402
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

    @router.query
    def greeting(name: str) -> str:
        """A simple typed query rendered by the vanilla TS frontend."""
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

urlpatterns = [
    *trpc_urlpatterns(ROUTER),  # sync trpc_view — /trpc/*
]

application = None  # populated lazily so settings.configure runs first


def wsgi_app() -> object:
    """Return the Django WSGI application (configured in-module)."""
    from django.core.wsgi import get_wsgi_application

    return get_wsgi_application()


if __name__ == "__main__":
    from werkzeug.serving import run_simple

    run_simple(
        "127.0.0.1",
        int(os.environ.get("PORT", "8000")),
        wsgi_app(),
        threaded=True,
    )
