"""flask_app.py: serve the shared router through Flask (`mount_wsgi()`).

Run it (from the repository root, after `uv run maturin develop --release`):

    cd examples/shared-router && uv run --project ../.. python flask_app.py
    # PORT=9000 to override the listen port (default 8002)

The tRPC endpoints live at `http://127.0.0.1:8002/trpc`.
"""

from __future__ import annotations

import os

from flask import Flask
from shared_router import build_router
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple

from trpc_rs_py import mount_wsgi


def build_app() -> Flask:
    """Build the Flask app with the shared router dispatched at /trpc."""
    app = Flask(__name__)
    app.wsgi_app = DispatcherMiddleware(
        app.wsgi_app,
        {"/trpc": mount_wsgi(build_router())},
    )
    return app


app = build_app()

if __name__ == "__main__":
    run_simple("127.0.0.1", int(os.environ.get("PORT", "8002")), app, threaded=True)
