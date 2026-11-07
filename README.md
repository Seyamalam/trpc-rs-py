# trpc-rs-py

**The Python backend for your tRPC app.** Define procedures in Python, call
them from your TypeScript app with the official `@trpc/client` — with full
end-to-end types that update live as you edit Python.

```python
# FastAPI (or Starlette, Flask, Django)
from trpc_rs_py import Router

router = Router()

@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"

@router.mutation
async def create_post(title: str) -> dict:
    return {"id": 1, "title": title}
```

```ts
// TypeScript — full types, no codegen step in your inner loop
const greeting = await trpc.greeting.query({ name: "Ada" });
//    ^? string
```

> **Status: pre-release foundation (0.1.0).** The core protocol lands in
> 0.2.0; the launch version (live type sync, AI streaming, OpenAPI output,
> starter templates) is 0.3.0. See [`MISSION.md`](MISSION.md) for the full
> roadmap and [`docs/feature-matrix.md`](docs/feature-matrix.md) for feature
> status.

## Why this exists

- **Full end-to-end types** — procedures are typed from Python type hints via
  Pydantic; generated `.d.ts` types update live as you edit Python.
- **AI streaming first-class** — an `async def` generator becomes a typed tRPC
  subscription over SSE; client disconnect cancels the generator (no wasted
  LLM tokens).
- **No lock-in** — the same router can serve REST + an OpenAPI 3.1 document.
- **Fast** — request parsing, routing, batching, and serialization run in a
  Rust core; the Python adapter adds near-zero overhead over a plain JSON view.

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
