# trpc-rs-py

**The Python backend for your tRPC app.** Define procedures in Python, call
them from your TypeScript app with the official `@trpc/client` — with full
end-to-end types that update live as you edit Python.

```python
# FastAPI — any ASGI framework works; Flask and Django adapters are planned
from collections.abc import AsyncIterator

from trpc_rs_py import Router, tracked

router = Router()

@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"

@router.mutation
async def create_post(title: str) -> dict:
    return {"id": 1, "title": title}

@router.subscription
async def events() -> AsyncIterator[dict]:
    async for event in stream():
        yield tracked(str(event.id), event.payload)  # resumable SSE ids
```

```ts
// TypeScript — full types, no codegen step in your inner loop
const greeting = await trpc.greeting.query({ name: "Ada" });
//    ^? string
```

> **Status: 0.2.0 released; 0.3.0 in development.** Queries, mutations,
> batching, SSE subscriptions, live type sync (`trpc-py dev`), REST +
> OpenAPI output, and starter templates (`trpc-py init`) are in; the 0.3.0
> launch adds AI streaming helpers and examples. See [`MISSION.md`](MISSION.md)
> for the full roadmap and [`docs/feature-matrix.md`](docs/feature-matrix.md)
> for feature status.

## Quickstart

```sh
pip install trpc-rs-py fastapi uvicorn
trpc-py init my-app && cd my-app
uvicorn server:app --reload &
trpc-py dev --app server:router --out web/app-router.d.ts
```

Then `cd web && npm install && npm run dev` — the Next.js app calls Python
with full types that update live on every Python save.

## Why this exists

- **Full end-to-end types** — procedures are typed from Python type hints via
  Pydantic; generated `.d.ts` types update live as you edit Python.
- **AI streaming first-class** — an `async def` generator becomes a typed tRPC
  subscription over SSE; client disconnect cancels the generator (no wasted
  LLM tokens).
- **No lock-in** — the same router can serve REST + an OpenAPI 3.1 document.
- **Fast** — request parsing, routing, batching, and serialization run in a
  Rust core; the Python adapter adds near-zero overhead over a plain JSON view
  ([benchmark results](docs/benchmarks.md)).

## Development

```sh
uv sync --no-install-project   # dev dependencies
uv run maturin develop --release  # build + install the extension
just ci-fast                   # quick gate
just ci                        # full gate
```

See [`AGENTS.md`](AGENTS.md) for contributor/agent rules and
[`CONTRIBUTING.md`](CONTRIBUTING.md) to get started.

## License

MIT — see [`LICENSE`](LICENSE).
