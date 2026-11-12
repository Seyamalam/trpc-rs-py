# Vercel AI SDK

`useChat` and friends speak the AI SDK v5 **UI message stream protocol**: a
`text/event-stream` response carrying one JSON chunk per `data:` line, sent
with the `x-vercel-ai-ui-message-stream: v1` header and terminated by
`data: [DONE]`. Two pieces implement it:

- **Chunk helpers** shape an LLM token source into protocol parts:
  `ai_text_stream`, `ai_message_stream`, `ai_data_part`.
- **`ai_sdk_endpoint`** serves them as a plain ASGI app speaking the
  protocol directly — the frontend needs **zero custom transport code**
  (see [ADR 0012](decisions/0012-vercel-ai-sdk.md) for why this beats a
  tRPC-subscription transport).

## Endpoint + Python

```python
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI

from trpc_rs_py.streaming import (
    ai_message_stream,
    ai_sdk_endpoint,
    ai_text_stream,
)


async def chat(body: Any, scope: Any) -> AsyncIterator[dict[str, Any]]:
    messages = body["messages"] if body else []
    return ai_message_stream(ai_text_stream(llm_reply(messages)))


app = FastAPI()
app.mount("/api/chat", ai_sdk_endpoint(chat))
```

The factory is called per request with the decoded JSON body —
`{"messages": [...], ...}` for `useChat` POSTs, `None` for GET or an empty
body — and the raw ASGI `scope` (headers, client), and returns a sync or
async iterable of chunk dicts. Cancellation and backpressure mirror the
subscription transport: disconnect cancels the in-flight `__anext__` and
`aclose()`s the generator (in-flight LLM calls stop), and the next chunk is
only pulled after the previous frame was accepted by `send`.

`ai_sdk_endpoint` options: `max_body_size` (default 1 MiB, 413 beyond),
`keep_alive_interval` (emits `: stream-open` and `: keep-alive` comments
while idle, like the official `keepAliveMs`), and `error_text` mapping
exceptions to `errorText` (default `str` — pass a fixed lambda in
production).

## useChat wiring (TypeScript)

```ts
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";

const { messages, sendMessage, status } = useChat({
  transport: new DefaultChatTransport({ api: "/api/chat" }),
});
```

That is the entire frontend. `DefaultChatTransport` (also the `useChat`
default) POSTs `{ messages }` and consumes the stream: `text-*` chunks
append to the assistant message, `data-*` chunks reach `onData`, and
`finish`/`error` end it.

## Chunk helpers

`ai_text_stream(source, text_id=...)` maps a token source to
`text-start`/`text-delta`/`text-end` under one block id. Source items may
be plain `str` deltas, OpenAI `ChatCompletionChunk`-shaped objects or dicts
(`choices[0].delta.content`), or Anthropic `content_block_delta`-shaped
events (`delta.text`); everything else (usage, role, ping chunks) is
skipped. For several text blocks, concatenate multiple calls — each owns
one block id.

`ai_message_stream(parts, message_id=..., error_text=str)` adds the message
envelope: `start` → parts → `finish`; if `parts` raises, an
`{"type": "error", "errorText": ...}` chunk ends the message instead. Yield
raw chunk dicts for anything else the protocol supports (`reasoning-*`,
`tool-*`, `source-*`, `start-step`, ...).

## `data-*` custom parts

`ai_data_part(name, data, part_id=..., transient=...)` builds
`{"type": "data-<name>", "data": ...}` chunks for arbitrary structured data
(weather cards, tool progress, usage). Parts carrying `id` are reconciled
client-side; `transient=True` parts reach `onData` handlers without joining
the message.

## Through the router (alternative)

The helpers emit plain dicts, so a subscription can stream them too:

```python
# snippet: skip — excerpt; needs the router and llm_reply from your app
@router.subscription
async def chat(prompt: str) -> AsyncIterator[object]:
    async for chunk in ai_message_stream(ai_text_stream(llm_reply(prompt))):
        yield chunk  # or tracked(str(seq), chunk) for lastEventId resume
```

Honest cost: `useChat` cannot read tRPC's SSE framing, so this route needs
a custom `ChatTransport` on the frontend that wraps
`trpc.chat.subscribe(...)` and feeds each payload to the message-stream
reader — roughly 30–50 lines of TypeScript before anything renders. Pick it
when per-event `tracked` resume on the chat stream matters more than
zero-config `useChat` (the endpoint has no `lastEventId` mechanism).

## Limits

- GET and POST only; POST bodies must be JSON (the `useChat` shape).
- A factory raising before producing the iterable answers JSON `4xx`/`5xx`
  instead of opening the stream; mid-stream failures become `error` chunks
  followed by `[DONE]`, matching the official flush.
- Generated ids are process-sequential (`msg-N`, `text-N`); pass explicit
  `message_id`/`text_id`/`part_id` when ids must survive restarts.
- The endpoint is outside the router: no generated TS types, tRPC error
  envelope, or middleware/context — wrap the ASGI app for auth, or use the
  subscription route.
