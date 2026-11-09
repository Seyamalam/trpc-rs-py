# Demo script: live type sync (`trpc-py dev`)

**The shot:** a Python procedure edit turns into a new typed TypeScript API
in under a second — no codegen step, no restart.

## Setup

```bash
trpc-py init demo && cd demo
```

Open `server.py` and `web/app-router.d.ts` side by side (two panes — the
tape uses a `tail -f` on the d.ts for a single-pane recording).

## Narration beats

1. "This is a FastAPI backend the official `@trpc/client` talks to."
2. Start `trpc-py dev --app server:router --out web/app-router.d.ts`.
   Point out: initial generation, watcher armed.
3. Add a `delete_user` mutation to `server.py`, save.
4. The d.ts updates — `Delete_userProcedure` appears. "The TS client can
   call it immediately, fully typed."
5. Break it: rename a field to something unmappable or introduce a syntax
   error. The last good output stays; the traceback prints. Fix it, save —
   it recovers.
6. (Optional) Mention `dev` refuses to run under `TRPC_RS_PY_PRODUCTION`.

## VHS tape

`live-types.tape` records the single-pane version (init → dev → edit →
d.ts tail shows the new procedure). Adjust `Sleep`s to your typing pace.
