# react-zod-form

A **FastAPI** backend whose Pydantic-validated `register` mutation drives a
**Vite + React** form that validates client-side with a **generated Zod
schema** — one source of truth for the constraints, enforced in both
places.

It demonstrates:

- `RegisterInput(BaseModel)` with `StringConstraints` (min/max length,
  regex) and `Field(ge=, le=)` — validated server-side by Pydantic;
- `generate_zod(router)` emitting `frontend/app-schemas.ts`, so the form
  calls `RegisterInput.safeParse(...)` with the *same* constraints before
  submitting — field errors render without a round trip;
- the official `@trpc/react-query` client (`createTRPCReact<AppRouter>()`)
  for the `register` mutation and `registrations` query, typed by
  `frontend/app-router.d.ts`;
- defense in depth: `parsed.data` is typed to match the mutation input
  exactly, and the server still rejects what a bypassed form would send
  (HTTP 400 `BAD_REQUEST`).

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the backend (terminal 1).
cd examples/react-zod-form
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)

# 4. Install the frontend dependencies and start Vite (terminal 2).
pnpm --dir frontend install
pnpm --dir frontend dev
# → http://localhost:5173
```

Try the form: `A!` as a username fails client-side via the Zod schema
(`min 3 chars, ^[a-z0-9_]+$`); the same payload sent straight to the server
fails identically — that parity is the point of the generated schema.

## Generated types and schemas

Two artifacts are generated from the Python router and committed inside
`frontend/` (so their `zod` / `@trpc/server` imports resolve against the
frontend's `node_modules`):

- `frontend/app-router.d.ts` — procedure types for the tRPC client;
- `frontend/app-schemas.ts` — Zod schemas for the Pydantic input models.

Regenerate after editing `app.py`:

```bash
uv run python examples/react-zod-form/generate_types.py
```

Constraints Pydantic can't express in Zod are emitted with a `// dropped:`
marker; see `docs/codegen.md` for the mapping table.

## Smoke test

```bash
./examples/react-zod-form/smoke.sh
```

The smoke test checks both committed generated files for drift, installs
the frontend dependencies if needed and runs `tsc --noEmit`, then starts
the backend on a free port and curl-asserts: an empty `registrations`
list, a valid `register` mutation, an invalid form → HTTP 400
`BAD_REQUEST`, and `registrations` returning the new entry. No secrets, no
network beyond localhost (after `pnpm install` has run once).
