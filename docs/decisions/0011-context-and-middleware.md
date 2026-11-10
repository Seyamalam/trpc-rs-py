# ADR 0011: Per-request context and the middleware chain

Status: Accepted
Date: 2026-11-10

## Context

0.6.0 (MISSION section 19) requires context factories, middleware (auth,
logging, timing, rate limiting), CORS and CSRF helpers, tracing hooks, and
in-process test clients. Everything except CORS needs the same two missing
primitives:

1. **A per-request context.** Today a handler receives only its decoded JSON
   input; it cannot see headers, the client address, or per-request state.
   Auth, rate limiting, and audit logging are impossible without it.
2. **An interception point.** `asgi._run_call` invokes handlers directly;
   there is no way to run code before/after a procedure, short-circuit it,
   or time it.

## Options

1. **Starlette-style HTTP middleware only** (wrap the ASGI app). Rejected as
   the *only* mechanism: it cannot see procedure names, batch contents, or
   per-call results, and it does not exist on the REST/OpenAPI path's
   per-procedure granularity.
2. **tRPC-style procedure middleware** (`ctx` injection + `next()` chain).
   Matches the official tRPC mental model, works identically on REST calls,
   gives auth/rate-limit/tracing one place to live, and — because every
   adapter funnels into the same `_serve_trpc`/`_run_call` (ADR 0010) —
   ships once for all five frameworks.
3. **Signal/hook registry** (`on_call_start`, `on_call_end`). Weaker: no
   short-circuiting, no ctx augmentation. Tracing is expressed as middleware
   instead.

## Decision

Option 2.

### Context

- **`RequestContext`** is a frozen dataclass carrying `method`, `path`,
  `query_string`, `headers` (immutable mapping), `client` (`(host, port)` or
  `None`), `request` (the framework request object — ASGI scope dict under
  `mount()`/`mount_wsgi()`, `django.http.HttpRequest` under Django views,
  WSGI `environ` under `mount_wsgi()`), and `user` (a slot for middleware to
  fill; `None` by default). It is cheap to construct and never exposes
  internals clients could not already see.
- **`context_factory`** is an optional parameter on every adapter entry
  point (`mount`, `attach`, `mount_wsgi`, `attach_flask`, `trpc_view`,
  `trpc_view_async`, `mount_rest`). It receives the `RequestContext` and
  returns whatever object the app wants handlers to see as `ctx` — a dict,
  a dataclass, the `RequestContext` itself. It may be sync or async and is
  called **once per HTTP request** (not per batched call), matching the
  official client's semantics where `createContext` runs per request.
  Default: return the `RequestContext`.
- **`ctx` is a reserved parameter name.** When a handler's first parameter
  is named `ctx`, it is excluded from input validation, REST/OpenAPI
  parameter mapping, `codegen ts`, and `codegen zod`, and the context object
  is injected at call time. A user who needs a literal `ctx` input field can
  rename it — documented. Procedures without a `ctx` parameter are
  unaffected and skip the injection cost entirely.
- The generated `.d.ts` keeps `ctx: object` in `AppRouterRootTypes`; the
  context type is server-side and does not change the client contract.

### Middleware

- **`Middleware`** is `Callable[[Call, Next], Awaitable[Any] | Any]`.
  `Call` is a frozen view of the invocation: `path`, `kind`, `input` (raw
  decoded input), `ctx`, `request_context`. `Next` is
  `Callable[[], Awaitable[Any]]`; awaiting it runs the rest of the chain and
  the handler and returns the handler's (pre-output-validation) result.
  Raising inside middleware produces the normal error envelope
  (`TRPCError` keeps its code).
- Middleware may **replace** the context object downstream handlers see:
  `await next(ctx=obj)` swaps it; `await next()` keeps it. One optional
  parameter, one behavior (tRPC's `next({ctx})` with the braces dropped).
- Registered **per router** (`Router(middleware=[...])` and `router.use(fn)`
  appending at the end) and per `mount`-family call (`middleware=[...]`),
  mounted after router-level ones, outermost first — so global concerns
  (auth) wrap per-router ones.
- The chain runs **inside `_run_call`**: input validation happens first
  (mirroring tRPC, where parse failure precedes middleware), then the
  middleware/handler invocation, then output validation after the chain
  unwinds.
- Subscriptions call the middleware chain once at subscription start to
  produce the `ctx` the generator sees; the per-event path is not
  re-entered (a middleware wrapping every yielded item would serialize and
  complicate backpressure for no demonstrated need — revisit if asked).

### Helpers built on the chain

- `trpc_rs_py.middleware.logging_middleware()` — logs `path`, `kind`,
  duration, and outcome.
- `trpc_rs_py.middleware.timing_middleware()` — records duration on
  `ctx`-visible storage.
- `trpc_rs_py.middleware.rate_limit(requests_per_minute, key=...)` —
  in-process token bucket keyed on `ctx` (default client IP), raising
  `TOO_MANY_REQUESTS`.
- `trpc_rs_py.middleware.require_auth(predicate)` — maps to
  `UNAUTHORIZED`.
- `trpc_rs_py.middleware.tracing_middleware(tracer=None)` — OpenTelemetry
  span per call when `opentelemetry` is installed; hard-fails on import
  otherwise (optional dependency, never vendored).
- `trpc_rs_py.cors.cors(...)` — an ASGI middleware factory with preflight
  handling; because WSGI and Django run through the same ASGI app
  (ADR 0010), one implementation covers all adapters
  (`wsgi_from_asgi(cors(mount(...)))`, `asgi_view(cors(...))`).
- `trpc_rs_py.csrf.csrf_protect(allowed_origins=...)` — adapter-level
  request filter validating `Origin`/`Sec-Fetch-Site` on non-safe methods
  and GET queries carrying credentials; layered before dispatch, not inside
  middleware, because it must also cover REST routes.

### Test clients

`trpc_rs_py.testing` ships `test_client(router, context_factory=...)` —
in-process dispatch through the real ASGI app (no socket) plus thin
`flask_test_client`/`django_test_client` wrappers using each framework's own
test client so fixture parity is proven, not assumed.

## Consequences

- New reserved name `ctx` — a breaking change for any handler that uses
  `ctx` as an input field name today. Documented in CHANGELOG with the
  rename workaround; `check` gains a lint for it.
- `_run_call` gains context construction and the middleware chain on every
  call — hot-path commits carry before/after `pytest-benchmark` numbers.
- Sync middleware runs inline (not `to_thread`): middleware is expected to
  be cheap orchestration, and offloading it would break the "middleware
  runs before the handler" ordering for free-threaded correctness without
  buying anything measurable.
