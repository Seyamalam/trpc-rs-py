"""Typing tests: procedure and public API types, checked by mypy and pyright.

Proves the public exports, including the codegen entry point. Extended in
milestone 0.4.0 with procedure type inference tests.
"""

from __future__ import annotations

from typing_extensions import assert_type

import trpc_rs_py


def test_version_is_str() -> None:
    assert_type(trpc_rs_py.__version__, str)


def test_generate_types_returns_str() -> None:
    router = trpc_rs_py.Router()
    assert_type(trpc_rs_py.generate_types(router), str)
