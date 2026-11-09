"""`trpc-py` command-line interface.

Python `argparse` implementation per ADR 0006 — the command names, flags,
exit codes, and `--json` output are the stable contract; the implementation
language may move to `clap` when the standalone server ships. Commands:

* `dev` — live type sync: watch Python sources, regenerate `.d.ts` on save.
* `codegen ts` — one-off TypeScript declaration generation.
* `routes` — list procedures with kinds and signatures.
* `check` — validate that a router generates cleanly.
* `doctor` — environment info for bug reports.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ._dev_loader import load_app_object

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .router import Router

__all__ = ["build_parser", "main"]


def _load_router(spec: str) -> Router:
    """Resolve `module:attr` to a `Router`, or exit 2 with the reason."""
    from .router import Router

    try:
        app = load_app_object(spec)
    except (ValueError, ImportError, AttributeError) as error:
        print(f"trpc-py: cannot load {spec!r}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    if not isinstance(app, Router):
        print(
            f"trpc-py: {spec!r} resolved to {type(app).__name__}, not a trpc_rs_py.Router",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return app


def _cmd_dev(args: argparse.Namespace) -> int:
    from .dev import run_dev

    try:
        return run_dev(
            args.app,
            Path(args.out),
            watch_dirs=args.watch,
            server=args.server,
            debounce_ms=args.debounce_ms,
        )
    except (RuntimeError, ValueError) as error:
        print(f"trpc-py dev: {error}", file=sys.stderr)
        return 1


def _cmd_codegen(args: argparse.Namespace) -> int:
    from .codegen import generate_types

    router = _load_router(args.app)
    try:
        source = generate_types(router)
    except TypeError as error:
        print(f"trpc-py codegen: {error}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source)
    print(f"trpc-py codegen: wrote {out}")
    return 0


def _cmd_codegen_openapi(args: argparse.Namespace) -> int:
    """Handle `trpc-py codegen openapi`: write the OpenAPI document."""
    import json

    from .openapi import generate_openapi

    router = _load_router(args.app)
    kwargs: dict[str, str] = {}
    if args.title is not None:
        kwargs["title"] = args.title
    if args.version is not None:
        kwargs["version"] = args.version
    document = generate_openapi(router, **kwargs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n")
    print(f"trpc-py codegen: wrote {out}")
    return 0


def _signature_line(handler: object) -> str:
    """One-line signature for `routes` output, parameters only."""
    import inspect

    try:
        signature = inspect.signature(handler)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "(…)"
    return (
        "("
        + ", ".join(
            f"{param.name}: {param.annotation}"
            if param.annotation is not inspect.Parameter.empty
            else param.name
            for param in signature.parameters.values()
        )
        + ")"
    )


def _cmd_routes(args: argparse.Namespace) -> int:
    router = _load_router(args.app)
    procedures = router.procedures
    if args.json:
        payload = [
            {
                "path": proc.path,
                "kind": proc.kind.value,
                "handler": getattr(proc.handler, "__name__", repr(proc.handler)),
            }
            for proc in procedures.values()
        ]
        print(json.dumps(payload, indent=2))
        return 0
    for proc in procedures.values():
        print(f"{proc.kind.value:12} {proc.path:24} {_signature_line(proc.handler)}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from .codegen import generate_types

    router = _load_router(args.app)
    count = len(router.procedures)
    try:
        generate_types(router)
    except TypeError as error:
        print(f"trpc-py check: {error}", file=sys.stderr)
        return 1
    print(f"trpc-py check: ok — {count} procedure{'s' if count != 1 else ''}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from ._core import __version__ as core_version

    info = {
        "trpc_rs_py": core_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pydantic": _dependency_version("pydantic"),
        "cwd": str(Path.cwd()),
    }
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        for key, value in info.items():
            print(f"{key:12} {value}")
    return 0


def _dependency_version(package: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"


def build_parser() -> argparse.ArgumentParser:
    """Construct the `trpc-py` argument parser.

    Returns:
        The parser with every subcommand wired to its handler.

    Example:
        ```python
        from trpc_rs_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["doctor", "--json"])
        assert args.command == "doctor"
        ```
    """
    from ._core import __version__ as core_version

    parser = argparse.ArgumentParser(
        prog="trpc-py",
        description="tRPC for Python backends — live type sync, codegen, checks.",
    )
    parser.add_argument("--version", action="version", version=f"trpc-py {core_version}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    dev = subcommands.add_parser(
        "dev", help="watch Python sources and regenerate TypeScript types on save"
    )
    dev.add_argument("--app", required=True, help="router import string (module:attribute)")
    dev.add_argument("--out", required=True, help="destination .d.ts path")
    dev.add_argument(
        "--watch",
        action="append",
        default=[],
        help="extra directory to watch (repeatable)",
    )
    dev.add_argument(
        "--server",
        default=None,
        help="also start the ASGI app with reload (module:app, via uvicorn)",
    )
    dev.add_argument(
        "--debounce-ms",
        type=int,
        default=100,
        help="quiet window before a save burst regenerates (default: 100)",
    )
    dev.set_defaults(handler=_cmd_dev)

    codegen = subcommands.add_parser("codegen", help="one-off code generation")
    codegen_sub = codegen.add_subparsers(dest="target", required=True)
    codegen_ts = codegen_sub.add_parser("ts", help="emit the AppRouter .d.ts")
    codegen_ts.add_argument("--app", required=True, help="router import string (module:attribute)")
    codegen_ts.add_argument("--out", required=True, help="destination .d.ts path")
    codegen_ts.set_defaults(handler=_cmd_codegen)
    codegen_openapi = codegen_sub.add_parser(
        "openapi", help="emit the OpenAPI 3.1 document for RestRoute procedures"
    )
    codegen_openapi.add_argument(
        "--app", required=True, help="router import string (module:attribute)"
    )
    codegen_openapi.add_argument("--out", required=True, help="destination .json path")
    codegen_openapi.add_argument("--title", default=None, help="info.title override")
    codegen_openapi.add_argument("--version", default=None, help="info.version override")
    codegen_openapi.set_defaults(handler=_cmd_codegen_openapi)

    routes = subcommands.add_parser("routes", help="list all procedures")
    routes.add_argument("--app", required=True, help="router import string (module:attribute)")
    routes.add_argument("--json", action="store_true", help="emit JSON")
    routes.set_defaults(handler=_cmd_routes)

    check = subcommands.add_parser("check", help="validate a router generates cleanly")
    check.add_argument("--app", required=True, help="router import string (module:attribute)")
    check.set_defaults(handler=_cmd_check)

    doctor = subcommands.add_parser("doctor", help="print environment info for bug reports")
    doctor.add_argument("--json", action="store_true", help="emit JSON")
    doctor.set_defaults(handler=_cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list; `sys.argv[1:]` when `None`.

    Returns:
        The subcommand's exit code (0 success, 1 failure, 2 usage).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
