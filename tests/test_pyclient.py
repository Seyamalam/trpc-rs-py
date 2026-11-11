"""Tests for `trpc_rs_py.pyclient` — Python `AppRouterClient` stub codegen.

Generation tests are pure (source text assertions); delegation tests `exec`
the emitted module against a recording fake client, and one integration
test drives the generated stub over a real uvicorn socket so the stub
proves itself end-to-end on the wire.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from pydantic import BaseModel

from trpc_rs_py import Router, mount, tracked
from trpc_rs_py.client import SubscriptionEvent, create_client
from trpc_rs_py.pyclient import generate_python_client
from trpc_rs_py.router import Procedure, ProcedureKind


class Post(BaseModel):
    """Model used to prove annotations round-trip as importable names."""

    title: str


def _build_router() -> Router:
    router = Router()

    @router.query
    def ping() -> str:
        return "pong"

    @router.query(name="posts.by_id")
    def by_id(ctx, post_id: int) -> Post:
        return Post(title=f"post {post_id}")

    @router.mutation
    def create(title: str, priority: int = 0) -> dict[str, Any]:
        return {"title": title, "priority": priority}

    @router.subscription
    def ticks(count: int = 2, lastEventId: str | None = None) -> AsyncIterator[dict[str, Any]]:
        async def stream() -> AsyncIterator[dict[str, Any]]:
            for index in range(count):
                yield tracked(f"t-{index}", {"index": index})

        return stream()

    @router.query
    def loose(value):  # intentionally unannotated
        return value

    return router


def _exec_stub(source: str) -> Any:
    """Execute generated source and return the AppRouterClient class."""
    namespace: dict[str, Any] = {}
    exec(compile(source, "<generated>", "exec"), namespace)
    return namespace["AppRouterClient"]


class _FakeClient:
    """Record delegations the way `trpc_rs_py.client.Client` receives them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def query(self, path: str, input: Any = None) -> Any:
        self.calls.append(("query", path, input))
        return {"ok": True}

    def mutation(self, path: str, input: Any = None) -> Any:
        self.calls.append(("mutation", path, input))
        return {"ok": True}

    def subscribe(self, path: str, input: Any = None, *, last_event_id: str | None = None) -> Any:
        self.calls.append(("subscribe", path, input))
        return iter(())


def test_header_class_and_methods() -> None:
    """Source carries the do-not-edit header, the class, and every method."""
    source = generate_python_client(_build_router())
    assert "Do not edit" in source
    assert "class AppRouterClient:" in source
    assert "def __init__(self, client: Client) -> None:" in source
    assert "def ping(self) -> str:" in source
    assert "def create(self, title: str, priority: int = 0)" in source


def test_ctx_and_last_event_id_excluded() -> None:
    """Injected ctx and resume params never appear in stub signatures."""
    source = generate_python_client(_build_router())
    assert "def posts_by_id(self, post_id: int)" in source
    assert "def ticks(self, count: int = 2) -> Iterator[SubscriptionEvent]:" in source
    assert "ctx" not in source.split("def posts_by_id")[1].split("\n")[0]


def test_dotted_path_and_return_model() -> None:
    """`posts.by_id` becomes `posts_by_id`; model annotations import their module."""
    source = generate_python_client(_build_router())
    assert "def posts_by_id(self, post_id: int) -> test_pyclient.Post:" in source
    assert "import test_pyclient" in source


def test_unannotated_handler_gets_untyped_params() -> None:
    """A handler without annotations still emits a callable stub."""
    source = generate_python_client(_build_router())
    assert "def loose(self, value)" in source
    assert "def loose(self, value):" in source


def test_deterministic_output() -> None:
    """Two runs over the same router produce identical source."""
    assert generate_python_client(_build_router()) == generate_python_client(_build_router())


def test_keyword_and_reserved_paths_renamed() -> None:
    """Keywords/dunder-shaped/internal names can never break the class."""
    router = Router()

    @router.query(name="class")
    def kw() -> str:
        return "x"

    @router.query(name="__eq__")
    def dunder() -> str:
        return "x"

    @router.query(name="_client")
    def internal() -> str:
        return "x"

    source = generate_python_client(router)
    assert "def class_(self)" in source
    assert "def call___eq__(self)" in source
    assert "def call__client(self)" in source


def test_delegation_packs_input_object() -> None:
    """Stub methods pack params into the wire input and delegate."""
    stub = _exec_stub(generate_python_client(_build_router()))
    fake = _FakeClient()
    client = stub(fake)
    client.posts_by_id(7)
    client.create("hi", priority=2)
    client.ping()
    client.ticks(4)
    assert fake.calls == [
        ("query", "posts.by_id", {"post_id": 7}),
        ("mutation", "create", {"title": "hi", "priority": 2}),
        ("query", "ping", None),
        ("subscribe", "ticks", {"count": 4}),
    ]


