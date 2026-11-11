# ADR 0014: Python client transport and codegen stubs

Status: Accepted
Date: 2026-11-11

## Context

MISSION section 3 requires `trpc_rs_py.client` — "a typed sync and async
client for calling a tRPC server from Python, with generated stubs" — and
section 8 the `codegen py-client` CLI command. Two choices: the HTTP
transport(s), and what generated stubs look like in a language where the
server's own annotations are already the source of truth.

## Options

### Transport

1. **Stdlib only everywhere.** Sync via `urllib.request`/`http.client`
   (response objects stream SSE line-by-line). Async has no stdlib HTTP
   client — a hand-rolled `asyncio` HTTP/1.1 implementation is ~200 lines
   before TLS, chunked encoding, and proxies, and each of those is a
   footgun.
2. **Sync stdlib + async on lazy `httpx`.** The async client lazily
   imports `httpx` and raises `RuntimeError` with an install hint when it
   is missing — identical to how `testing.py` treats werkzeug/Django and
   `middleware.py` treats `opentelemetry-api`. httpx is the de-facto
   async HTTP client; it also covers sync users who prefer pooling.
3. **Hard `httpx` dependency for both.** Simplest API surface but makes
   every install carry a new dependency for a feature many users never
   use. Rejected — the package ships pydantic-only today.

### Generated stubs

1. **A typed `AppRouterClient` class** — one method per procedure, with
   the handler's own parameter and return annotations copied into the
   stub (same language: no type translation needed), each method one
   line delegating to `client.query`/`mutation`/`subscribe`. The stub is
   *actually typed* — `mypy` verifies call sites against it.
2. **Protocol/`.pyi` files only** — types without an implementation;
   callers still hand-write paths. Pointless extra machinery.
3. **No codegen at all** — dynamic `client.query("posts.by_id", ...)`
   stays available either way (it is the base client's API), but typed
   stubs are what make `codegen py-client` worth existing.

## Decision

Options 2 + 1: `create_client(url, *, headers=None)` is stdlib-only;
`create_async_client(url, *, headers=None)` lazily requires `httpx`.
`codegen py-client` emits an `AppRouterClient` wrapper class.

Client semantics, mirroring the server contract:

- `client.query(path, input)`, `client.mutation(path, input)` → return
  `result.data`; error envelopes raise `TrpcClientError(code, message,
  data, http_status)` — the same shape `testing.TrpcTestClientError`
  uses, so in-process and over-the-wire failures look identical.
- `client.batch([{path, input}, ...])` → one `?batch=1` request, a
  per-call list of results or `TrpcClientError` instances.
- `client.subscribe(path, input, *, last_event_id=None)` → an iterator
  (async iterator for `AsyncClient`) of `SubscriptionEvent(data, id)`;
  `id` is set on `tracked()` events and passed back on reconnect for
  resume; a `serialized-error` frame raises `TrpcClientError`.
- Subscriptions send `lastEventId` inside the input object exactly like
  the official client, not a bespoke header.
- The generated stub class takes a base client at construction; a
  `subscribe` procedure becomes a method returning the event iterator.
- Everything is reusable from `trpc_rs_py.testing` where it already
  exists conceptually — but the client is its own module: `testing` is
  for tests of routers, `client` talks to real HTTP servers.

## Consequences

- Zero new hard dependencies; `httpx` is needed only for the async
  client, matching the project's lazy-dependency convention.
- `codegen py-client` output is plain Python — it can be checked into a
  service repo and type-checked with mypy directly.
- The standalone server (ADR 0013) plus this client gives a pure-Python
  deploy path end to end: `trpc-py serve` on one side, generated
  `AppRouterClient` on the other.
