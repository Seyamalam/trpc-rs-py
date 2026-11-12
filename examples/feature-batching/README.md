# feature-batching

tRPC v11 request batching: several calls travel in **one HTTP request** and
return an **ordered list of per-call envelopes**, where a failing call
produces its own error entry without affecting the others.

It demonstrates:

- several procedures (`add`, `multiply`, `divide`) invoked in a single
  `?batch=1` request;
- the ordered per-call result/error envelopes — the batch HTTP status
  follows its worst per-call error (the official v11 wire contract);
- the `trpc_rs_py.client.create_client(...).batch(...)` API, which returns a
  list where each entry is the call's `result.data` or a `TrpcClientError`
  instance for a failed call.

Queries batch over one GET (inputs ride in a single `input` map keyed by
call index); mutations batch over one POST. Calls in a batch must share one
procedure kind — the official client splits query and mutation batches for
you — and subscriptions cannot be batched.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/feature-batching
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)
```

## Call it

Four calls — `add`, `multiply`, `divide`, and a `divide` that fails — in one
GET (the `input` parameter is a JSON map from call index to that call's
input):

```bash
curl -g "http://127.0.0.1:8000/trpc/add,multiply,divide,divide?batch=1&input=%7B%220%22%3A%7B%22a%22%3A2%2C%22b%22%3A3%7D%2C%221%22%3A%7B%22a%22%3A2%2C%22b%22%3A3%7D%2C%222%22%3A%7B%22a%22%3A7%2C%22b%22%3A2%7D%2C%223%22%3A%7B%22a%22%3A1%2C%22b%22%3A0%7D%7D"
# [{"result":{"data":5}},{"result":{"data":6}},{"result":{"data":3.5}},
#  {"error":{"message":"Division by zero","code":-32600,"data":{...}}}]
```

Or with the Python client (see `client.py`):

```python
from trpc_rs_py.client import create_client

with create_client("http://127.0.0.1:8000/trpc") as client:
    results = client.batch(
        [
            {"path": "add", "input": {"a": 2, "b": 3}},
            {"path": "multiply", "input": {"a": 2, "b": 3}},
            {"path": "divide", "input": {"a": 7, "b": 2}},
            {"path": "divide", "input": {"a": 1, "b": 0}},
        ]
    )
    # results == [5, 6, 3.5, TrpcClientError(code="BAD_REQUEST", ...)]
```

With the official `@trpc/client`, `httpBatchLink` / `httpBatchStreamLink`
batch automatically — concurrent calls in one tick are merged into a single
request, split by procedure kind.

## Generated types

```bash
uv run python examples/feature-batching/generate_types.py
```

## Smoke test

```bash
./examples/feature-batching/smoke.sh
```

The smoke test starts the app on a free port and asserts the raw wire shape
of a four-call batched GET (ordered envelopes, an error entry for the
divide-by-zero call, HTTP 400 on the batch) plus the Python client's
ordered/mixed batch results. It also verifies the committed
`app-router.d.ts` matches the app. No secrets, no network beyond localhost.
