# ai-chat

Streamed AI responses over tRPC subscriptions — the differentiator this
project exists for. `chat(prompt)` is an `async def` generator: each yielded
`tracked(id, ChatChunk)` becomes one SSE event the official
`@trpc/client` receives as `{id, data}`. Client disconnect cancels the
generator, so a cancelled request stops "LLM" work immediately.

## Run

```bash
uvicorn app:app --reload
```

```bash
# SSE stream (tRPC subscription over SSE)
curl -N "http://localhost:8000/trpc/chat?input=%7B%22prompt%22%3A%22hi%22%7D"

# Same router, REST + OpenAPI for non-TS clients
curl http://localhost:8000/api/models
open http://localhost:8000/api/docs
```

`app-router.d.ts` is generated (`generate_types.py`) — under `trpc-py dev` it
updates live as you edit `app.py`.

## Smoke test

```bash
./smoke.sh
```
