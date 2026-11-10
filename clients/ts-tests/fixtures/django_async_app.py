"""Django (async/ASGI) fixture server for the official @trpc/client tests.

Same contract as `fastapi_app.py`: reads `TRPC_TEST_PORT`, prints `READY`,
serves `/trpc` via `trpc_view_async` under uvicorn + `get_asgi_application`,
and `/api` REST through the sync bridge offloaded to a thread.
"""

from __future__ import annotations

import os
import sys

import django
from django.conf import settings

settings.configure(
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    SECRET_KEY="fixture-secret",
    DATABASES={},
    INSTALLED_APPS=["django.contrib.contenttypes"],
)
django.setup()

from django.urls import path
from router import build_fixture_router

from trpc_rs_py import mount_rest
from trpc_rs_py.django import asgi_view_async, trpc_view_async

ROUTER = build_fixture_router()
REST_APP = mount_rest(ROUTER, title="Fixture API", version="0.1.0")

urlpatterns = [
    path("trpc/<path:trpc_path>", trpc_view_async(ROUTER)),
    path("api/<path:path>", asgi_view_async(REST_APP)),
]


def main() -> None:
    """Serve get_asgi_application() under uvicorn."""
    import uvicorn
    from django.core.asgi import get_asgi_application

    application = get_asgi_application()
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    sys.exit(main())
