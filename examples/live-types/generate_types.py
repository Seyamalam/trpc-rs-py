"""Regenerate app-router.d.ts for this example.

Run: uv run python examples/live-types/generate_types.py
"""

from app import build_router

from trpc_rs_py import generate_types

if __name__ == "__main__":
    output = generate_types(build_router())
    with open("app-router.d.ts", "w") as file:
        file.write(output)
    print("wrote app-router.d.ts")
