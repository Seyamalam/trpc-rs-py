"""Django (async/ASGI) fixture server for the official @trpc/client tests.

Same contract as `fastapi_app.py`: reads `TRPC_TEST_PORT`, prints `READY`,
serves `/trpc` via `trpc_view_async` under uvicorn + `get_asgi_application`,
and `/api` REST through the sync bridge offloaded to a thread.
"""

from __future__ import annotations

import asyncio
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

from typing import Any

from django.http import HttpResponse
from django.urls import path
from router import build_fixture_router

from trpc_rs_py import mount_rest
from trpc_rs_py._bridge import serve_asgi_sync
from trpc_rs_py.django import _scope_from_request, trpc_view_async

ROUTER = build_fixture_router()
REST_APP = mount_rest(ROUTER, title="Fixture API", version="0.1.0")


async def rest_view(request: Any, rest_path: str = "") -> Any:
    """Serve the REST mount under /api; bridge runs on a worker thread."""
    try:
        body = request.body
    except Exception:
        body = b""
    resp = await asyncio.to_thread(
        serve_asgi_sync, REST_APP, _scope_from_request(request, rest_path), body
    )
    headers = {n.decode("latin-1"): v.decode("latin-1") for n, v in resp.headers}
    return HttpResponse(b"".join(resp.body), status=resp.status, headers=headers)


urlpatterns = [
    path("trpc/<path:trpc_path>", trpc_view_async(ROUTER)),
    path("api/<path:rest_path>", rest_view),
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
