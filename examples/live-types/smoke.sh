#!/usr/bin/env bash
# Smoke test for examples/live-types: copies the example app to a temp
# module, runs `trpc-py dev` against it, edits the module, and asserts the
# generated .d.ts regenerates with the new procedure.
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(cd ../.. && pwd)"

uv run --project "$ROOT" python - "$PWD/app-router.d.ts" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app import build_router

from trpc_rs_py import generate_types

committed = Path(sys.argv[1]).read_text()
if committed != generate_types(build_router()):
    print(
        "smoke: app-router.d.ts is stale; run "
        "`uv run python examples/live-types/generate_types.py`",
        file=sys.stderr,
    )
    sys.exit(1)
PY

uv run --project "$ROOT" python - <<'PY'
"""End-to-end dev-loop check on a temp copy of the example app."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

source = Path("app.py").read_text()

with tempfile.TemporaryDirectory() as tmp:
    module = Path(tmp) / "live_mod.py"
    module.write_text(source)
    out = Path(tmp) / "router.d.ts"

    env = dict(os.environ, PYTHONPATH=tmp + os.pathsep + os.environ.get("PYTHONPATH", ""))
    process = subprocess.Popen(
        [sys.executable, "-m", "trpc_rs_py.cli", "dev",
         "--app", "live_mod:router", "--out", str(out), "--debounce-ms", "50"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while not out.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert out.exists(), "initial codegen never wrote router.d.ts"
        assert "Add_noteProcedure" in out.read_text(), "initial output missing procedure"

        # Edit the module: add a procedure, then wait for regeneration.
        with module.open("a") as file:
            file.write(
                "\n\n@router.mutation\ndef delete_note(note_id: int) -> bool:\n"
                '    """Remove a note."""\n    return True\n'
            )
        deadline = time.monotonic() + 15
        while "Delete_noteProcedure" not in out.read_text() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert "Delete_noteProcedure" in out.read_text(), "dev did not regenerate on save"
        print("smoke: ok  - trpc-py dev regenerated .d.ts on save")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
PY

echo "smoke: live-types passed"
