# merged-routers

One app composed from several sub-routers — the trpc-rs-py equivalent of
tRPC's `appRouter = router({ posts: postsRouter, users: usersRouter })`.
Each domain lives in its own module (`posts.py`, `users.py`) as a plain
`Router`; the app composes them with `nest` (dotted prefix) and `merge`
(same level).

It demonstrates:

- **`app.nest("posts", posts_router())` / `app.nest("users", ...)`** —
  procedures serve under dotted paths (`posts.by_id`, `users.rename`);
- **nesting composes**: `posts.py` nests a comments router of its own, so
  `comments.for_post` is served as `posts.comments.for_post`;
- **`app.merge(meta_router())`** — root-level procedures (`health`) merge
  in unprefixed beside the nested domains;
- **generated TypeScript types**: `app-router.d.ts` is produced by
  `generate_types(router)` and nests the same way — `client.posts.by_id`,
  `client.posts.comments.for_post`.

```
posts.py            users.py            app.py
┌──────────────┐    ┌──────────────┐    ┌─────────────────────────┐
│ by_id        │    │ by_id        │    │ health        (merge)   │
│ create       │    │ rename       │ -> │ posts.*       (nest)    │
│ comments.* ──┼──┐ │              │    │ users.*       (nest)    │
└──────────────┘  │ └──────────────┘    └─────────────────────────┘
                  └─ nest("comments") inside posts
```

## Run from a clean checkout

From the repository root:

```bash
uv sync --no-install-project
uv run maturin develop --release
cd examples/merged-routers
uv run --project ../.. python app.py     # PORT=9000 to override (default 8000)
```

## Call it with the official tRPC client

```ts
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import type { AppRouter } from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

await client.posts.create.mutate({ title: "First post" });
await client.posts.by_id.query({ post_id: 1 });
await client.posts.comments.for_post.query({ post_id: 1 });
await client.users.rename.mutate({ user_id: 1, name: "Ada Lovelace" });
```

Regenerate the types after editing any sub-router:

```bash
uv run python examples/merged-routers/generate_types.py
```

## Smoke test

```bash
./examples/merged-routers/smoke.sh
```

The smoke test starts the app on a free port, calls the merged root
procedure, a query and mutation from each nested domain, the two-level
`posts.comments.*` path, and asserts `NOT_FOUND` produces HTTP 404 with the
official error envelope. It also verifies that the committed
`app-router.d.ts` matches the app. No secrets, no network beyond localhost.
