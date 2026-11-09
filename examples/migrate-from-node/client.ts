// One client, two backends: migrationLink sends each call to Node or Python
// by path, and MergedAppRouter merges both router types so every procedure —
// migrated or not — is fully typed.
import { createTRPCClient } from "@trpc/client";
import { migrationLink, type MergedAppRouter } from "@trpc-rs-py/link";

import type { AppRouter as NodeRouter } from "./node-server.ts";
import type { AppRouter as PyRouter } from "./app-router.d.ts";

type AppRouter = MergedAppRouter<NodeRouter, PyRouter>;

const client = createTRPCClient<AppRouter>({
  links: [
    migrationLink({
      nodeUrl: process.env.NODE_URL ?? "http://127.0.0.1:3010",
      pythonUrl: process.env.PYTHON_URL ?? "http://127.0.0.1:8000/trpc",
      // posts.byId keeps its original path — routed to Python explicitly.
      procedures: ["posts.byId"],
      // py.* routes to Python automatically (default prefix).
    }),
  ],
});

const me = await client.users.me.query();
const byId = await client.posts.byId.query({ id: 2 });
const found = await client.py.posts_search.query({ q: "Python" });

console.log(JSON.stringify({ me, byId, found }, null, 2));
