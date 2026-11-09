# ADR 0006: CLI implementation language

Status: Accepted
Date: 2026-11-09

## Context

MISSION.md section 8 specifies the `trpc-py` CLI implemented in Rust with
`clap`, exposed as a console script through the extension module. Milestone
0.3.0 needs `trpc-py dev` (live type sync) and `trpc-py init` before the full
CLI milestone (0.6.0). The commands that matter now orchestrate Python: they
import the user's router module, call `generate_types` (a Python function),
spawn a reload subprocess, and optionally start the user's ASGI server.

## Options

1. **Build the Rust `clap` CLI now** with `dev`/`init`/`codegen` implemented
   in Rust. Honors MISSION.md literally, but pushes Python-interpreter
   orchestration (imports, subprocesses, virtualenv discovery) into Rust for
   no performance or safety gain — the hot parts (file watching) already live
   in the core.
2. **Python `argparse` CLI now, migrate to `clap` at 0.6.0** — the command
   names and flags are the user-facing contract; the implementation language
   is internal. The file watcher is still Rust (`notify` via PyO3), so the
   only performance-sensitive piece stays where MISSION.md wants it.
3. **Defer the CLI entirely to 0.6.0** — but `dev` and `init` are launch
   differentiators; deferring them defers the launch.

## Decision

Option 2. Ship `trpc-py` as a Python console script
(`[project.scripts] trpc-py = "trpc_rs_py.cli:main"`) covering `dev`, `init`,
`codegen ts`, `routes`, `check`, and `doctor`. Re-evaluate the `clap` port at
0.6.0 when the remaining commands (`serve`, playground, middleware tooling)
land; the CLI surface will be preserved either way.

## Consequences

- No `clap` dependency and no Python-interpreter embedding logic in Rust.
- The file watcher is implemented in `crates/trpc-core` (`notify`) and exposed
  through the extension, matching MISSION.md's intent (watching is the
  performance-relevant part).
- The eventual Rust port, if still wanted at 0.6.0, replaces the internals
  behind an unchanged `trpc-py` command surface.
