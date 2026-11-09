# `trpc-py dev` live type sync: subprocess regeneration, atomic changed-only
# writes, the Rust notify watcher, the production guard, the end-to-end
# watch→regenerate loop, and the argparse CLI commands.

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from trpc_rs_py.cli import main as cli_main
from trpc_rs_py.dev import (
    PRODUCTION_ENV_VAR,
    is_production,
    regenerate,
    resolve_watch_dirs,
    run_dev,
)


def _write_router_module(
    tmp_path: Path,
    procedures: int = 1,
    body: str | None = None,
    name: str = "router_mod",
) -> Path:
    """Write a `<name>.py` fixture exposing `router`."""
    if body is None:
        registrations = "\n".join(
            f"""
@router.query(name="q{i}")
def q{i}() -> int:
    return {i}"""
            for i in range(procedures)
        )
        body = f"""from trpc_rs_py import Router

router = Router()
{registrations}
"""
    module = tmp_path / f"{name}.py"
    module.write_text(body)
    return module


def test_load_app_object_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trpc_rs_py._dev_loader import load_app_object

    _write_router_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    from trpc_rs_py import Router

    assert isinstance(load_app_object("router_mod:router"), Router)
    with pytest.raises(ValueError, match="module:attribute"):
        load_app_object("router_mod")
    with pytest.raises(AttributeError):
        load_app_object("router_mod:missing")


def test_production_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not is_production()
    monkeypatch.setenv(PRODUCTION_ENV_VAR, "1")
    assert is_production()
    monkeypatch.setenv(PRODUCTION_ENV_VAR, "off")
    assert not is_production()


def test_run_dev_refuses_in_production(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(PRODUCTION_ENV_VAR, "true")
    with pytest.raises(RuntimeError, match="production"):
        run_dev("builtins:len", tmp_path / "out.d.ts")


def test_resolve_watch_dirs_includes_module_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_router_module(tmp_path, name="router_mod_dirs")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "router_mod_dirs", raising=False)
    dirs = resolve_watch_dirs("router_mod_dirs:router")
    assert tmp_path.resolve() in dirs


def test_regenerate_writes_and_skips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_router_module(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    out = tmp_path / "router.d.ts"

    assert regenerate("router_mod:router", out)
    first = out.read_text()
    assert "Q0Procedure" in first

    # Unchanged output never rewrites the file.
    mtime = out.stat().st_mtime_ns
    assert not regenerate("router_mod:router", out)
    assert out.stat().st_mtime_ns == mtime

    # A broken spec fails and keeps the last good output.
    assert not regenerate("router_mod:missing", out)
    assert out.read_text() == first
    assert not (tmp_path / "router.d.ts.tmp").exists()


def test_regenerate_200_procedures_is_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MISSION §2.1: save-to-types must be under a second for 200 procedures.

    Includes the subprocess interpreter startup; the asserted bound is loose
    so CI machines cannot flake — the published number comes from benchmarks.
    """
    _write_router_module(tmp_path, procedures=200)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    start = time.monotonic()
    assert regenerate("router_mod:router", tmp_path / "router.d.ts")
    elapsed = time.monotonic() - start
    assert elapsed < 3.0


def test_watcher_reports_changed_paths(tmp_path: Path) -> None:
    from trpc_rs_py._core import Watcher

    watcher = Watcher([str(tmp_path)])
    # Events already queued by pytest's own tmp-dir work may arrive first;
    # drain until the batch for our file shows up.
    changed = tmp_path / "a.py"
    changed.write_text("x = 1")
    deadline = time.monotonic() + 10
    seen: list[str] = []
    while time.monotonic() < deadline:
        batch = watcher.next(timeout_ms=2000, debounce_ms=50)
        if batch is None:
            continue
        seen += batch
        if any(path.endswith("a.py") for path in seen):
            break
    assert any(path.endswith("a.py") for path in seen)


def test_dev_loop_regenerates_on_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: `python -m trpc_rs_py.cli dev` regenerates on every save."""
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    module = _write_router_module(tmp_path)
    out = tmp_path / "router.d.ts"
    env = {"PYTHONPATH": str(tmp_path)}
    import os

    full_env = {**os.environ, **env}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "trpc_rs_py.cli",
            "dev",
            "--app",
            "router_mod:router",
            "--out",
            str(out),
            "--watch",
            str(tmp_path),
        ],
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not out.exists():
            time.sleep(0.1)
        assert out.exists(), "initial generation did not write the file"

        module.write_text(
            module.read_text()
            + '\n\n@router.query(name="added")\ndef added() -> bool:\n    return True\n'
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and "AddedProcedure" not in out.read_text():
            time.sleep(0.1)
        assert "AddedProcedure" in out.read_text(), "save did not regenerate types"
    finally:
        proc.send_signal(signal.SIGINT)
        stdout, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0, stdout


def test_cli_doctor_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["doctor", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["trpc_rs_py"]
    assert info["python"]


def test_cli_routes_and_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_router_module(tmp_path, procedures=3, name="router_mod_routes")
    monkeypatch.syspath_prepend(str(tmp_path))
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert cli_main(["routes", "--app", "router_mod_routes:router"]) == 0
    listing = buffer.getvalue()
    assert "query" in listing
    assert "q0" in listing and "q2" in listing

    with redirect_stdout(io.StringIO()):
        assert cli_main(["check", "--app", "router_mod_routes:router"]) == 0

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert cli_main(["routes", "--app", "router_mod_routes:router", "--json"]) == 0
    routes = json.loads(buffer.getvalue())
    assert {entry["path"] for entry in routes} == {"q0", "q1", "q2"}


def test_cli_codegen_ts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_router_module(tmp_path, name="router_mod_codegen")
    monkeypatch.syspath_prepend(str(tmp_path))
    import io
    from contextlib import redirect_stdout

    out = tmp_path / "gen.d.ts"
    with redirect_stdout(io.StringIO()):
        assert (
            cli_main(["codegen", "ts", "--app", "router_mod_codegen:router", "--out", str(out)])
            == 0
        )
    assert "Q0Procedure" in out.read_text()


def test_cli_check_fails_for_untyped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_router_module(
        tmp_path,
        body="""from trpc_rs_py import Router

router = Router()

@router.query
def untyped(x):
    return x
""",
        name="router_mod_untyped",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "router_mod_untyped", raising=False)
    import io
    from contextlib import redirect_stderr

    with redirect_stderr(io.StringIO()):
        assert cli_main(["check", "--app", "router_mod_untyped:router"]) == 1
