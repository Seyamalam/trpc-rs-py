"""In-process tests for `trpc_rs_py._dev_loader` and `trpc_rs_py.dev`.

test_dev.py drives `trpc-py dev` through a real subprocess (which coverage
cannot see); these tests cover the in-process pieces: the `_dev_loader`
module entry point, `resolve_watch_dirs` fallback branches, `regenerate`'s
zod sibling output and failure cleanup, `_start_server`'s uvicorn spawn,
and the `run_dev` watch loop driven by a scripted `_core.Watcher` stand-in.
"""

from __future__ import annotations

import runpy
import subprocess
import sys
import warnings
from collections import deque
from pathlib import Path
from typing import Any

import pytest

import trpc_rs_py.dev as dev_module
from trpc_rs_py import _core
from trpc_rs_py._dev_loader import load_app_object
from trpc_rs_py._dev_loader import main as loader_main
from trpc_rs_py.dev import _start_server, regenerate, resolve_watch_dirs, run_dev


def _write_router_module(
    tmp_path: Path,
    body: str | None = None,
    name: str = "router_mod",
) -> Path:
    """Write a `<name>.py` fixture exposing `router` (or a custom body)."""
    if body is None:
        body = """from trpc_rs_py import Router

router = Router()

@router.query(name="q0")
def q0(name: str) -> str:
    return name
"""
    module = tmp_path / f"{name}.py"
    module.write_text(body)
    return module


# -- _dev_loader ------------------------------------------------------------


def test_load_app_object_calls_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `module:factory()` spec calls the attribute and returns its result."""
    _write_router_module(
        tmp_path,
        body="""from trpc_rs_py import Router

def build() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    return router

router = build()
""",
        name="loader_mod_factory",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from trpc_rs_py import Router

    assert isinstance(load_app_object("loader_mod_factory:build()"), Router)
    assert isinstance(load_app_object("loader_mod_factory:router"), Router)


def test_loader_main_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wrong argument count prints usage to stderr and exits 2."""
    monkeypatch.setattr(sys, "argv", ["trpc_rs_py._dev_loader", "only-one-arg"])
    assert loader_main() == 2
    assert "usage:" in capsys.readouterr().err


