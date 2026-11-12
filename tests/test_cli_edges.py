"""CLI edge-path tests for `trpc_rs_py.cli`.

Covers the branches `test_dev.py`'s subprocess-driven runs cannot reach:
`_load_router` exit-2 paths, the `dev`/`serve` command wrappers (including
their error mappings), codegen failure modes, `init`'s unknown-template
branch, the `(…)` signature fallback in `routes`, plain `doctor` output,
`_dependency_version`, and the `__main__` module guard.
"""

from __future__ import annotations

import argparse
import io
import json
import runpy
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

import trpc_rs_py.dev as dev_module
import trpc_rs_py.serve as serve_module
from trpc_rs_py.cli import _cmd_init, _dependency_version
from trpc_rs_py.cli import main as cli_main


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


def test_load_router_failure_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing modules, bad specs, and non-Router objects all exit 2."""
    _write_router_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(SystemExit) as excinfo:
        cli_main(["routes", "--app", "missing_mod_xyz:router"])
    assert excinfo.value.code == 2
    assert "cannot load" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        cli_main(["routes", "--app", "builtins:len"])
    assert excinfo.value.code == 2
    assert "not a trpc_rs_py.Router" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        cli_main(["routes", "--app", "no-separator"])
    assert excinfo.value.code == 2
    assert "cannot load" in capsys.readouterr().err


def test_cmd_dev_invokes_run_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `dev` subcommand forwards every flag to `run_dev`."""
    seen: dict[str, Any] = {}

    def fake_run_dev(
        app_spec: str,
        out: Path,
        *,
        watch_dirs: Any,
        server: str | None,
        debounce_ms: int,
        zod_out: Path | None,
    ) -> int:
        seen.update(
            app_spec=app_spec,
            out=out,
            watch_dirs=watch_dirs,
            server=server,
            debounce_ms=debounce_ms,
            zod_out=zod_out,
        )
        return 0

    monkeypatch.setattr(dev_module, "run_dev", fake_run_dev)
    assert (
        cli_main(
            [
                "dev",
                "--app",
                "m:router",
                "--out",
                "o.d.ts",
                "--watch",
                "w1",
                "--server",
                "srv:app",
                "--debounce-ms",
                "25",
                "--zod-out",
                "z.ts",
            ]
        )
        == 0
    )
    assert seen == {
        "app_spec": "m:router",
        "out": Path("o.d.ts"),
        "watch_dirs": ["w1"],
        "server": "srv:app",
        "debounce_ms": 25,
        "zod_out": Path("z.ts"),
    }


def test_cmd_dev_failure_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setup failures from `run_dev` print to stderr and exit 1."""

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("refusing in production")

    monkeypatch.setattr(dev_module, "run_dev", boom)
    assert cli_main(["dev", "--app", "m:router", "--out", "o.d.ts"]) == 1
    assert "refusing in production" in capsys.readouterr().err


def test_cmd_serve_invokes_serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `serve` subcommand loads the router and forwards the options."""
    _write_router_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    seen: dict[str, Any] = {}

    def fake_serve(router: Any, *, host: str, port: int, production: bool) -> int:
        seen.update(host=host, port=port, production=production, router=router)
        return 0

    monkeypatch.setattr(serve_module, "serve", fake_serve)
    assert (
        cli_main(
            [
                "serve",
                "--app",
                "router_mod:router",
                "--host",
                "0.0.0.0",
                "--port",
                "9999",
                "--production",
            ]
        )
        == 0
    )
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9999
    assert seen["production"] is True


