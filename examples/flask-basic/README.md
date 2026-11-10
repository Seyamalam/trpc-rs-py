# flask-basic

A minimal Flask app serving a tRPC router from Python through the WSGI
adapter, callable by the official `@trpc/client` with full end-to-end types.

It demonstrates:

- a **query** (`user_by_id`, `greeting`) and a **mutation** (`create_user`)
  defined as plain Python functions on a `trpc_rs_py.Router`;
- **Pydantic v2 validation** of inputs and outputs straight from type hints;
- **error handling** with `TRPCError` (`NOT_FOUND` maps to HTTP 404 and the
  official tRPC error envelope);
- **REST + OpenAPI output**: `mount_rest` serves `/api/greeting/<name>` and
  `/api/openapi.json` through `wsgi_from_asgi`;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## How it mounts

One `DispatcherMiddleware` mounts two apps on the Flask `wsgi_app`:

- `mount_wsgi(router)` — the tRPC endpoints under `/trpc`;
- `wsgi_from_asgi(mount_rest(router))` — the REST + OpenAPI app under `/api`.

For a tRPC-only mount, `attach_flask(app, router)` does the same in one call.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/flask-basic
uv run --project ../.. python app.py   # PORT=9000 to override (default 8000)
```

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

The same procedures answer over REST:
`curl http://127.0.0.1:8000/api/greeting/ada` and
`curl http://127.0.0.1:8000/api/openapi.json`.

## WSGI streaming notes

Subscriptions (SSE) work through WSGI, but each open stream holds one worker
thread for its lifetime and disconnects are noticed on write. Set
`sse_ping_interval` on `mount_wsgi` so dead connections are cleaned up
promptly — see `docs/adapters.md` for the full limitations list.

## Smoke test

```bash
./examples/flask-basic/smoke.sh
```

The smoke test starts the app on a free port, calls the mutation, a query, a
two-call batch (asserting order), REST and the OpenAPI document, and checks
that `NOT_FOUND` maps to HTTP 404 and invalid input to HTTP 400. It also
verifies that the committed `app-router.d.ts` matches the app.
