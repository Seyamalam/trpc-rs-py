# python-client

A `trpc_rs_py` router served by uvicorn, called from **Python** with
`trpc_rs_py.client.create_client` — the stdlib-only tRPC v11 client that
speaks the same wire protocol as the official TypeScript client — and by the
generated `AppRouterClient` stub, a typed one-method-per-procedure wrapper.

It demonstrates:

- `client.query` / `client.mutation` unwrapping `result.data`;
- `client.batch` returning ordered per-call results, with a failed call
  surfacing as a `TrpcClientError` **instance** instead of raising;
- **typed errors** round-tripping: `PostNotFound` (declared via
  `errors=[PostNotFound]` with a `data_model`) arrives as a
  `TrpcClientError` carrying `code`, `http_status` and the `post_id`
  payload;
- `generate_python_client(router)` emitting `generated_client.py`, whose
  `AppRouterClient` copies the handler signatures verbatim.

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/python-client
uv run --project ../.. python app.py     # PORT=9000 to override (default 8000)
```

In another shell:

```bash
cd examples/python-client
uv run --project ../.. python client.py
# or: uv run --project ../.. python client.py http://127.0.0.1:9000/trpc
```

`client.py` asserts each call and exits non-zero on any failure.

## Using the client in your own code

```python
from trpc_rs_py.client import TrpcClientError, create_client
from generated_client import AppRouterClient

with create_client("http://127.0.0.1:8000/trpc") as client:
    client.query("greeting", {"name": "Ada"})  # -> "Hello, Ada!"
    client.mutation("create_post", {"title": "hi"})  # -> {"id": 1, ...}

    try:
        client.query("post_by_id", {"post_id": 99})
    except TrpcClientError as error:
        error.code  # "NOT_FOUND"
        error.data["post_id"]  # 99 — the declared typed payload

    stub = AppRouterClient(client)
    stub.post_by_id(1)  # typed: -> app.Post
    stub.list_posts()  # typed: -> list[app.Post]
```

Regenerate the checked-in stubs after editing the router:

```bash
uv run python examples/python-client/generate_client.py   # generated_client.py
uv run python examples/python-client/generate_types.py    # app-router.d.ts
```

(`generated_client.py` is generator output committed verbatim — the smoke
test fails on drift, so it is never hand-edited.)

## Smoke test

```bash
./examples/python-client/smoke.sh
```

The smoke test starts the app on a free port, verifies both generated files
match the router, asserts the typed error maps to HTTP 404 with the declared
payload over raw HTTP, then runs `client.py` for the full query / mutation /
batch / typed-error round-trip. No secrets, no network beyond localhost.
