# flask-nextjs

A **Flask** backend serving a Python tRPC router, called by a **Next.js**
frontend through the official `@trpc/client` — end-to-end typed from Python
annotations.

It demonstrates:

- `mount_wsgi(router)` attached to a Flask app with
  `DispatcherMiddleware` at `/trpc`;
- a Next.js App Router **server component** fetching server-to-server, so
  no CORS is involved and nothing tRPC-related ships to the browser;
- the generated `frontend/app-router.d.ts` consumed by
  `createTRPCClient<AppRouter>()` — a rename in Python turns into a `tsc`
  error in `app/page.tsx`.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the backend (terminal 1).
cd examples/flask-nextjs
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)

# 4. Install the frontend dependencies and start Next.js (terminal 2).
pnpm --dir frontend install
pnpm --dir frontend dev
# → http://localhost:3000 (TRPC_URL overrides the backend address)
```

## The frontend

`frontend/app/page.tsx` is an async server component: it creates a typed
`@trpc/client` pointed at the Flask server and awaits `greeting`,
`create_user`, and `user_by_id` while rendering — the Python backend is
called like any Node tRPC server would be.

```ts
const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: process.env.TRPC_URL ?? "http://127.0.0.1:8000/trpc" })],
});

const greeting = await client.greeting.query({ name: "Next.js" });
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
uv run python examples/flask-nextjs/generate_types.py
```

## Smoke test

```bash
./examples/flask-nextjs/smoke.sh
```

The smoke test checks the committed `frontend/app-router.d.ts` for drift,
installs the frontend dependencies if needed and runs `tsc --noEmit`, then
starts the Flask backend on a free port and curl-asserts the `greeting`,
`create_user`, and `user_by_id` calls — including `NOT_FOUND` → HTTP 404.
No secrets, no network beyond localhost (after `pnpm install` has run
once).
