# Concepts

The moving parts of a trpc-rs-py app, one section each. Every page linked
below goes deeper; [architecture](architecture.md) explains how the Rust
core and the Python layer split the work.

## Router

A `Router` is a named collection of procedures under dotted paths. It is
framework-agnostic: the same object serves through `mount` (ASGI),
`mount_wsgi` (WSGI), the Django views, or `trpc-py serve`. Routers compose:

```python
# snippet: skip — excerpt; needs the Router import and routers from above
app_router = Router()
app_router.nest("posts", posts_router)   # posts.by_id
app_router.merge(admin_router)           # later entries win collisions
```

`router.procedures` exposes the read-only `{path: Procedure}` mapping the
adapters and code generators consume. `trpc-py routes --app mod:router`
prints it.

## Procedures: `@query`, `@mutation`, `@subscription`

A procedure is a decorated Python function — its signature is the API
contract:

```python
# snippet: skip — excerpt; needs router, Post, PostInput and AsyncIterator
@router.query                      # read; GET or POST
def post_by_id(post_id: int) -> Post: ...

@router.mutation                   # write; POST only
def create_post(post: PostInput) -> Post: ...

@router.subscription               # stream; async generator over SSE
async def ticks() -> AsyncIterator[int]: ...
```

- The path defaults to the function name; override with
  `@router.query(name="post.byId")`.
- Inputs come from annotated parameters (a JSON object maps to keyword
  arguments), outputs from the return annotation. Both are validated by
  Pydantic v2 at dispatch time — see [validation](validation.md).
- Sync `def` handlers run on a worker thread; `async def` handlers are
  awaited on the event loop.
- Decorator options: `errors=[...]` declares [typed errors](typed-errors.md),
  `rest=RestRoute(...)` opts into [REST + OpenAPI](rest.md) (queries and
  mutations only).

## Context

Any adapter entry point takes a `context_factory`, called **once per HTTP
request** — not per batched call. Its return value is injected into
handlers whose **first parameter is named `ctx`**:

```python
# snippet: skip — excerpt; needs the imports and router from the page
def context_factory(request: RequestContext) -> dict:
    return {"user": current_user(request.headers)}

app = mount(router, context_factory=context_factory)

@router.query
def me(ctx) -> dict:          # ctx never appears in the input type
    return {"name": ctx["user"]}
```

`ctx` is stripped from validation and codegen — clients never send it.
Without a factory, `ctx` is the `RequestContext` itself (method, headers,
client address, framework-native request object). See [context](context.md).

## Middleware

Middleware is a `(call, next) -> result` callable wrapping one invocation,
in the tRPC onion model:

```python
# snippet: skip — excerpt; needs the router defined above
@router.use
async def timing(call, next):
    result = await next()       # run the rest of the chain + handler
    return result
```

`Router(middleware=[...])` / `router.use()` apply to procedures registered
afterwards; the `middleware=` argument on every mount function covers
everything and runs outermost. `call.ctx` is swappable via `next(ctx=...)`.
Built-ins: `rate_limit`, `require_auth`, `logging_middleware`,
`timing_middleware`, `tracing_middleware`. See [middleware](middleware.md).

## Errors

Raise `TRPCError` with an official v11 code — the adapter serializes the
exact envelope `@trpc/client` expects:

```python
# snippet: skip — excerpt; an error raised inside a handler
raise TRPCError("no such post", code=TRPCErrorCode.NOT_FOUND)
```

Unexpected exceptions become `INTERNAL_SERVER_ERROR`; under
`production=True` their details are redacted. For typed payloads the client
can switch on, subclass `TRPCError` with a `data_model` and declare it in
`errors=[...]` — the generated `.d.ts` exposes a discriminated union on
`error.data.code`. See [typed errors](typed-errors.md) and
[error handling on the client](ts-client.md#typed-errors).

## Batching

The official client automatically joins concurrent same-type calls into
one `?batch=1` HTTP request — no server setup needed. Each call in the
batch validates, dispatches, and errors independently; the server answers
one response with per-call envelopes. `Limits.max_batch_size` (default
100) caps batch width. See [wire protocol](protocol.md) and
[client batching](ts-client.md#links-and-batching).

## Subscriptions

An `async def` generator becomes a tRPC subscription over Server-Sent
Events — the same format `@trpc/server` emits, so `httpSubscriptionLink`
works unchanged:

```python
# snippet: skip — excerpt; needs the imports, router and stream_llm_tokens
class Token(BaseModel):
    seq: int
    text: str

@router.subscription
async def chat(room: str, lastEventId: str | None = None) -> AsyncIterator[Token]:
    async for token in stream_llm_tokens(room, since=lastEventId):
        yield tracked(str(token.seq), token)   # resumable event id
```

`tracked(id, data)` marks an event resumable — the id returns as
`lastEventId` on reconnect (header, query/input member, or the declared
handler parameter, never part of the input type). Disconnects cancel the
generator. Bounded by `sse_ping_interval` and `max_sse_event_size` mount
options; subscriptions cannot be batched. See [subscriptions](subscriptions.md).

## Limits

`Limits` carries the enforced request bounds — body size, batch size, JSON
depth, per-call timeout — accepted by every adapter entry point:

```python
# snippet: skip — excerpt; needs mount, router and Limits imported
app = mount(router, limits=Limits(max_body_size=2 * 1024 * 1024,
                                  procedure_timeout=30.0))
```

See [limits](limits.md) for the field table and semantics.
