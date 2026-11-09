# tRPC v11 wire protocol

This page documents the exact tRPC v11 HTTP wire format trpc-rs-py implements
and how it is verified. The source of truth is the official
`@trpc/server`/`@trpc/client` v11 (pinned at 11.25.0 in
`clients/ts-tests`); every claim below is backed either by the committed
Python protocol tests (`tests/test_asgi.py`, `tests/test_core_boundary.py`)
or by the runtime compatibility suite
([`clients/ts-tests/src/runtime.test.ts`](https://github.com/Seyamalam/trpc-rs-py/blob/main/clients/ts-tests/src/runtime.test.ts)),
which spawns live FastAPI and Starlette servers and calls them with the real
official `@trpc/client` over HTTP.

## Transport and URLs

- **Queries** are `GET` requests: `GET <url>/<procedure_path>?input=<json>`.
- **Mutations** are `POST` requests with a JSON body containing the input
  directly (`application/json`). GET on a mutation path is rejected with
  `METHOD_NOT_SUPPORTED` and HTTP 405 (runtime-verified).
- **Batching** appends `?batch=1`, joins the procedure paths with a plain
  comma (`/trpc/echo,echo`), and sends all inputs as one JSON object keyed by
  call index — in the `input` query parameter for GET
  (`input={"0":{...},"1":{...}}`), as the JSON body for POST.
- The official client sends `content-type: application/json` on POST and the
  server enforces that content type on POST requests.
- The comma in a batched path is **not** percent-encoded by the client.
- Method enforcement is **per call**, matching the official server (which
  checks the method inside its per-call loop): a batched GET touching a
  mutation returns a batch array whose entry for that call carries the
  `METHOD_NOT_SUPPORTED` envelope. A non-batched GET on a mutation returns
  the single error envelope with HTTP 405 (runtime-verified).

## Transformer

Only the **identity transformer** is supported (tRPC v11 default). Inputs are
raw JSON, outputs are raw JSON — no `{"json": ...}` superjson-style wrapping
anywhere. The generated `AppRouter` declares `transformer: false`
(`docs/codegen.md`). superjson/transformer support is planned after 0.3.0.

## Response envelopes

Per-call success envelope (identity transformer):

```json
{"result": {"data": <json>}}
```

Batched responses are a JSON **array** of per-call envelopes, in the request's
call order (runtime-verified: two batched queries resolve in order from one
HTTP request).

Per-call error envelope — the **official v11 shape**, verified against a real
`@trpc/server` 11.25.0 server:

```json
{
  "error": {
    "message": "deliberate not found",
    "code": -32004,
    "data": {"code": "NOT_FOUND", "httpStatus": 404}
  }
}
```

`error.code` is the numeric JSON-RPC-style code, `error.data.code` the
machine-readable name (same table as `crates/trpc-core/src/error.rs`, mirrored
in `python/trpc_rs_py/errors.py`). The official client requires exactly this
placement: `transformResult` accepts an error only when `error.code` is a
number and `error.message` a string, and surfaces `error.data` as the client
error's `data`/`shape.data`. The official server additionally includes
`data.path` and `data.stack` (stack only in development mode); the client
treats both as optional, so their absence is compatible.

## HTTP status codes

Per-call codes map from the tRPC error code (`PARSE_ERROR`→400,
`BAD_REQUEST`→400, `NOT_FOUND`→404, `METHOD_NOT_SUPPORTED`→405, ...; full
table in `crates/trpc-core/src/error.rs`).

- A **non-batched** error response carries the mapped status (404 observed).
- A **batched** response: the official server returns the uniform per-call
  status when every call maps to the same HTTP status (a lone
  `METHOD_NOT_SUPPORTED` call → 405), and **207** when the per-call statuses
  differ. The official client **ignores the batch response's HTTP status**
  and drives errors from the per-call envelopes (verified in the runtime
  suite), so any status works for official-client traffic; the per-call
  envelope is what matters.

## Subscriptions

Subscription procedures stream over Server-Sent Events when called through
the official `httpSubscriptionLink` (GET, `EventSource`). The frame layout
matches `@trpc/server` byte-for-byte:

- On connect: `event: connected` with `data: {}`.
- Each yielded value: a default `message` event carrying
  `data: <compact JSON>`. `tracked(id, value)` yields add `id: <event id>`;
  the client reports it back as `lastEventId` on reconnect (via the
  `last-event-id` header or merged into the input object — handlers may
  declare a `lastEventId` parameter to receive it, and it is stripped from
  the validated input either way).
- An untracked yield after a tracked one emits an empty `id:` followed by a
  `restore-id` data event, matching the official resume-tracking behavior.
- Idle streams emit `event: ping` every `sse_ping_interval` seconds
  (disabled by default); clean generator completion sends `event: return`.
- Handler and validation failures send `event: serialized-error` with the
  flat tRPC error shape (`message`, `code`, `data.code`, `data.httpStatus`),
  then end the stream.
- Responses use the official headers: `Content-Type: text/event-stream`,
  `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`,
  `Connection: keep-alive`.

Subscriptions accept GET and POST (the adapter runs with the official
`allowMethodOverride` semantics, matching queries); any other method ends
the stream with a `serialized-error` `METHOD_NOT_SUPPORTED` for a single
call. Subscriptions cannot be batched: a subscription call inside a batch
answers with a per-call `METHOD_NOT_SUPPORTED`/`BAD_REQUEST` JSON envelope,
and a batch mixing procedure types is rejected at the top level — both
matching the official server. Client disconnect (`http.disconnect`) cancels
the pending `__anext__` and `aclose()`s the generator, so in-flight producer
work (for example an LLM call) stops. `max_sse_event_size` caps a single
encoded frame.

Stream calls over `trpc-accept: application/jsonl` are recognized but JSONL
responses are not implemented: an unbatched stream call is rejected at the
top level (`Streaming requests must be batched`, matching the official
server), and a batched stream call gets ordinary JSON envelopes rather than
a JSONL stream.

## Runtime compatibility status

The runtime suite exercises the official `@trpc/client` 11.25.0 against
live FastAPI and Starlette servers: 32 tests (16 cases per adapter), all
passing, including `httpSubscriptionLink` SSE subscriptions with tracked
event ids and async iteration. It confirmed that the server emits the flat error envelope above,
that omitted batch indexes are interpreted as `null`/no-input, and that
method enforcement is per call. Those behaviors are covered by the Rust,
Python, and live client tests. The remaining known deviation is batch HTTP
status selection: batches whose per-call statuses differ use the first
error's status rather than the official server's 207; the official client
ignores the batch status and resolves/rejects each call from its envelope,
so this does not break wire compatibility for the supported client.
