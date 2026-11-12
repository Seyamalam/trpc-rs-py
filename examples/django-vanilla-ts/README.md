# django-vanilla-ts

A **Django** backend serving a Python tRPC router, called by a **vanilla
TypeScript** frontend (Vite, no framework) through the official
`@trpc/client` — end-to-end typed from Python annotations.

It demonstrates:

- `trpc_urlpatterns(router)` serving tRPC under `/trpc` inside Django,
  with settings configured inline so the example stays one file;
- a browser frontend using `createTRPCClient<AppRouter>()` directly — no
  React, no query library — so the smallest possible consumer is visible;
- the Vite dev server proxying `/trpc` to the Django server, making calls
  same-origin (no CORS) exactly as a production deploy behind one origin
  would behave;
- the generated `frontend/app-router.d.ts` giving `.query`/`.mutate` exact
  input and output types — a rename in Python turns into a `tsc` error.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the backend (terminal 1).
cd examples/django-vanilla-ts
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)

# 4. Install the frontend dependencies and start Vite (terminal 2).
pnpm --dir frontend install
pnpm --dir frontend dev
# → http://localhost:5173 (vite.config.ts proxies /trpc to :8000)
```

## The frontend

`frontend/src/main.ts` builds the typed client once; every procedure call
is checked against the generated `AppRouter`:

```ts
const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "/trpc" })], // same-origin via the Vite proxy
});

const greeting = await client.greeting.query({ name: "Django" });
const user = await client.create_user.mutate({ user: { name: "Ada", age: 36 } });
```

Typecheck it against the Python router:

```bash
pnpm --dir frontend exec tsc --noEmit
```

## Generated types

`frontend/app-router.d.ts` is generated from the Python router and
committed; it lives inside `frontend/` so its `@trpc/server` imports
resolve against the frontend's `node_modules`. Regenerate after editing
`app.py`:

```bash
uv run python examples/django-vanilla-ts/generate_types.py
```

## Smoke test

```bash
./examples/django-vanilla-ts/smoke.sh
```

The smoke test checks the committed `frontend/app-router.d.ts` for drift,
installs the frontend dependencies if needed and runs `tsc --noEmit`, then
starts the Django backend on a free port and curl-asserts the `greeting`,
`create_user`, and `user_by_id` calls — including `NOT_FOUND` → HTTP 404.
No secrets, no network beyond localhost (after `pnpm install` has run
once).
