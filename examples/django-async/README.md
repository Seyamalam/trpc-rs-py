# django-async

A minimal Django app serving a tRPC router through the **async** view
(ASGI side, under uvicorn), callable by the official `@trpc/client` with
full end-to-end types.

It demonstrates:

- a **query** (`user_by_id`, `greeting`) and a **mutation** (`create_user`)
  defined as plain Python functions on a `trpc_rs_py.Router`;
- **Pydantic v2 validation** of inputs and outputs straight from type hints;
- **error handling** with `TRPCError` (`NOT_FOUND` maps to HTTP 404 and the
  official tRPC error envelope);
- **REST + OpenAPI output**: `asgi_view_async(mount_rest(router))` serves
  `/api/greeting/<name>` and `/api/openapi.json`;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## How it mounts

```python
urlpatterns = [
    *trpc_urlpatterns(ROUTER, asynchronous=True),        # /trpc/*  (async view)
    path("api/<path:path>", asgi_view_async(REST)),      # /api/*   (REST + OpenAPI)
]
```

The async views run the tRPC dispatch directly on Django's event loop, so
SSE subscriptions stream with real pull-based backpressure — no worker
threads. This is the recommended view when Django runs under ASGI.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/django-async
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

## Smoke test

```bash
./examples/django-async/smoke.sh
```

The smoke test starts the Django ASGI app under uvicorn on a free port and
exercises queries, mutations, batching, error mapping, REST, and the
OpenAPI document over real HTTP, then verifies the committed
`app-router.d.ts` matches.
