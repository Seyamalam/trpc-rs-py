# Limits

`trpc_rs_py.Limits` carries the enforced, configurable request limits
(MISSION section 7). Pass it to any adapter entry point via `limits=` —
`mount`, `attach`, `mount_wsgi`, `attach_flask`, the Django views, and
`serve()` all accept the same object:

```python
from trpc_rs_py import Limits, Router, mount

router = Router()
app = mount(router, limits=Limits(max_body_size=2 * 1024 * 1024))
```

`limits=None` (the default) means every knob sits at its protocol default.

## Fields

| Field | Default | Enforced at | Meaning |
| --- | --- | --- | --- |
| `max_body_size` | 1 MiB | Transport + parser | Largest request body or encoded GET `input` in bytes. Exceeding it answers `PAYLOAD_TOO_LARGE` (413) — checked against `Content-Length` before any body bytes are read. |
| `max_batch_size` | 100 | Parser | Largest number of calls in one `?batch=1` request. Exceeding it answers `PAYLOAD_TOO_LARGE` (413). |
| `max_json_depth` | 32 | Parser | Deepest JSON nesting in any input value (the effective cap is the minimum of this and the serializer's recursion guard). Exceeding it answers `BAD_REQUEST` (400). |
| `procedure_timeout` | `None` (off) | Dispatch | Wall-clock bound in seconds on one procedure handler invocation. Expiry answers `TIMEOUT` (408). |

The first three are enforced by the Rust protocol parser before any
handler runs. `procedure_timeout` is a dispatch-level limit: it lives on
the same `Limits` object but is applied by the adapter around each handler
call — see below.

## `procedure_timeout`

A hung handler pins a worker or task indefinitely. Setting
`procedure_timeout` wraps each handler invocation in `asyncio.wait_for`:

```python
# snippet: skip — excerpt; needs mount, router and Limits imported
app = mount(router, limits=Limits(procedure_timeout=30.0))
```

When a handler exceeds the bound, the call fails with the standard tRPC
error envelope — `TIMEOUT`, data code `-32008`, HTTP `408` — so clients
see a real tRPC code rather than a generic 500:

```json
{"error": {"message": "Procedure \"slow\" timed out after 0.05 seconds", "code": -32008,
           "data": {"code": "TIMEOUT", "httpStatus": 408, "path": "slow"}}}
```

Semantics:

* **Queries and mutations** are covered, and **each call inside a batch is
  bounded independently** — one slow call times out while its fast
  siblings still return results.
* The bound wraps the *handler* call only. Input/output validation and
  the middleware chain run outside it, so middleware time is not
  double-counted (ADR 0015).
* REST endpoints mounted via `mount_rest` run a separate dispatch path
  and are not bounded by `procedure_timeout`.
* **Subscriptions are exempt.** Generators are long-lived by design; use
  `sse_ping_interval` to bound idle time, and disconnect cancellation
  handles cleanup.
* **Sync handlers cannot be preempted mid-call.** A `def` handler runs on
  a worker thread (`asyncio.to_thread`); after the wait times out the
  thread still runs to completion in the background. The bound is on
  *waiting*, not CPU burn — CPU-bound handlers belong off the event loop.
* Negative or non-finite values are rejected at construction with
  `ValueError`.

## SSE limits

SSE caps are **mount kwargs**, not `Limits` fields:

```python
# snippet: skip — excerpt; needs mount and router imported
app = mount(router, sse_ping_interval=10.0, max_sse_event_size=512 * 1024)
```

* `max_sse_event_size` (default 1 MiB) caps one encoded SSE frame; a
  yielded value serializing beyond it ends the stream with a
  `serialized-error` frame and a clean close.
* `sse_ping_interval` (default `None`) emits a `ping` keep-alive when a
  stream has been idle that long — it bounds unnoticed dead connections,
  not event production.

Two related bounds are structural rather than configurable (ADR 0015):
concurrent subscriptions per connection are bounded by `max_batch_size`
(one request carries at most a batch of subscription calls, and HTTP/1.1
is one stream per connection), and per-client SSE buffering is bounded by
pull-based backpressure plus the per-frame cap.
