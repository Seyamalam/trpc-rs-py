# Migrating from Node

`@trpc-rs-py/link` moves a Node tRPC app to Python one procedure at a time —
no flag-day rewrite, no server-side proxy. A client-side link routes each
operation to the Python or Node server by path (ADR
[0008](decisions/0008-node-migration-link.md)).

```bash
pnpm add @trpc-rs-py/link @trpc/client
```

## Setup

```ts
import { createTRPCClient } from "@trpc/client";
import { migrationLink, type MergedAppRouter } from "@trpc-rs-py/link";

import type { AppRouter as NodeRouter } from "./server"; // typeof appRouter
import type { AppRouter as PyRouter } from "./app-router"; // trpc-py codegen ts

type AppRouter = MergedAppRouter<NodeRouter, PyRouter>;

const client = createTRPCClient<AppRouter>({
  links: [
    migrationLink({
      nodeUrl: "http://localhost:3000",
      pythonUrl: "http://localhost:8000/trpc",
      procedures: ["posts.byId"],
    }),
  ],
});
```

## Routing rules

An operation goes to **Python** when:

- its path starts with the `prefix` (default `"py"`, i.e. `py.*`), or
- its path appears in the `procedures` list.

Everything else goes to **Node**. Paths are never rewritten — what the
client calls is what each server sees, so a procedure keeps its original
path after moving (list it in `procedures`), or can move under `py.` when
you want the frontend to opt into the new location.

Two migration styles, both supported at once:

| Style | Python path | Link option | Frontend change |
|---|---|---|---|
| Same path | `posts.byId` (e.g. `router.nest("posts", r)` + `name="byId"`) | `procedures: ["posts.byId"]` | none |
| Namespaced | `py.posts_byId` (`router.nest("py", r)`) | `prefix: "py"` (default) | rename call site |

Queries and mutations go through `httpBatchLink` on the chosen side;
subscriptions go through `httpSubscriptionLink`, so a moved subscription
streams from Python over SSE with no Node hop. `fetch`, `headers`, and
`EventSource` options are forwarded to the underlying links.

## Merged types

`MergedAppRouter<TNode, TPy>` merges `_def.record` of the two official
`Router` types: records deep-merge and **Python wins conflicts** — it is the
destination of the migration. `ctx`, `meta`, and `transformer` come from
the Node router; `errorShape` widens to a union of both sides so typed
Python errors stay discriminated on `error.data.code`.

```ts
type AppRouter = MergedAppRouter<NodeRouter, PyRouter>;
```

## Endgame

When the last procedure has moved, delete the link, point the client at
`pythonUrl` alone, and retire Node.

A runnable end-to-end version of this flow — real Node and Python servers
plus a merged client — lives in
[`examples/migrate-from-node`](https://github.com/Seyamalam/trpc-rs-py/tree/main/examples/migrate-from-node).
