# feature-auth-context

Bearer-token auth in a FastAPI + `mount()` tRPC app: a `context_factory`
resolves the caller into `ctx.user`, and the built-in `require_auth`
middleware gates the protected procedure.

It demonstrates:

- **`context_factory`** — called once per HTTP request with a
  `RequestContext`; whatever it returns becomes the `ctx` object middleware
  and handlers see. Here it reads `Authorization: Bearer <token>` and stores
  the resolved `User` on `request.user`.
- **`require_auth`** (`trpc_rs_py.middleware`) — a ready-made middleware that
  runs a predicate over `ctx` and raises `TRPCError` with code
  `UNAUTHORIZED` (HTTP 401) when it fails.
- **public vs. protected procedures on one router** — `router.use(...)`
  applies to procedures registered *after* it (the middleware chain is
  snapshotted at registration), so `greeting` stays public while `me` is
  gated. This is the same shape as tRPC's `publicProcedure` /
  `protectedProcedure`.
- **ctx injection** — `me(ctx, ...)` names its first parameter `ctx` and
  receives the context object; the parameter is excluded from the client
  input schema and from the generated TypeScript.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/feature-auth-context
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)
```

The demo token table in `app.py` maps `token-ada` and `token-grace` to users;
a real app would verify a JWT or query a session store inside
`context_factory`.

## Call it

```bash
# Public procedure — no credentials needed.
curl "http://127.0.0.1:8000/trpc/greeting"
# {"result":{"data":"Hello, world!"}}

# Protected procedure — 401 without a token, and with an unknown one.
curl -i "http://127.0.0.1:8000/trpc/me"

# Protected procedure with a valid bearer token.
curl -H "Authorization: Bearer token-ada" "http://127.0.0.1:8000/trpc/me"
# {"result":{"data":{"id":1,"name":"Ada"}}}
```

With the official `@trpc/client`, attach the header in the link:

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: "http://127.0.0.1:8000/trpc",
      headers: () => ({ Authorization: `Bearer ${getToken()}` }),
    }),
  ],
});

const me = await client.me.query(); // { id: number; name: string }
```

## Generated types

```bash
uv run python examples/feature-auth-context/generate_types.py
```

## Smoke test

```bash
./examples/feature-auth-context/smoke.sh
```

The smoke test starts the app on a free port and asserts: the public
procedure answers with and without a token; the protected procedure rejects
a missing token and an unknown token with HTTP 401 and the `UNAUTHORIZED`
error envelope; and each valid token resolves to its own user. It also
verifies the committed `app-router.d.ts` matches the app. No secrets, no
network beyond localhost.
