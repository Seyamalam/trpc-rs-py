"""Django (sync/WSGI) fixture server for the official @trpc/client tests.

Same contract as `fastapi_app.py`: reads `TRPC_TEST_PORT`, prints `READY`,
serves `/trpc` via `trpc_view` (the sync view) and `/api` REST through the
same sync bridge. Settings are configured inline — no project scaffolding.
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
from trpc_rs_py.django import asgi_view, trpc_view

ROUTER = build_fixture_router()
REST_APP = mount_rest(ROUTER, title="Fixture API", version="0.1.0")

urlpatterns = [
    path("trpc/<path:trpc_path>", trpc_view(ROUTER)),
    path("api/<path:path>", asgi_view(REST_APP)),
]


def main() -> None:
    """Serve get_wsgi_application() via werkzeug's threaded server."""
    from django.core.wsgi import get_wsgi_application
    from werkzeug.serving import run_simple

    application = get_wsgi_application()
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    run_simple("127.0.0.1", port, application, threaded=True, use_reloader=False)


if __name__ == "__main__":
    sys.exit(main())
