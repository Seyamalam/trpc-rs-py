# Demo script: migrate-from-node

> **Status: pending.** The migration link (`packages/migration-link`,
> MISSION §2.2) is scheduled for 0.4.0. This script is written so it can be
> recorded as soon as that lands; the beats won't change.

**The shot:** an existing Node `@trpc/server` app keeps its frontend and its
generated types while the backend moves to Python — same procedures, same
wire, same client calls.

## Setup

A small Node tRPC app (`server.ts`, two procedures, `@trpc/client` web app)
and a Python router with the same procedure names and shapes.

## Narration beats

1. Show the Node app working end to end (one query, one mutation).
2. Install the migration link; point the frontend at the Python server
   while the Node server still runs — the link proxies unknown procedures
   to Node so migration is incremental, not a flag day.
3. Move `post.byId` to Python. Show the call now hitting Python (server
   log / response marker).
4. `trpc-py codegen ts` — the generated d.ts matches the old Node types;
   the frontend typechecks with zero changes.
5. Move the second procedure; drop the proxy. Node is gone; the frontend
   never changed.
6. Callout: same `@trpc/client`, same protocol — verified by the
   compatibility suite.
