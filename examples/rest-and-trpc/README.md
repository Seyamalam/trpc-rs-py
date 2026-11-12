# rest-and-trpc

ONE `trpc_rs_py.Router` mounted twice on a single FastAPI app: `mount()`
serves tRPC under `/trpc` while `mount_rest()` serves REST endpoints, an
OpenAPI 3.1 document, and a docs page under `/api`. Adopting trpc-rs-py
carries no lock-in — TypeScript frontends use tRPC, every other client uses
REST, and both share the same Pydantic validation.

It demonstrates:

- **`rest=RestRoute(method, path)`** opting each procedure into the REST
  surface: `GET /api/greeting/{name}`, `GET /api/posts/{post_id}`,
  `POST /api/posts`;
- **both protocols, one router**: a post created over REST is read back over
  tRPC and vice versa;
- **per-protocol error shapes**: the same `TRPCError(NOT_FOUND)` produces
  the tRPC envelope on `/trpc/*` and `{"error": {"code", "message"}}` on
  `/api/*`;
- **`/api/openapi.json`** generated from the router and **`/api/docs`**, a
  vendored docs UI with no CDN dependency;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and consumed by the official client.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/rest-and-trpc
uv run --project ../.. python app.py     # PORT=9000 to override (default 8000)
```

Then try both protocols:

```bash
# tRPC (GET query, official envelope)
curl 'http://127.0.0.1:8000/trpc/greeting?input={"name":"Ada"}'

# REST (bare JSON payload)
curl http://127.0.0.1:8000/api/greeting/Ada
curl -X POST http://127.0.0.1:8000/api/posts \
  -H 'content-type: application/json' \
  -d '{"post": {"title": "hello", "priority": 3}}'

# OpenAPI + docs
open http://127.0.0.1:8000/api/docs
```

Non-`RestRoute` procedures stay tRPC-only, so you can expose a subset of the
router over REST.

## Smoke test

```bash
./examples/rest-and-trpc/smoke.sh
```

The smoke test starts the app on a free port, calls the same procedure over
tRPC and REST, creates a post over each protocol and reads it back over the
other, asserts the tRPC error envelope and the REST error shape, checks the
OpenAPI document's paths, and verifies the committed `app-router.d.ts`
matches the app. No secrets, no network beyond localhost.
