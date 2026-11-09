"""Subprocess loader for `trpc-py dev` and `trpc-py codegen`.

Run as `python -m trpc_rs_py._dev_loader <module:attr> <output.tmp>`: imports
the router in a fresh interpreter (so every save sees real module state),
generates the TypeScript declarations, and writes them to the given path.
The parent process performs the atomic rename, so a failed run leaves the
last good `.d.ts` untouched.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import cast


def load_app_object(spec: str) -> object:
    """Import `module:attr` and return the referenced object.

    Args:
        spec: Import string such as `server:router` or
            `pkg.server:build_router()`. A trailing `()` calls the attribute
            and returns its result.

    Returns:
        The imported (or factory-produced) object.

    Raises:
        ValueError: If `spec` does not contain a `module:attribute` split.
        ImportError: If the module cannot be imported.
        AttributeError: If the attribute does not exist.
    """
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        msg = f"invalid import string {spec!r}: expected module:attribute"
        raise ValueError(msg)
    calls = attribute.endswith("()")
    name = attribute[:-2] if calls else attribute
    obj: object = importlib.import_module(module_name)
    for part in name.split("."):
        obj = getattr(obj, part)
    if calls:
        obj = cast(Callable[[], object], obj)()
    return obj


def main() -> int:
    """Load the router and write its generated declarations.

    Returns:
        0 on success, 1 when the import or generation fails (the traceback is
        printed for the parent's terminal, which keeps the last good file).
    """
    if len(sys.argv) != 3:
        print("usage: python -m trpc_rs_py._dev_loader <module:attr> <output>", file=sys.stderr)
        return 2
    spec, out = sys.argv[1], Path(sys.argv[2])
    try:
        app = load_app_object(spec)
        from trpc_rs_py.codegen import generate_types
        from trpc_rs_py.router import Router

        if not isinstance(app, Router):
            print(
                f"{spec!r} resolved to {type(app).__name__}, not a trpc_rs_py.Router",
                file=sys.stderr,
            )
            return 1
        out.write_text(generate_types(app))
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
