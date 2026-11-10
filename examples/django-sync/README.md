# django-sync

A minimal Django app serving a tRPC router through the **sync** view
(WSGI-side bridge), callable by the official `@trpc/client` with full
end-to-end types.

It demonstrates:

- a **query** (`user_by_id`, `greeting`) and a **mutation** (`create_user`)
  defined as plain Python functions on a `trpc_rs_py.Router`;
- **Pydantic v2 validation** of inputs and outputs straight from type hints;
- **error handling** with `TRPCError` (`NOT_FOUND` maps to HTTP 404 and the
  official tRPC error envelope);
- **REST + OpenAPI output**: `asgi_view(mount_rest(router))` serves
  `/api/greeting/<name>` and `/api/openapi.json`;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## How it mounts

```python
urlpatterns = [
    *trpc_urlpatterns(ROUTER),                        # /trpc/*  (sync view)
    path("api/<path:path>", asgi_view(REST)),         # /api/*   (REST + OpenAPI)
]
```

`trpc_urlpatterns(router)` wires `trpc_view` under `/trpc`; `asgi_view` is
the generic helper for any ASGI app (here `mount_rest`). Settings are
configured inline to keep the example one file — a real project splits this
into `settings.py` / `urls.py` / `wsgi.py` as usual.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/django-sync
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

## Sync-view streaming notes

The sync view pumps each request on a private event loop inside a worker
thread — an open SSE stream holds that thread for its lifetime, and
disconnects are noticed on write. Deployments that serve many concurrent
subscriptions should use the async view (`trpc_view_async` /
`trpc_urlpatterns(asynchronous=True)`, see `examples/django-async`) or the
ASGI adapters. See `docs/adapters.md` for the full limitations list.

## Smoke test

```bash
./examples/django-sync/smoke.sh
```

The smoke test starts the Django WSGI app on a free port and exercises
queries, mutations, batching, error mapping, REST, and the OpenAPI document
over real HTTP, then verifies the committed `app-router.d.ts` matches.