def test_loader_main_writes_types_and_zod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4-arg invocation writes both the .d.ts and the zod schema module."""
    _write_router_module(tmp_path, name="loader_mod_gen")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "router.d.ts"
    zod = tmp_path / "router.zod.ts"
    monkeypatch.setattr(
        sys,
        "argv",
        ["trpc_rs_py._dev_loader", "loader_mod_gen:router", str(out), str(zod)],
    )
    assert loader_main() == 0
    assert "Q0Procedure" in out.read_text()
    assert "export const Q0Input" in zod.read_text()


def test_loader_main_rejects_non_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A spec resolving to a non-Router object reports and exits 1."""
    (tmp_path / "loader_mod_plain.py").write_text("app = 42\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "router.d.ts"
    monkeypatch.setattr(
        sys,
        "argv",
        ["trpc_rs_py._dev_loader", "loader_mod_plain:app", str(out)],
    )
    assert loader_main() == 1
    assert "not a trpc_rs_py.Router" in capsys.readouterr().err
    assert not out.exists()


def test_loader_main_import_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unimportable spec prints the traceback and exits 1."""
    out = tmp_path / "router.d.ts"
    monkeypatch.setattr(
        sys,
        "argv",
        ["trpc_rs_py._dev_loader", "missing_mod_xyz:router", str(out)],
    )
    assert loader_main() == 1
    assert capsys.readouterr().err
    assert not out.exists()


def test_loader_main_guard_runs_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m trpc_rs_py._dev_loader ...` reaches the __main__ guard."""
    _write_router_module(tmp_path, name="loader_mod_guard")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "router.d.ts"
    monkeypatch.setattr(
        sys,
        "argv",
        ["trpc_rs_py._dev_loader", "loader_mod_guard:router", str(out)],
    )
    with warnings.catch_warnings():
        # run_module warns that the module is already in sys.modules.
        warnings.simplefilter("ignore")
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("trpc_rs_py._dev_loader", run_name="__main__")
    assert excinfo.value.code == 0
    assert "Q0Procedure" in out.read_text()


# -- dev.resolve_watch_dirs ---------------------------------------------------


def test_resolve_watch_dirs_package_module() -> None:
    """A package spec watches the package directory itself."""
    dirs = resolve_watch_dirs("trpc_rs_py.codegen:router")
    assert dirs == [Path(dev_module.__file__).resolve().parent]


def test_resolve_watch_dirs_extra_dirs_deduplicated(tmp_path: Path) -> None:
    """`--watch` dirs are appended, resolved, and deduplicated."""
    dirs = resolve_watch_dirs(
        "trpc_rs_py:router", [str(tmp_path), str(tmp_path), str(tmp_path / "nope")]
    )
    assert tmp_path.resolve() in dirs
    assert dirs.count(tmp_path.resolve()) == 1
    # Nonexistent extra dirs are filtered out.
    assert all(d.is_dir() for d in dirs)


def test_resolve_watch_dirs_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unlocatable module spec (with or without extras) watches cwd."""
    monkeypatch.chdir(tmp_path)
    assert resolve_watch_dirs("missing_mod_xyz:router") == [Path.cwd()]
    assert resolve_watch_dirs("missing_mod_xyz:router", ["no-such-dir"]) == [Path.cwd()]


# -- dev.regenerate ---------------------------------------------------------


def test_regenerate_writes_zod_sibling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`zod_out` is written by the same subprocess run; unchanged runs skip."""
    _write_router_module(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    out = tmp_path / "router.d.ts"
    zod = tmp_path / "router.zod.ts"

    assert regenerate("router_mod:router", out, zod_out=zod)
    assert "Q0Procedure" in out.read_text()
    assert "export const Q0Input" in zod.read_text()

    # Second run: nothing changed, neither file is rewritten.
    assert not regenerate("router_mod:router", out, zod_out=zod)


def test_regenerate_failure_removes_zod_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed run cleans up the zod temp file alongside the .d.ts temp."""
    _write_router_module(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    out = tmp_path / "router.d.ts"
    zod = tmp_path / "router.zod.ts"

    assert not regenerate("router_mod:missing", out, zod_out=zod)
    assert "could not reload" in capsys.readouterr().err
    assert not out.with_name(out.name + ".tmp").exists()
    assert not zod.with_name(zod.name + ".tmp").exists()


def test_regenerate_subprocess_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A subprocess that never completes reports the failure, not a traceback."""

    def boom(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="loader", timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    out = tmp_path / "router.d.ts"
    zod = tmp_path / "router.zod.ts"
    assert not regenerate("router_mod:router", out, zod_out=zod)
    assert "regeneration failed" in capsys.readouterr().err
    assert not out.with_name(out.name + ".tmp").exists()
    assert not zod.with_name(zod.name + ".tmp").exists()


# -- dev._start_server --------------------------------------------------------


def test_start_server_requires_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--server` fails fast with an actionable message when uvicorn is absent."""
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "uvicorn" else real_find_spec(name, *a, **k),
    )
    with pytest.raises(RuntimeError, match="uvicorn"):
        _start_server("app:main", [])


def test_start_server_spawns_uvicorn_reload(tmp_path: Path) -> None:
    """The managed server is `python -m uvicorn <spec> --reload --reload-dir ...`."""
    proc = _start_server("srv:app", [tmp_path])
    try:
        command = [str(arg) for arg in proc.args]
        assert command[:4] == [sys.executable, "-m", "uvicorn", "srv:app"]
        assert "--reload" in command
        assert command.count("--reload-dir") == 1
        assert str(tmp_path) in command
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# -- dev.run_dev ------------------------------------------------------------


class _ScriptedWatcher:
    """`_core.Watcher` stand-in yielding scripted batches, then raising.

    Each queued item is either a batch (`list[str]`/`None`) returned by
    `next`, or an exception to raise — `RuntimeError` exercises the
    keep-watching backend-error path, `KeyboardInterrupt` the clean stop.
    """

    def __init__(
        self, directories: list[str], script: deque[list[str] | BaseException | None]
    ) -> None:
        self.directories = list(directories)
        self._script = script

    def next(self, *, timeout_ms: int, debounce_ms: int) -> list[str] | None:
        item = self._script.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeProc:
    """`subprocess.Popen` stand-in recording terminate/wait/kill."""

    def __init__(self, *, hang_on_wait: bool = False) -> None:
        self.terminated = False
        self.killed = False
        self._hang = hang_on_wait

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="uvicorn", timeout=timeout)
        return 0

    def kill(self) -> None:
        self.killed = True


def _install_watcher(
    monkeypatch: pytest.MonkeyPatch, script: deque[list[str] | BaseException | None]
) -> list[list[str]]:
    """Patch `_core.Watcher` to hand out scripted watchers; returns seen dirs."""
    seen: list[list[str]] = []

    def factory(directories: list[str]) -> _ScriptedWatcher:
        seen.append(list(directories))
        return _ScriptedWatcher(directories, script)

    monkeypatch.setattr(_core, "Watcher", factory)
    return seen


def test_run_dev_watch_loop_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Drive the loop through timeout/skip/error/save/stop events.

    The loop skips timeouts and non-.py events, survives backend errors,
    regenerates on .py saves, and returns 0 on KeyboardInterrupt.
    """
    script: deque[list[str] | BaseException | None] = deque(
        [
            None,  # debounce timeout — keep watching
            ["notes.txt"],  # non-Python change — skipped
            RuntimeError("backend blew up"),  # watcher error — logged, keep watching
            ["mod.py"],  # Python save — regenerate
            KeyboardInterrupt(),
        ]
    )
    _install_watcher(monkeypatch, script)
    writes: list[Path | None] = []
    monkeypatch.setattr(
        dev_module,
        "regenerate",
        lambda spec, out, zod_out=None: writes.append(zod_out) or True,
    )

    assert run_dev("trpc_rs_py:router", tmp_path / "out.d.ts") == 0

    captured = capsys.readouterr()
    assert "trpc-py dev: watching" in captured.out
    assert "trpc-py dev: changed mod.py" in captured.out
    assert captured.out.count("trpc-py dev: wrote") == 2
    assert "watcher error" in captured.err
    assert "backend blew up" in captured.err
    assert "trpc-py dev: stopped" in captured.out
    # One initial sync plus one save-driven sync.
    assert len(writes) == 2


def test_run_dev_quiet_regenerate_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A no-change regeneration does not print the `wrote` line."""
    script: deque[list[str] | BaseException | None] = deque([KeyboardInterrupt()])
    _install_watcher(monkeypatch, script)
    monkeypatch.setattr(dev_module, "regenerate", lambda spec, out, zod_out=None: False)

    assert run_dev("trpc_rs_py:router", tmp_path / "out.d.ts") == 0
    captured = capsys.readouterr()
    assert "trpc-py dev: wrote" not in captured.out
    assert "trpc-py dev: stopped" in captured.out


def test_run_dev_terminates_managed_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--server` spawns through `_start_server` and is terminated on stop."""
    script: deque[list[str] | BaseException | None] = deque([KeyboardInterrupt()])
    _install_watcher(monkeypatch, script)
    monkeypatch.setattr(dev_module, "regenerate", lambda spec, out, zod_out=None: False)
    proc = _FakeProc()
    started: dict[str, Any] = {}

    def fake_start(server_spec: str, reload_dirs: Any) -> _FakeProc:
        started["spec"] = server_spec
        started["dirs"] = list(reload_dirs)
        return proc

    monkeypatch.setattr(dev_module, "_start_server", fake_start)

    assert run_dev("trpc_rs_py:router", tmp_path / "out.d.ts", server="srv:app") == 0
    assert started["spec"] == "srv:app"
    assert started["dirs"], "watch dirs must reach --reload-dir"
    assert proc.terminated
    assert not proc.killed


def test_run_dev_kills_unresponsive_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server ignoring terminate is killed after the wait window."""
    script: deque[list[str] | BaseException | None] = deque([KeyboardInterrupt()])
    _install_watcher(monkeypatch, script)
    monkeypatch.setattr(dev_module, "regenerate", lambda spec, out, zod_out=None: False)
    proc = _FakeProc(hang_on_wait=True)
    monkeypatch.setattr(dev_module, "_start_server", lambda spec, dirs: proc)

    assert run_dev("trpc_rs_py:router", tmp_path / "out.d.ts", server="srv:app") == 0
    assert proc.terminated
    assert proc.killed
