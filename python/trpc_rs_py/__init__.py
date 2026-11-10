"""tRPC for Python backends, powered by a Rust core.

Wire-compatible with the official tRPC v11 client: define procedures in
Python, call them from TypeScript with full end-to-end types.

Example:
    ```python
    from trpc_rs_py import Router

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"
    ```

For the current status of the project see the docs and the feature matrix.

ASGI apps (FastAPI, Starlette, any ASGI framework) can serve a `Router` with
`mount` or `attach`:

Example:
    ```python
    from fastapi import FastAPI

    from trpc_rs_py import Router, mount

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"

    app = FastAPI()
    app.mount("/trpc", mount(router))
    ```
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

from ._core import Limits
from .asgi import ASGIApp, attach, mount
from .codegen import generate_types
from .errors import TRPCError, TRPCErrorCode, data_code_for
from .openapi import generate_openapi, merge_openapi
from .rest import mount_rest
from .router import Procedure, ProcedureKind, RestRoute, Router
from .streaming import Tracked, tracked
from .validation import build_input_validator, build_output_validator
from .wsgi import attach_flask, mount_wsgi, wsgi_from_asgi
from .zodgen import generate_zod

try:
    __version__ = _package_version("trpc-rs-py")
except PackageNotFoundError:  # running from a source checkout without install metadata
    __version__ = "0.0.0.dev0"

__all__ = [
    "ASGIApp",
    "Limits",
    "Procedure",
    "ProcedureKind",
    "RestRoute",
    "Router",
    "TRPCError",
    "TRPCErrorCode",
    "Tracked",
    "__version__",
    "attach",
    "attach_flask",
    "build_input_validator",
    "build_output_validator",
    "data_code_for",
    "generate_openapi",
    "generate_types",
    "generate_zod",
    "merge_openapi",
    "mount",
    "mount_rest",
    "mount_wsgi",
    "tracked",
    "wsgi_from_asgi",
]