def test_mixed_star_args_raises_with_path() -> None:
    """`*args` mixed with named params has no wire shape — TypeError names it."""
    router = Router()

    @router.query
    def bad(a: int, *rest: int) -> int:
        return a

    with pytest.raises(TypeError, match="bad"):
        generate_python_client(router)


def test_star_args_only_packs_list() -> None:
    """A `*args`-only handler sends the varargs as the input list."""
    router = Router()

    @router.query
    def collect(*values: int) -> int:
        return len(values)

    source = generate_python_client(router)
    assert "def collect(self, *values)" in source
    assert 'self._client.query("collect", list(values))' in source


def test_kwargs_only_passes_dict() -> None:
    """A `**kwargs`-only handler forwards the captured dict as input."""
    router = Router()

    @router.mutation
    def anything(**fields: Any) -> dict[str, Any]:
        return fields

    source = generate_python_client(router)
    assert "def anything(self, **fields)" in source
    assert 'self._client.mutation("anything", fields)' in source


def test_uninspectable_handler_raises_with_path() -> None:
    """Handlers without an inspectable signature refuse with procedure context."""
    router = Router()
    router._procedures["opaque"] = Procedure(  # pyright: ignore[reportPrivateUsage]
        path="opaque", kind=ProcedureKind.QUERY, handler=iter
    )
    with pytest.raises(TypeError, match="opaque"):
        generate_python_client(router)


def test_unrenderable_default_raises_with_path() -> None:
    """Defaults that cannot be written as source refuse with context."""

    def unrenderable() -> object:
        return object()

    router = Router()

    @router.query
    def bad_default(token: Any = unrenderable()) -> Any:  # noqa: B008 - testing a bad default
        return token

    with pytest.raises(TypeError, match="bad_default"):
        generate_python_client(router)


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    """Serve the same router under uvicorn for the end-to-end stub test."""
    app = mount(_build_router())
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn server did not start"
            time.sleep(0.02)
        socket = server.servers[0].sockets[0]
        port = socket.getsockname()[1]
        yield f"http://127.0.0.1:{port}/trpc"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


def test_generated_stub_runs_over_real_socket(server_url: str, tmp_path) -> None:
    """Write → import → call the stub against the live router."""
    source = generate_python_client(_build_router())
    stub_path = tmp_path / "generated_client.py"
    stub_path.write_text(source)

    namespace: dict[str, Any] = {}
    exec(compile(source, str(stub_path), "exec"), namespace)
    stub = namespace["AppRouterClient"]

    with create_client(server_url) as base:
        client = stub(base)
        assert client.ping() == "pong"
        assert client.create("stub", priority=3) == {"title": "stub", "priority": 3}
        events = list(client.ticks(2))
        assert events == [
            SubscriptionEvent({"index": 0}, id="t-0"),
            SubscriptionEvent({"index": 1}, id="t-1"),
        ]


_CLI_APP = """\
from trpc_rs_py import Router

router = Router()


@router.query
def ping() -> str:
    return "pong"
"""

_CLI_APP_BAD = """\
from trpc_rs_py import Router

router = Router()


@router.query
def bad(a: int, *rest: int) -> int:
    return a
"""


def test_cli_codegen_py_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`trpc-py codegen py-client` writes an importable stub module."""
    import io
    from contextlib import redirect_stdout

    from trpc_rs_py.cli import main as cli_main

    (tmp_path / "cli_app_mod.py").write_text(_CLI_APP)
    monkeypatch.syspath_prepend(str(tmp_path))
    out = tmp_path / "generated" / "stub.py"
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert (
            cli_main(["codegen", "py-client", "--app", "cli_app_mod:router", "--out", str(out)])
            == 0
        )
    assert f"trpc-py codegen: wrote {out}" in buffer.getvalue()
    namespace: dict[str, Any] = {}
    exec(compile(out.read_text(), str(out), "exec"), namespace)
    assert "AppRouterClient" in namespace


def test_cli_codegen_py_client_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TypeError from generation prints to stderr and exits 1."""
    from trpc_rs_py.cli import main as cli_main

    (tmp_path / "cli_bad_mod.py").write_text(_CLI_APP_BAD)
    monkeypatch.syspath_prepend(str(tmp_path))
    assert (
        cli_main(
            [
                "codegen",
                "py-client",
                "--app",
                "cli_bad_mod:router",
                "--out",
                str(tmp_path / "stub.py"),
            ]
        )
        == 1
    )
    assert "bad" in capsys.readouterr().err
