# standalone-server

A tRPC router served directly by `trpc_rs_py.serve.serve()` — the standalone
Rust HTTP server embedded in `trpc_rs_py._core`. No Python web framework is
involved: `serve()` mounts the router exactly like `mount()` and drives the
resulting ASGI app on an in-process hyper server (ADR 0013). This is the
highest-throughput way to run a `trpc_rs_py` router.

It demonstrates:

- `serve(router, production=True, port=...)` — one call replaces uvicorn;
- a **query** (`user_by_id`), a **mutation** (`create_user`), and a `boom`
  query that always raises;
- **production mode**: `boom`'s internal `RuntimeError` is redacted to the
  fixed `"Internal server error"` message and the playground is off;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/standalone-server
uv run --project ../.. python app.py          # listens on 127.0.0.1:8000
uv run --project ../.. python app.py 9000     # or pass a port
```

`serve()` blocks until Ctrl-C, then shuts down gracefully and returns `0`.
The same app is what `trpc-py serve --app app:build_router` runs; calling
`serve()` directly keeps everything in one file.

## Call it with the official tRPC client

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

const user = await client.create_user.mutate({ user: { name: "Ada", age: 36 } });
const found = await client.user_by_id.query({ user_id: user.id });
```

Regenerate the types after editing the router:

```bash
uv run python examples/standalone-server/generate_types.py
```

## Smoke test

```bash
./examples/standalone-server/smoke.sh
```

The smoke test starts `app.py` on a free port, calls the mutation, a query,
a two-call batch (asserting order), checks that `NOT_FOUND` produces HTTP
404 with the official error envelope, and verifies production mode redacts
`boom`'s internal error. It stops the server with SIGINT (the graceful
shutdown path) and also verifies that the committed `app-router.d.ts`
matches the app. No secrets, no network beyond localhost.
