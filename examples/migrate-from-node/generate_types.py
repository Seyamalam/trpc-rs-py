"""Regenerate `app-router.d.ts` for this example."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import build_router

from trpc_rs_py import generate_types

Path(__file__).with_name("app-router.d.ts").write_text(generate_types(build_router()))
print("wrote app-router.d.ts")
