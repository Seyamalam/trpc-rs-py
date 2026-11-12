# feature-error-handling

The production error policy, shown side by side with development mode. The
**same router** is mounted twice on one FastAPI app so both behaviors are
visible at once — a real deployment picks one `production` flag from
config:

- `/trpc` — `production=True`: `TRPCError`s with a client-facing code
  (`NOT_FOUND`, `BAD_REQUEST`, …) pass their message through; `TRPCError`s
  coded `INTERNAL_SERVER_ERROR` and any unexpected exception are answered
  with the fixed message `"Internal server error"`, so internals never
  leak.
- `/trpc-dev` — `production=False` (the default): every message passes
  through, including the `repr` of unexpected exceptions.

It demonstrates:

- deliberate API errors (`lookup` raising `NOT_FOUND`) are part of the
  contract — identical in both modes;
- unexpected exceptions (`crash` raising `ValueError` with a fake
  connection string inside) are redacted in production and exposed in dev;
- a `TRPCError` explicitly coded `INTERNAL_SERVER_ERROR` (`internal`) is
  redacted too — choosing the code does not mark a message client-safe;
- the fixed error envelope shape (`message`, `code`, `data.code`,
  `data.httpStatus`, `data.path`) is the same in both modes.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/feature-error-handling
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)
```

## Call it

```bash
# Deliberate NOT_FOUND — message passes through in BOTH modes.
curl "http://127.0.0.1:8000/trpc/lookup?input=%7B%22account_id%22%3A9%7D"
# {"error":{"message":"Account 9 does not exist","code":-32004,
#  "data":{"code":"NOT_FOUND","httpStatus":404,"path":"lookup"}}}

# Unexpected exception — redacted under the production mount…
curl "http://127.0.0.1:8000/trpc/crash"
# {"error":{"message":"Internal server error","code":-32603,
#  "data":{"code":"INTERNAL_SERVER_ERROR","httpStatus":500,"path":"crash"}}}

# …and exposed under the dev mount (the repr leaks internals — that's the
# point of production mode).
curl "http://127.0.0.1:8000/trpc-dev/crash"
# {"error":{"message":"ValueError('could not connect to postgres://…')", …}}
```

## Generated types

```bash
uv run python examples/feature-error-handling/generate_types.py
```

## Smoke test

```bash
./examples/feature-error-handling/smoke.sh
```

The smoke test starts the app on a free port and asserts, against **both**
mounts: `NOT_FOUND` messages pass through; an unexpected `ValueError` is
redacted to `"Internal server error"` with no internals in the body in
production but carries the full `repr` in dev; and a `TRPCError` coded
`INTERNAL_SERVER_ERROR` is redacted as well. It also verifies the committed
`app-router.d.ts` matches the app. No secrets, no network beyond localhost.
