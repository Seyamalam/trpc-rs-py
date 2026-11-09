"""`trpc-py dev`: live TypeScript type sync while editing Python.

The loop is intentionally small and observable:

1. Generate the `.d.ts` in a **fresh subprocess** (`_dev_loader`), so every
   save sees real module state instead of `importlib.reload` staleness.
2. Write to a sibling temp file and `os.replace` it **only when the content
   actually changed**, so the TypeScript language server is not reloaded
   needlessly (MISSION section 2.1).
3. Watch the router's sources with the Rust `notify` backend (`_core.Watcher`)
   in debounced batches — one save regenerates once.
4. Optionally spawn the user's ASGI server with reload (`--server`), so one
   command runs the whole backend side of development.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["PRODUCTION_ENV_VAR", "is_production", "regenerate", "resolve_watch_dirs", "run_dev"]

PRODUCTION_ENV_VAR = "TRPC_RS_PY_PRODUCTION"
"""Environment variable that marks a deployment as production.

`trpc-py dev` refuses to run when it is set to a truthy value, per MISSION
section 6 — live type sync is a development-only tool.
"""

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_production() -> bool:
    """Return `True` when the environment marks a production deployment.

    Example:
        ```python
        import os
        from trpc_rs_py.dev import is_production

        os.environ["TRPC_RS_PY_PRODUCTION"] = "1"
        assert is_production()
        del os.environ["TRPC_RS_PY_PRODUCTION"]
        ```
    """
    return os.environ.get(PRODUCTION_ENV_VAR, "").strip().lower() in _TRUTHY


def resolve_watch_dirs(app_spec: str, extra_dirs: Sequence[str] = ()) -> list[Path]:
    """Choose the directories to watch for a `--app module:attr` spec.

    Args:
        app_spec: Import string such as `myapp.server:router`; its top-level
            module directory is always watched when it can be located.
        extra_dirs: Additional `--watch` directories from the CLI.

    Returns:
        Existing directories, deduplicated; the current directory when
        nothing else can be located.

    Example:
        ```python
        from trpc_rs_py.dev import resolve_watch_dirs

        dirs = resolve_watch_dirs("builtins:len")
        assert all(d.is_dir() for d in dirs)
        ```
    """
    candidates: list[Path] = []
    module_name, _, _ = app_spec.partition(":")
    top_level = module_name.split(".", 1)[0]
    spec = importlib.util.find_spec(top_level) if top_level else None
    if spec is not None:
        if spec.submodule_search_locations:
            candidates.append(Path(next(iter(spec.submodule_search_locations))))
        elif spec.origin:
            candidates.append(Path(spec.origin).parent)
    for raw in extra_dirs:
        candidates.append(Path(raw))
    if not candidates:
        candidates.append(Path.cwd())

    resolved: list[Path] = []
    for candidate in candidates:
        directory = candidate.resolve()
        if directory.is_dir() and directory not in resolved:
            resolved.append(directory)
    return resolved or [Path.cwd()]


def _swap_if_changed(tmp: Path, out: Path) -> bool:
    """Atomically replace `out` with `tmp` when contents differ."""
    new = tmp.read_bytes()
    if out.exists() and out.read_bytes() == new:
        tmp.unlink(missing_ok=True)
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, out)
    return True


def regenerate(app_spec: str, out: Path, zod_out: Path | None = None) -> bool:
    """Regenerate `out` from `--app` in a fresh subprocess, atomically.

    The subprocess writes sibling `*.tmp` files; the content is compared and
    `os.replace` applied only when it differs, so untouched types never touch
    the file's mtime. The temp file is removed either way, so a failed run
    leaves the last good `.d.ts` untouched. When `zod_out` is given the same
    subprocess also writes the zod schema module there.

    Args:
        app_spec: Import string such as `server:router`.
        out: Destination `.d.ts` path.
        zod_out: Optional destination `.ts` path for zod input schemas.

    Returns:
        `True` when any file was (re)written, `False` on failure or no change.

    Example:
        ```python
        from pathlib import Path
        from trpc_rs_py.dev import regenerate

        wrote = regenerate("builtins:len", Path("/tmp/x.d.ts"))
        assert not wrote  # len is not a Router — failure, file absent
        ```
    """
    tmp = out.with_name(out.name + ".tmp")
    zod_tmp = zod_out.with_name(zod_out.name + ".tmp") if zod_out else None
    command = [sys.executable, "-m", "trpc_rs_py._dev_loader", app_spec, str(tmp)]
    if zod_tmp is not None:
        command.append(str(zod_tmp))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"trpc-py dev: regeneration failed: {error}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        if zod_tmp is not None:
            zod_tmp.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "unknown error"
        print(f"trpc-py dev: could not reload {app_spec}:", file=sys.stderr)
        print(details, file=sys.stderr)
        tmp.unlink(missing_ok=True)
        if zod_tmp is not None:
            zod_tmp.unlink(missing_ok=True)
        return False
    changed = _swap_if_changed(tmp, out)
    if zod_tmp is not None and zod_out is not None:
        changed = _swap_if_changed(zod_tmp, zod_out) or changed
    return changed


def _start_server(server_spec: str, reload_dirs: Sequence[Path]) -> subprocess.Popen[bytes]:
    """Spawn the user's ASGI server with reload via `uvicorn`.

    Raises:
        RuntimeError: When `uvicorn` is not installed in this environment.
    """
    if importlib.util.find_spec("uvicorn") is None:
        msg = (
            "--server requires uvicorn in this environment "
            "(or start your server separately without --server)"
        )
        raise RuntimeError(msg)
    command = [sys.executable, "-m", "uvicorn", server_spec, "--reload"]
    for directory in reload_dirs:
        command += ["--reload-dir", str(directory)]
    return subprocess.Popen(command)


def run_dev(
    app_spec: str,
    out: Path,
    *,
    watch_dirs: Sequence[str] = (),
    server: str | None = None,
    debounce_ms: int = 100,
    zod_out: Path | None = None,
) -> int:
    """Run the live-type-sync loop until interrupted.

    Args:
        app_spec: Import string such as `server:router`.
        out: Destination `.d.ts` path.
        watch_dirs: Extra directories to watch beyond the app's own sources.
        server: Optional `module:app` ASGI import string started with reload.
        debounce_ms: Quiet-window in milliseconds before a save burst
            regenerates (the watcher also caps coalescing at 2 s).
        zod_out: Optional destination `.ts` path also regenerated on every
            save with zod input schemas (`codegen zod` output).

    Returns:
        0 on `KeyboardInterrupt`; a nonzero exit code never reaches the
        caller — setup failures raise before the loop starts.

    Raises:
        RuntimeError: When production mode is set or `--server` cannot start.
        ValueError: When the Rust watcher cannot be created.

    Example:
        ```python
        # trpc-py dev --app server:router --out app/router.d.ts
        # Equivalent:
        from pathlib import Path
        from trpc_rs_py.dev import run_dev

        # run_dev("server:router", Path("app/router.d.ts"))
        ```
    """
    if is_production():
        msg = f"trpc-py dev refuses to run in production ({PRODUCTION_ENV_VAR} is set)"
        raise RuntimeError(msg)

    from ._core import Watcher

    directories = resolve_watch_dirs(app_spec, watch_dirs)
    watcher = Watcher([str(directory) for directory in directories])
    server_proc = _start_server(server, directories) if server else None

    def sync() -> None:
        if regenerate(app_spec, out, zod_out):
            print(f"trpc-py dev: wrote {out}")

    print(f"trpc-py dev: watching {', '.join(str(d) for d in directories)}")
    sync()  # First sync happens even before any save.
    try:
        while True:
            try:
                # A bounded wait lets the interpreter deliver KeyboardInterrupt
                # between calls — an unbounded native wait would ignore SIGINT
                # until the next fs event arrived.
                batch = watcher.next(timeout_ms=500, debounce_ms=debounce_ms)
            except Exception as error:  # watcher backend error — keep watching
                print(f"trpc-py dev: watcher error: {error}", file=sys.stderr)
                continue
            if batch is None:
                continue
            changed = [path for path in batch if path.endswith(".py")]
            if not changed:
                continue
            for path in changed:
                print(f"trpc-py dev: changed {path}")
            sync()
    except KeyboardInterrupt:
        print("trpc-py dev: stopped")
        return 0
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
