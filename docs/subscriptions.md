# Subscriptions

`@router.subscription` turns an async generator into a tRPC subscription,
streamed over Server-Sent Events in the exact format `@trpc/server` emits —
the official `@trpc/client` `httpSubscriptionLink` works against it
unchanged.

```python
from collections.abc import AsyncIterator

from pydantic import BaseModel
from trpc_rs_py import Router, tracked


class Token(BaseModel):
    text: str


router = Router()


@router.subscription
async def chat(room: str, lastEventId: str | None = None) -> AsyncIterator[Token]:
    async for token in stream_llm_tokens(room, since=lastEventId):
        yield tracked(token.seq, token)  # resumable: client sees {id, data}
```

On the client:

```ts
const sub = trpc.chat.subscribe(
  { room: "lobby" },
  { onData: ({ id, data }) => render(data.text) },
);
```

## Behavior

- `tracked(event_id, data)` marks a yield resumable: the id rides as the SSE
  `id:` field and comes back as `lastEventId` on reconnect — via the
  `last-event-id` header, the `lastEventId` query/input member, or a
  declared `lastEventId` handler parameter (never part of the generated
  input type). Ids containing newline, carriage return, or NUL are rejected.
- A bare `yield` after tracked yields emits an empty `id:` and a
  `restore-id` event, matching the official client resume tracking.
- `event: ping` keep-alives: `mount(..., sse_ping_interval=30)` (seconds;
  off by default). `max_sse_event_size` caps one encoded frame (default
  1 MiB); an oversized yield ends the stream with `serialized-error`.
- Client disconnect (`http.disconnect`) cancels the pending `__anext__` and
  `aclose()`s the generator — in-flight LLM calls stop immediately.
- GET and POST are accepted (method-override semantics, same as queries);
  subscriptions cannot be batched. Errors stream as `serialized-error`
  events carrying the flat tRPC error shape.

## Generated types

`generate_types` emits `SubscriptionProcedure` declarations:

```ts
export interface ChatProcedure
  extends SubscriptionProcedure<{
    input: { room: string }; // lastEventId stays protocol metadata
    output: AsyncIterable<{ id: string; data: Token }>;
    // ...
  }> {}
```

The handler's `AsyncIterator[X]` return annotation maps to
`AsyncIterable<X>`; `Tracked[X]` yields become `{ id: string; data: X }`.
See [Wire Protocol](protocol.md#subscriptions) for the frame format.
