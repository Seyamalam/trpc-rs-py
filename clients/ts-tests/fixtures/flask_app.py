"""Flask fixture server for the official @trpc/client runtime tests.

Same contract as `fastapi_app.py`: reads `TRPC_TEST_PORT`, prints `READY`,
serves the fixture router under `/trpc` and the REST mount under `/api` —
through the WSGI adapter (DispatcherMiddleware) instead of ASGI.
"""

from __future__ import annotations

import os
import sys

from flask import Flask
from router import build_fixture_router
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from trpc_rs_py import mount_rest, mount_wsgi, wsgi_from_asgi


def main() -> None:
    """Serve the fixture router with werkzeug's threaded dev server."""
    from werkzeug.serving import run_simple

    router = build_fixture_router()
    app = Flask(__name__)
    app.wsgi_app = DispatcherMiddleware(
        app.wsgi_app,
        {
            "/trpc": mount_wsgi(router),
            "/api": wsgi_from_asgi(mount_rest(router, title="Fixture API", version="0.1.0")),
        },
    )
    port = int(os.environ["TRPC_TEST_PORT"])
    print("READY", flush=True)
    run_simple("127.0.0.1", port, app, threaded=True, use_reloader=False)


if __name__ == "__main__":
    sys.exit(main())
