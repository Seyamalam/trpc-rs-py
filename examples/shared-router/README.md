# shared-router

ONE router module consumed by **two different web apps**: `fastapi_app.py`
serves it through FastAPI with `mount()` (ASGI), `flask_app.py` serves it
through Flask with `mount_wsgi()` (WSGI). The router is framework-agnostic —
same procedures, same Pydantic validation, same error envelopes — so teams
can move a router between frameworks without touching the frontend.

```
shared_router.py ─┬─> fastapi_app.py   mount()        ASGI → uvicorn
   build_router() └─> flask_app.py     mount_wsgi()   WSGI → werkzeug
```

It demonstrates:

- a shared `build_router()` in `shared_router.py` with a query
  (`greeting`, `user_by_id`) and a mutation (`create_user`);
- **adapter parity**: both apps answer byte-identical bodies — the WSGI
  adapter translates every request through the same ASGI dispatch path
  (ADR 0010);
- **identical error envelopes**: `NOT_FOUND` maps to HTTP 404 with the same
  official error shape on both;
- **generated TypeScript types**: one `app-router.d.ts` describes the router
  regardless of which framework serves it.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/shared-router
uv run --project ../.. python fastapi_app.py   # listens on 127.0.0.1:8001
uv run --project ../.. python flask_app.py     # listens on 127.0.0.1:8002
```

(Both take `PORT` to override.) Then point the same client at either:

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8001/trpc" })], // or :8002
});
```

Regenerate the types after editing `shared_router.py`:

```bash
uv run python examples/shared-router/generate_types.py
```

## Smoke test

```bash
./examples/shared-router/smoke.sh
```

The smoke test starts **both** apps on free ports, fires the same query and
mutation at each and asserts the bodies are byte-identical, checks that
`NOT_FOUND` maps to HTTP 404 with the same error envelope on both, and
verifies the committed `app-router.d.ts` matches the shared router. No
secrets, no network beyond localhost.
