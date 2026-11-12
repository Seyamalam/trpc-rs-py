# feature-typed-errors

Typed errors: procedures declare the `TRPCError` subclasses they can raise,
each with a `code` and a Pydantic `data_model`. The wire envelope merges the
payload fields into `error.data`, and the generated TypeScript gives clients
a **discriminated union** to `switch` on (ADR 0009).

It demonstrates:

- `class OrderNotFound(TRPCError)` with `code = TRPCErrorCode.NOT_FOUND` and
  `data_model = OrderRef` → HTTP 404, `error.data.order_id` typed;
- `class RateLimited(TRPCError)` with `code = TRPCErrorCode.TOO_MANY_REQUESTS`
  and `data_model = RateLimitInfo` → HTTP 429, `error.data.retry_after` /
  `error.data.limit` typed;
- `errors=[...]` per procedure — `order_by_id` declares `[OrderNotFound]`,
  `place_order` declares `[OrderNotFound, RateLimited]`, and the generated
  `app-router.d.ts` narrows each procedure's `errorShape` to exactly its
  declared union.

Declaration is a **type contract, not a runtime gate**: undeclared
`TRPCError`s still serialize normally, and unexpected exceptions still map
to `INTERNAL_SERVER_ERROR` (message redacted in production). See
`docs/typed-errors.md` for the wire-format details.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/feature-typed-errors
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)
```

## Call it

```bash
# Unknown order id → 404 with the typed payload in error.data.
curl "http://127.0.0.1:8000/trpc/order_by_id?input=%7B%22order_id%22%3A99%7D"
# {"error":{"message":"Order 99 not found","code":-32004,
#  "data":{"code":"NOT_FOUND","httpStatus":404,"path":"order_by_id","order_id":99}}}

# First checkout succeeds; resubmitting the same order → 429 with payload.
curl -X POST "http://127.0.0.1:8000/trpc/place_order" \
  -H 'content-type: application/json' -d '{"order_id":1}'
curl -X POST "http://127.0.0.1:8000/trpc/place_order" \
  -H 'content-type: application/json' -d '{"order_id":1}'
# {"error":{"message":"Order 1 is already being checked out","code":-32029,
#  "data":{"code":"TOO_MANY_REQUESTS","httpStatus":429,"path":"place_order",
#          "retry_after":30.0,"limit":1}}}
```

With the official `@trpc/client`, `error.data.code` narrows to the declared
member — the payload fields are directly accessible:

```ts
import { isTRPCClientError } from "@trpc/client";
import type { AppRouter } from "./app-router";

try {
  await client.place_order.mutate({ order_id: 1 });
} catch (error) {
  if (!isTRPCClientError<AppRouter>(error)) throw error;
  switch (error.data?.code) {
    case "NOT_FOUND":
      console.log(error.data.order_id); // number — fully typed
      break;
    case "TOO_MANY_REQUESTS":
      console.log(error.data.retry_after); // number
      break;
  }
}
```

## Generated types

```bash
uv run python examples/feature-typed-errors/generate_types.py
```

The committed `app-router.d.ts` contains `AppRouterErrorShape` — one union
member per declared `(code, data_model)` pair, plus a default member
covering every undeclared code — and each procedure's `errorShape` is
restricted to its own `errors=[...]` list.

## Smoke test

```bash
./examples/feature-typed-errors/smoke.sh
```

The smoke test starts the app on a free port and asserts: `OrderNotFound`
maps to HTTP 404 with `order_id` inside `error.data`; `RateLimited` maps to
HTTP 429 with `retry_after`/`limit` inside `error.data`; and the committed
`app-router.d.ts` matches the app and contains both declared codes. No
secrets, no network beyond localhost.
