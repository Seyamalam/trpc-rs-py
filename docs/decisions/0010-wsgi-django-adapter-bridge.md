# ADR 0010: WSGI and Django adapters — bridge to the single dispatch path

Status: Accepted
Date: 2026-11-10

## Context

MISSION section 4 requires a WSGI adapter (Flask, any WSGI app) and a Django
adapter (sync and async views plus a URL include helper), and 0.5.0 requires
documenting WSGI streaming limitations. The existing dispatch code — parse,
limits, dispatch, error policy, SSE — lives in the async `_serve_trpc` /
`_serve_subscription` pair behind `mount()`'s ASGI app.

## Options

1. **Reimplement dispatch synchronously** for WSGI and Django-sync: a second
   `_serve_trpc_sync` with its own error policy and batching logic.
2. **Bridge to the ASGI app**: translate the incoming request into an ASGI
   scope, run `mount()`'s app inside a pumped `asyncio` event loop owned by
   the adapter, and stream `send()` messages back through a thread-safe
   queue. One dispatch implementation; async handlers, `asyncio.to_thread`
   offload, and async subscription generators all work unchanged.

## Decision

Option 2 — a private `_bridge.py` used by three thin adapters:

- **`mount_wsgi(router, ...)`** returns a WSGI app. Per request it starts a
  fresh event loop in a daemon thread, feeds `receive()` from `wsgi.input`,
  forwards `send()` messages to a `queue.Queue`, and returns a generator the
  WSGI server pull-iterates. `close()` on the iterable cancels the ASGI
  coroutine (this is how SSE cleanup happens when the server drops the
  connection).
- **`trpc_view` / `trpc_view_async`** are Django views built on the same
  pieces: the sync view buffers/streams via the WSGI bridge inside a
  `StreamingHttpResponse`; the async view drives `_serve_trpc` directly and
  streams through an `asyncio.Queue` into an async `StreamingHttpResponse`.
- **`urlpatterns(router, prefix=..., asynchronous=...)`** returns a ready
  `path()` list — the URL include helper.

Protocol behavior — parsing, batching, error policy, SSE frame order — is
byte-identical to the ASGI adapters because it *is* the same code.

## WSGI streaming limitations (documented in `docs/adapters.md`)

- Each open SSE stream monopolizes one worker thread for its lifetime —
  inherent to WSGI's pull model.
- Client disconnects are not observable mid-stream: the server detects them
  only on write; cleanup happens when the iterable's `close()` runs. Use
  `sse_ping_interval` to force periodic writes that surface dead
  connections.
- Sync (WSGI) Django views serve SSE the same way; async (ASGI) Django views
  stream through `StreamingHttpResponse` with the ASGI adapter's real
  backpressure.

## Consequences

- One dispatch path to keep wire-correct — the compat suite runs the
  official client against Flask, Django sync, and Django async fixtures to
  prove parity.
- A fresh event loop per request costs ~tens of microseconds; acceptable for
  thread-per-request servers, and it is what makes async handlers possible
  under WSGI at all.
