# Getting started

Define procedures in Python, serve them from your framework, and call them
from TypeScript with the official `@trpc/client` — fully typed end to end.

Requires Python 3.10+ and, for this walkthrough, FastAPI + uvicorn. The same
router also mounts on [Starlette](guides/starlette.md),
[Flask](guides/flask.md), and [Django](guides/django.md), or runs without a
framework via [`trpc-py serve`](standalone.md).

## Install

```bash
pip install trpc-rs-py fastapi uvicorn
# or with uv:
uv add trpc-rs-py fastapi uvicorn
```

The wheel is a prebuilt binary — no Rust toolchain is needed on supported
platforms (Linux x86_64/aarch64, macOS x86_64/arm64, Windows x86_64). On
other platforms `pip` builds from source with maturin, which requires a
Rust toolchain. Working from a git checkout, build the extension with
`uv run maturin develop --release`.

## A first router

`server.py` — one query, one mutation:

```python
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_rs_py import Router, mount


class Post(BaseModel):
    id: int
    title: str


router = Router()


@router.query
def greeting(name: str) -> str:
    return f"Hello {name}!"


@router.mutation
def create_post(title: str) -> Post:
    return Post(id=1, title=title)


app = FastAPI()
app.mount("/trpc", mount(router))
```

Run it:

```bash
uvicorn server:app --reload
```

The procedures are live at `http://127.0.0.1:8000/trpc`. A query is a GET
(or POST) call, a mutation is POST:

```bash
curl "http://127.0.0.1:8000/trpc/greeting?input=%7B%22name%22%3A%22Ada%22%7D"
# {"result":{"data":"Hello Ada!"}}
```

The playground at `http://127.0.0.1:8000/trpc/panel` lists the procedures
with generated input forms — handy while developing, off in production
(see [playground](playground.md)). `trpc-py routes --app server:router`
lists the same procedures in the terminal.

## Generate the TypeScript types

```bash
trpc-py codegen ts --app server:router --out app-router.d.ts
```

`--app` takes a `module:attribute` import string pointing at the `Router`.
The generated `.d.ts` exports an `AppRouter` built from your Python type
hints and Pydantic models — drop it anywhere in your TypeScript project.

During development, prefer live type sync — regenerate on every save:

```bash
trpc-py dev --app server:router --out web/app-router.d.ts
```

See [TypeScript codegen](codegen.md) for the mapping table and the CLI
reference for [`trpc-py dev` flags](cli.md).

## Call it from TypeScript

```bash
npm install @trpc/client @trpc/server
```

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

const hello = await client.greeting.query({ name: "Ada" });
//    ^? string
const post = await client.create_post.mutate({ title: "First" });
//    ^? Post — the Pydantic model became an exported interface
```

`@trpc/server` is a type-only dependency — the generated file imports
types from it; nothing from it ships in your bundle. Every procedure is
typed: wrong inputs, unknown paths, and `.query` on a mutation are
compile-time errors. See [the TypeScript client](ts-client.md) for
subscriptions, React hooks, batching, and error handling.

## Scaffold a starter instead

`trpc-py init` lays down a working project — server, generated types, and
a frontend already wired:

```bash
trpc-py init my-app && cd my-app   # Next.js + FastAPI template
trpc-py init api --template fastapi-only   # backend-only variants exist
```

## Next steps

- [Concepts](concepts.md) — routers, procedures, context, middleware,
  errors, batching, subscriptions in one page.
- Framework guides: [FastAPI](guides/fastapi.md),
  [Starlette](guides/starlette.md), [Flask](guides/flask.md),
  [Django](guides/django.md).
- [Adapters](adapters.md) — how the same router serves on every framework.
- [Deployment](deployment.md) — `production=True`, workers, proxies.
