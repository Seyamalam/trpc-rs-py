"""tRPC for Python backends, powered by a Rust core.

Wire-compatible with the official tRPC v11 client: define procedures in
Python, call them from TypeScript with full end-to-end types.

Example:
    ```python
    from trpc_rs_py import Router, procedure

    router = Router()

    @router.query
    def greeting(name: str) -> str:
        return f"Hello {name}!"
    ```

For the current status of the project see the docs and the feature matrix.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("trpc-rs-py")
except PackageNotFoundError:  # running from a source checkout without install metadata
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
