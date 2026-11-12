# Testing

`trpc_rs_py.testing` ships in-process test clients: requests are
dispatched against the real ASGI app `mount()` builds — the same
parsing, validation, [context](context.md), [middleware](middleware.md),
and error policy — with no socket, no server, and no framework fixture
to stand up. The package itself stays dependency-free; the framework
wrappers import their client lazily.

```python
# snippet: skip — excerpt; needs the test router (the ... in the assert is illustrative)
from trpc_rs_py.testing import test_client

client = test_client(router)

assert client.query("echo", {"message": "hi"}) == "echo: hi"
assert client.mutation("posts.create", {"title": "t"})["title"] == "t"
```

Each call runs the app coroutine on a fresh `asyncio.run` loop with
in-memory `receive`/`send`, so tests are ordinary sync `def` functions —
no `pytest-asyncio` needed.

## `test_client(router, ...)`

| Parameter | Contents |
| --- | --- |
| `context_factory` | Forwarded to `mount()`; runs once per request |
| `production` | `True` enables the production error-redaction policy |
| `path` | Mount prefix; requests go to `{path}/{proc_path}` (`"/trpc"` default) |
| `middleware` | Mount-level middleware, forwarded to `mount()` |

The returned `TestClient` exposes:

| Member | Contents |
| --- | --- |
| `query(path, input=None, *, headers=None)` | GET call, returns `result.data`, raises `TrpcTestClientError` |
| `mutation(path, input=None, *, headers=None)` | POST JSON call, same unwrap |
| `batch(calls, *, headers=None)` | One `?batch=1` GET; returns a list of per-call results/`TrpcTestClientError` instances |
| `subscribe(path, input=None, *, ...)` | Drives the SSE stream, returns `list[SubscriptionEvent]` |
| `app` | The raw ASGI app, for requests the client does not model |
| `requests` | Recorded `{"method", "path", "query_string"}` per dispatch |

`batch` takes `{"path": ..., "input": ...}` mappings; a failing call
yields a `TrpcTestClientError` **instance** in the list (not raised), so
partial failures are inspectable:

```python
# snippet: skip — excerpt; needs client and TrpcTestClientError from above
results = client.batch([{"path": "ping"}, {"path": "boom"}])
assert results[0] == "pong"
assert isinstance(results[1], TrpcTestClientError)
```

## `TrpcTestClientError`

Raised by `query`/`mutation`/`subscribe` and returned per-call by
`batch`. Fields: `code` (the string code from `error.data.code`, e.g.
`"NOT_FOUND"`), `message`, `data` (the `error.data` mapping with
`path`/`httpStatus`/typed extras), `http_status`.

```python
# snippet: skip — excerpt; needs pytest, the client and TrpcTestClientError
with pytest.raises(TrpcTestClientError) as excinfo:
    client.query("missing")
assert excinfo.value.code == "NOT_FOUND"
```

## Subscriptions

`subscribe` returns decoded data events (`SubscriptionEvent.data`,
`.id` for `tracked()` events); `connected`, `ping`, `return`, and
`restore-id` frames are consumed silently. A `serialized-error` raises
`TrpcTestClientError`, and `last_event_id=` is sent as the
`last-event-id` header for resume tests.

The stream can never hang: it ends when the generator ends, `max_events`
arrive (default 100 — the client then synthesizes `http.disconnect`,
running the adapter's real disconnect path), or `timeout` seconds pass
(default 30 — covers generators blocked mid-yield):

```python
# snippet: skip — excerpt; needs the test client from above
events = client.subscribe("ticks", {"count": 2})
assert [e.data for e in events] == [{"n": 0}, {"n": 1}]

first_three = client.subscribe("endless", max_events=3)
```

## Framework clients

For fixture parity with a real deployment, the wrappers return each
framework's own test client — real URLs in, raw envelopes out:

```python
# snippet: skip — excerpt; needs the router from your test suite
from trpc_rs_py.testing import flask_test_client, django_test_client

client = flask_test_client(router)              # werkzeug Client
response = client.get("/trpc/ping")
assert response.json == {"result": {"data": "pong"}}

client = django_test_client(router)             # django.test.Client
client = django_test_client(router, asynchronous=True)  # AsyncClient
```

`flask_test_client` forwards kwargs to `mount_wsgi`; `django_test_client`
forwards them to `trpc_view`/`trpc_view_async` (plus `prefix=` for the
mount point) and configures minimal Django settings when none exist.
Both raise `RuntimeError` when the framework is not installed.
