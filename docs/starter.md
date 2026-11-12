# Starter template

`trpc-py init` scaffolds a working project from a bundled template — no
boilerplate to wire by hand. The default, `nextjs-fastapi`, is a
Next.js + FastAPI monorepo where the frontend already calls Python
through the official `@trpc/client` with end-to-end types.

```bash
trpc-py init my-app          # creates ./my-app
trpc-py init my-app --dir app  # or an explicit target directory
```

The name becomes the directory and is substituted into `{{name}}`
placeholders (project titles, `package.json` name). The target directory
must be empty. Pick a different stack with `--template`:

| Template | What you get |
| --- | --- |
| `nextjs-fastapi` (default) | FastAPI backend + Next.js frontend monorepo |
| `fastapi-only` | Backend only — `uvicorn server:app` |
| `flask-only` | Backend only — `python server.py` (`mount_wsgi` + `wsgi_from_asgi`) |
| `django-only` | Backend only — `python server.py` (sync view + `asgi_view` for REST) |

## What `nextjs-fastapi` lays down

```
my-app/
├── server.py            # FastAPI app + router
├── README.md            # generated run steps
└── web/
    ├── app/page.tsx     # calls trpc.greeting.query({ name: "world" })
    ├── app/layout.tsx
    ├── lib/trpc.ts      # createTRPCClient<AppRouter> → :8000/trpc
    ├── app-router.d.ts  # generated router types
    ├── package.json
    └── tsconfig.json
```

`server.py` ships three procedures so every call kind is exercised on
first boot:

- `greeting` — a query opted into REST via
  `rest=RestRoute("GET", "/greeting/{name}")`
- `create_user` — a mutation returning a Pydantic `User` model (which
  becomes a named TypeScript interface)
- `ticks` — a subscription yielding three values over SSE

and mounts both surfaces:

```python
app = FastAPI(title="{{name}}")
app.mount("/trpc", mount(router))
app.mount("/api", mount_rest(router, title="{{name}}", version="0.1.0"))
```

The generated `web/lib/trpc.ts` points a `httpBatchLink` at
`http://localhost:8000/trpc` and imports `AppRouter` from
`web/app-router.d.ts` — that file is where your types land.

## Run it

```bash
# Python deps (uv or plain pip both work)
uv pip install trpc-rs-py fastapi uvicorn

# Backend on :8000
uvicorn server:app --reload

# Live type sync — regenerates web/app-router.d.ts on every Python save
trpc-py dev --app server:router --out web/app-router.d.ts

# Frontend on :3000 (pnpm or npm — the template ships package.json only)
cd web && pnpm install && pnpm dev
```

`trpc-py dev` and `uvicorn --reload` are two commands; merge them with
`--server`, which starts the ASGI app under `uvicorn --reload` watching
the same directories:

```bash
trpc-py dev --app server:router --out web/app-router.d.ts --server server:app
```

Open `http://localhost:3000` — `page.tsx` renders the `greeting` result
fetched from Python. The same procedure is also a REST endpoint:
`curl http://localhost:8000/api/greeting/world`, with the OpenAPI
document at `/api/openapi.json` and a docs UI at `/api/docs` (see
[REST + OpenAPI](rest.md)).

## The dev loop

1. Edit `server.py` — add a parameter, rename a field, add a procedure.
2. Save. `trpc-py dev` rewrites `web/app-router.d.ts` in well under a
   second (see [live type sync](live-type-sync.md) for the loop and the
   last-good-on-error behavior).
3. TypeScript picks it up: `page.tsx` autocompletes the new procedure,
   and a breaking change is a type error in the editor — before any
   request is made.

For one-off regeneration without the watcher (CI, packaging):

```bash
trpc-py codegen ts --app server:router --out web/app-router.d.ts
```

Full flag reference: [CLI — `trpc-py init`](cli.md#trpc-py-init).
