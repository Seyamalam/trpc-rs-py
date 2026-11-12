# Cross-platform wheels

The production-readiness gate (MISSION.md §12) requires wheels that build
**and install** for Linux x86_64/aarch64 (manylinux and musllinux), macOS
x86_64/arm64, and Windows x86_64. `scripts/wheels.sh` builds the whole
matrix with one command and verifies every wheel it can run locally.

```bash
just wheels          # or: ./scripts/wheels.sh
./scripts/wheels.sh --only linux-x86_64,windows-x86_64   # subset
./scripts/wheels.sh --no-verify                         # build only
```

Output lands in `dist/wheels/<target>/` with per-target logs in
`dist/wheels/logs/`. Each target builds in isolation — one failure never
aborts the rest — and the run ends with a summary table. Exit status is
non-zero if any attempted target failed.

## Wheel strategy: one abi3 wheel per platform

All wheels are built with `pyo3/abi3-py310` passed through
`maturin build -F "extension-module pyo3/abi3-py310"`, so a single wheel per
platform covers every supported CPython (3.10–3.14) instead of one wheel per
Python version per platform. The abi3 stable ABI constrains the extension to
CPython's stable-ABI surface; `crates/trpc-py` compiles under it cleanly and
the compiled module passes the full test suite (`just ci` exercises the same
code through `maturin develop`). Wheel tags look like
`cp310-abi3-manylinux_2_17_x86_64`.

No `Cargo.toml` or `pyproject.toml` change is needed for this: maturin
detects the abi3 feature from `-F` and tags the wheel accordingly. If a
future PyO3 API ever falls outside the stable ABI, the build fails loudly —
it cannot silently produce a non-abi3 wheel.

## The matrix

| target | Rust target | toolchain | wheel tag | install-verified |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `aarch64-apple-darwin` | native clang | `macosx_11_0_arm64` | **yes** — scratch venv on the build host |
| `macos-x86_64` | `x86_64-apple-darwin` | native clang `-arch x86_64` | `macosx_10_12_x86_64` | **yes** — uv-managed x86_64 CPython venv under Rosetta |
| `linux-x86_64` | `x86_64-unknown-linux-gnu` | `maturin --zig` (zig cc, glibc 2.17) | `manylinux2014` / `manylinux_2_17_x86_64` | **yes** — `docker run --platform linux/amd64 python:3.14-slim` |
| `linux-x86_64-musl` | `x86_64-unknown-linux-musl` | `maturin --zig` (zig cc, musl) | `musllinux_1_2_x86_64` | **yes** — `docker run --platform linux/amd64 python:3.14-alpine` |
| `linux-aarch64` | `aarch64-unknown-linux-gnu` | `maturin --zig` (zig cc, glibc 2.17) | `manylinux2014` / `manylinux_2_17_aarch64` | **yes** — `docker run --platform linux/arm64 python:3.14-slim` |
| `linux-aarch64-musl` | `aarch64-unknown-linux-musl` | `maturin --zig` (zig cc, musl) | `musllinux_1_2_aarch64` | **yes** — `docker run --platform linux/arm64 python:3.14-alpine` |
| `windows-x86_64` | `x86_64-pc-windows-msvc` | maturin's bundled cargo-xwin (`clang-cl`/`lld-link`, MSVC SDK auto-downloaded) | `win_amd64` | **build-only** — no Windows runner locally; the wheel is structure-verified to contain a PE32+ x86-64 `_core.pyd` |

`linux-aarch64-musl` is not required by the gate but is included: it costs
one extra zig link and broadens Alpine-on-ARM coverage (Apple Silicon VMs,
AWS Graviton).

"Install-verified" means `pip install <wheel>` plus a smoke that imports
`trpc_rs_py` and `trpc_rs_py._core`, checks their versions match, and runs
`parse_request`/`build_response` through the compiled module. Resolving
`pydantic` from the index during that install also proves our runtime
dependency resolves on the target libc and arch (including
`pydantic-core`'s musllinux wheels on Alpine).

## Toolchain notes and prerequisites

* **Linux targets use `maturin --zig`.** zig ships a self-contained C
  toolchain with versioned glibc/musl targets, so cross-compiling needs no
  Docker build stage and no qemu. All dependencies (`pyo3`, `tokio`,
  `hyper`, `serde_json`, …) are pure Rust; zig is only the linker, invoked
  through the shims maturin generates. musl builds print a harmless
  `ToolNotFound: x86_64-linux-musl-gcc` build-script probe warning — the
  wheel is still produced and verified.
* **glibc floor is 2.17 (`manylinux2014`).** zig emits the oldest glibc
  symbol versions the code needs, and auditwheel inside maturin confirms
  the tag. That covers every glibc distribution since ~2014 (RHEL 7,
  Debian 8, Ubuntu 14.04+); a `manylinux_2_28` image would buy nothing
  here.
* **Windows uses maturin's integrated cargo-xwin.** For an
  `x86_64-pc-windows-msvc` target, maturin 1.16 automatically downloads the
  MSVC CRT/SDK headers and import libraries (cached in
  `~/Library/Caches/cargo-xwin`, ~1 GB, one time) and links with
  `clang-cl` + `lld-link` — no Windows machine, VM, or Wine needed. PyO3
  abi3 links against `python3.dll`, which the SDK download provides.
* Required tools: Rust 1.96 toolchain with the targets above
  (`rustup target add …`), `zig` (only for the Linux targets), `docker`
  running (only for Linux install verification), `uv`, and — for the
  macOS x86_64 verify — an x86_64 CPython
  (`uv python install cpython-3.14-macos-x86_64-none`) plus Rosetta.
* The scratch venvs used for macOS verification are left under
  `dist/wheels/venv-*`; `dist/` is gitignored and disposable.

## Honest limitations

* **Windows is build-verified only.** The wheel is a genuine
  MSVC-compatible PE32+ x86-64 DLL, but nothing executes it locally — there
  is no Windows runner, and Wine is intentionally not part of the loop.
  Before tagging a release, spot-check the wheel on a real Windows box
  (`pip install trpc_rs_py-…-win_amd64.whl`, then
  `python -c "import trpc_rs_py._core as c; print(c.__version__)"`) — for
  example a Windows VM, or the user's own machine. `trpc-py serve` also
  deserves a manual smoke there since it exercises the embedded hyper
  server.
* **macOS x86_64 is verified under Rosetta**, not on bare-metal Intel
  hardware. The wheel's x86-64 slice is what ships; Rosetta runs exactly
  that code, but real-hardware quirks (none expected — the code has no
  arch-specific paths) are only fully ruled out on an Intel Mac.
* **The matrix covers CPython only** — the project declares CPython in its
  classifiers and PyO3 does not support PyPy stable-ABI wheels. The abi3
  floor is Python 3.10 per ADR 0001; the wheels will not install on 3.9.

## Release instructions

Wheels are built locally (no hosted CI) immediately before tagging:

```bash
just wheels                       # matrix + verification into dist/wheels/
twine check dist/wheels/*/*.whl   # optional metadata check
uv run maturin upload dist/wheels/*/*.whl   # publishes to PyPI
```

`scripts/release.sh` still builds the native wheel for the release tag;
`just wheels` is the cross-platform companion that produces the full set to
attach to the GitHub release / upload alongside it. Wire `just wheels` into
`release-check` when the matrix has been exercised on a second machine; it
is deliberately a separate recipe for now so a flaky cross toolchain cannot
block the local gate.