def test_cmd_serve_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bind/runtime failures from `serve` print to stderr and exit 1."""
    _write_router_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    def boom(*args: Any, **kwargs: Any) -> int:
        raise OSError("address in use")

    monkeypatch.setattr(serve_module, "serve", boom)
    assert cli_main(["serve", "--app", "router_mod:router"]) == 1
    assert "address in use" in capsys.readouterr().err


def test_cmd_codegen_ts_failure_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TypeError from `generate_types` prints to stderr and exits 1."""
    _write_router_module(
        tmp_path,
        body="""from trpc_rs_py import Router

router = Router()

@router.query
def untyped(x):
    return x
""",
        name="router_mod_bad_ts",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert (
        cli_main(
            [
                "codegen",
                "ts",
                "--app",
                "router_mod_bad_ts:router",
                "--out",
                str(tmp_path / "out.d.ts"),
            ]
        )
        == 1
    )
    assert "trpc-py codegen:" in capsys.readouterr().err


def test_cmd_codegen_zod_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`codegen zod` writes the module; ungeneratable routers exit 1."""
    _write_router_module(tmp_path, name="router_mod_zod")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "nested" / "router.zod.ts"
    with redirect_stdout(io.StringIO()) as buffer:
        assert (
            cli_main(
                [
                    "codegen",
                    "zod",
                    "--app",
                    "router_mod_zod:router",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
    assert f"trpc-py codegen: wrote {out}" in buffer.getvalue()
    assert "export const Q0Input" in out.read_text()

    _write_router_module(
        tmp_path,
        body="""from trpc_rs_py import Router

router = Router()

@router.query
def untyped(x):
    return x
""",
        name="router_mod_bad_zod",
    )
    assert (
        cli_main(
            [
                "codegen",
                "zod",
                "--app",
                "router_mod_bad_zod:router",
                "--out",
                str(tmp_path / "bad.zod.ts"),
            ]
        )
        == 1
    )
    assert "trpc-py codegen:" in capsys.readouterr().err


def test_cmd_codegen_openapi_title_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--title`/`--version` overrides flow into the OpenAPI document."""
    _write_router_module(tmp_path, name="router_mod_openapi")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "openapi.json"
    with redirect_stdout(io.StringIO()):
        assert (
            cli_main(
                [
                    "codegen",
                    "openapi",
                    "--app",
                    "router_mod_openapi:router",
                    "--out",
                    str(out),
                    "--title",
                    "Custom API",
                    "--version",
                    "9.9.9",
                ]
            )
            == 0
        )
    document = json.loads(out.read_text())
    assert document["info"]["title"] == "Custom API"
    assert document["info"]["version"] == "9.9.9"


def test_cmd_init_unknown_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A template name outside the bundled set reports and exits 1."""
    args = argparse.Namespace(name="proj", dir=str(tmp_path / "proj"), template="no-such-template")
    assert _cmd_init(args) == 1
    assert "unknown template" in capsys.readouterr().err


def test_routes_uninspectable_handler_shows_ellipsis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handlers without signatures render `(…)` in the routes table."""
    _write_router_module(
        tmp_path,
        body="""from trpc_rs_py import Router
from trpc_rs_py.router import Procedure, ProcedureKind

router = Router()
router._procedures["opaque"] = Procedure(
    path="opaque", kind=ProcedureKind.QUERY, handler=iter
)
""",
        name="router_mod_opaque",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert cli_main(["routes", "--app", "router_mod_opaque:router"]) == 0
    assert "(…)" in capsys.readouterr().out


def test_doctor_plain_output(capsys: pytest.CaptureFixture[str]) -> None:
    """`doctor` without --json prints aligned key/value rows."""
    assert cli_main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "trpc_rs_py" in out
    assert "python" in out
    assert "platform" in out


def test_dependency_version_missing() -> None:
    """Unknown distributions report `not installed`."""
    assert _dependency_version("no-such-distribution-zzz") == "not installed"


def test_cli_main_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m trpc_rs_py.cli` reaches the __main__ guard and exits 0."""
    monkeypatch.setattr(sys, "argv", ["trpc-py", "doctor", "--json"])
    with warnings.catch_warnings():
        # run_module warns that the module is already in sys.modules.
        warnings.simplefilter("ignore")
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("trpc_rs_py.cli", run_name="__main__")
    assert excinfo.value.code == 0
