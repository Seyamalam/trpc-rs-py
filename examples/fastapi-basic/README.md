# fastapi-basic

A minimal FastAPI app serving a tRPC router from Python, callable by the
official `@trpc/client` with full end-to-end types.

It demonstrates:

- a **query** (`user_by_id`) and a **mutation** (`create_user`) defined as
  plain Python functions on a `trpc_rs_py.Router`;
- **Pydantic v2 validation** of inputs and outputs straight from type hints;
- **error handling** with `TRPCError` (`NOT_FOUND` maps to HTTP 404 and the
  official tRPC error envelope);
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/fastapi-basic
uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

## Call it with the official tRPC client

In any TypeScript project with `@trpc/client` v11 installed (the repository's
`clients/ts-tests` project already has it pinned):

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

// Fully typed: input `{ user: { name: string; age: number } }`,
// output `{ id: number; name: string; age: number }`.
const user = await client.create_user.mutate({ user: { name: "Ada", age: 36 } });

// Fully typed; unknown ids reject with the NOT_FOUND error shape.
const found = await client.user_by_id.query({ user_id: user.id });
```

The generated `app-router.d.ts` updates live as you edit the Python router
once `trpc-py dev` (milestone 0.3.0) lands; today, regenerate it with:

```bash
uv run python examples/fastapi-basic/generate_types.py
```

## Type-check the generated types

```bash
pnpm --dir clients/ts-tests install --frozen-lockfile
pnpm --dir clients/ts-tests typecheck
```

`clients/ts-tests` contains the committed runtime compatibility suite that
calls this kind of server with the official `@trpc/client` over real HTTP
(`pnpm --dir clients/ts-tests test`).

## Smoke test

```bash
./examples/fastapi-basic/smoke.sh
```

The smoke test starts the app with uvicorn on a free port, calls the mutation,
a query, a two-call batch (asserting order), and checks that `NOT_FOUND` maps
to HTTP 404 and invalid input to HTTP 400. It also verifies that the committed
`app-router.d.ts` matches the app. No secrets, no network beyond localhost.
