# live-types

The headline feature: TypeScript types that update live as you edit Python.

```bash
trpc-py dev --app app:router --out app-router.d.ts \
    --server "uvicorn app:app --reload"
```

`dev` watches the module's directory with the Rust `notify` watcher, reloads
the router in a fresh subprocess on each debounced save, and writes
`app-router.d.ts` atomically — only when the contents actually changed.
Import errors print the traceback and keep the last good output, so a broken
edit never breaks your frontend's types.

Try it: add a `delete_note` mutation in `app.py`, save, and watch the new
`Delete_noteProcedure` appear in `app-router.d.ts` — your `@trpc/client`
calls type-check against it immediately.

`dev` refuses to run when `TRPC_RS_PY_PRODUCTION` is set.

## Smoke test

```bash
./smoke.sh
```
