# CLI reference

The `trpc-py` command ships inside the wheel (`[project.scripts]`). Command
names, flags, exit codes, and `--json` output are the stable contract
(ADR 0006).

## `trpc-py dev`

Live type sync: watch Python sources and regenerate the `.d.ts` on every
save.

```bash
trpc-py dev --app server:router --out web/router.d.ts
```

- `--app MODULE:ATTR` — the router import string (`pkg.server:router`, or
  `pkg.server:build_router()` for a factory). Loaded in a fresh subprocess
  on each save, so no stale module state.
- `--out FILE` — written atomically and only when the content changed, so
  the TypeScript language server is not reloaded needlessly.
- `--watch DIR` — extra directories to watch (repeatable); the app's own
  module directory is always included.
- `--server MODULE:APP` — also start the ASGI app with reload via `uvicorn`,
  watching the same directories.
- `--debounce-ms N` — quiet window before a save burst regenerates
  (default 100 ms; coalescing is capped at 2 s).

Import and type errors print the traceback and keep the last good output.
`dev` refuses to run when `TRPC_RS_PY_PRODUCTION` is set.

## `trpc-py init`

Scaffold a starter project from a bundled template:

```bash
trpc-py init my-app            # creates ./my-app
trpc-py init my-app --dir app  # or an explicit directory
```

`nextjs-fastapi` (the default and only template today) lays down a FastAPI
server with tRPC + REST mounts and a minimal Next.js app wired to the
official `@trpc/client` — see the generated `README.md` for run steps. The
target directory must be empty.

## `trpc-py codegen ts`

One-off declaration generation, same `--app`/`--out` contract:

```bash
trpc-py codegen ts --app server:router --out web/router.d.ts
```

## `trpc-py codegen openapi`

Emit the OpenAPI 3.1 document for `RestRoute`-annotated procedures:

```bash
trpc-py codegen openapi --app server:router --out openapi.json \
    --title "Blog API" --version 1.0.0
```

See [REST + OpenAPI](rest.md) for the `RestRoute` opt-in and `mount_rest`.

## `trpc-py routes`

List every procedure with its kind and signature; `--json` emits
`[{path, kind, handler}]`.

## `trpc-py check`

Validate that the router generates cleanly — untyped parameters,
unmappable annotations, and invalid subscription signatures each report the
procedure at fault. Exit 0 when clean, 1 otherwise.

## `trpc-py doctor`

Print versions and environment info for bug reports; `--json` for machine
consumption.
