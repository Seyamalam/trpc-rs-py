"""Smoke tests for the built extension module."""

from __future__ import annotations

import trpc_rs_py
import trpc_rs_py._core


def test_version_is_a_string() -> None:
    assert isinstance(trpc_rs_py.__version__, str)
    assert trpc_rs_py.__version__


def test_core_module_exposes_version() -> None:
    assert isinstance(trpc_rs_py._core.__version__, str)
    assert trpc_rs_py._core.__version__
