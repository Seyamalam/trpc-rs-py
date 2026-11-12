# fastapi-react-vite

The canonical full-stack example: a **FastAPI** backend serving a Python
tRPC router, called by a **Vite + React** frontend through the official
`@trpc/react-query` client — end-to-end typed from Python annotations.

It demonstrates:

- `mount(router)` under FastAPI at `/trpc`, wrapped with `cors(...)` so the
  Vite dev origin (`http://localhost:5173`) can call it cross-origin;
- Pydantic input/output validation from the same annotations that generate
  the TypeScript types;
- the generated `frontend/app-router.d.ts` consumed by
  `createTRPCReact<AppRouter>()`, so `trpc.greeting.useQuery({ name })`
  infers `{ name: string }` in and `string` out — a rename in Python turns
  into a `tsc` error;
- `httpBatchLink` on the frontend, so `greeting` + `user_by_id` render in
  one batched request.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the backend (terminal 1).
cd examples/fastapi-react-vite
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)

# 4. Install the frontend dependencies and start Vite (terminal 2).
pnpm --dir frontend install
pnpm --dir frontend dev
# → http://localhost:5173 (VITE_TRPC_URL overrides the backend address)
```

## The frontend

`frontend/src/trpc.ts` binds the generated types to hooks:

```ts
import { createTRPCReact } from "@trpc/react-query";
import type { AppRouter } from "../app-router";

export const trpc = createTRPCReact<AppRouter>();
```

Components call procedures as typed hooks:

```tsx
const greeting = trpc.greeting.useQuery({ name });
const createUser = trpc.create_user.useMutation();
createUser.mutate({ user: { name: "Ada", age: 36 } });
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
uv run python examples/fastapi-react-vite/generate_types.py
```

## Smoke test

```bash
./examples/fastapi-react-vite/smoke.sh
```

The smoke test checks the committed `frontend/app-router.d.ts` for drift,
installs the frontend dependencies if needed and runs `tsc --noEmit`, then
starts the backend on a free port and curl-asserts the `greeting`,
`create_user`, and `user_by_id` calls — including `NOT_FOUND` → HTTP 404
and a CORS preflight from the Vite origin. No secrets, no network beyond
localhost (after `pnpm install` has run once).
