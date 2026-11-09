# ADR 0008: Node tRPC migration link — client-side split link

Status: Accepted
Date: 2026-11-09

## Context

MISSION section 2.2 requires a way for teams with an existing Node.js tRPC
app to move procedures to Python one at a time without rewriting the
frontend, and asks for an ADR comparing a client-side link with a
server-side proxy in the Node router.

## Options

1. **Client-side link.** An npm package (`@trpc-rs-py/link`) providing a
   `TRPCLink` that inspects each operation's path and forwards it to either
   the Python server or the Node server, plus a type helper merging the Node
   `AppRouter` with the generated Python `AppRouter` type. The Node server
   is not in the request path for migrated procedures.
2. **Server-side proxy in the Node router.** The Node router keeps
   placeholder procedures that forward over HTTP to Python. The frontend
   keeps one URL and needs no client changes, but every migrated call pays a
   Node→Python hop, Node stays in the hot path forever, error envelopes must
   be forwarded faithfully, subscriptions cannot be proxied through the
   Node procedure machinery (an SSE stream per hop), and Node remains a
   runtime dependency for migrated traffic — which is the opposite of
   migrating *off* it.

## Decision

Client-side link, shipped as the npm package `@trpc-rs-py/link`.

- `migrationLink({ nodeUrl, pythonUrl, prefix?, procedures? })` returns a
  `TRPCLink` that routes an operation to Python when its path starts with
  `<prefix>.` (default `py`) or appears in `procedures`.
- **No path rewriting.** The wire path is sent to the Python server exactly
  as issued. Python procedures that should live under the prefix are nested
  there in the Python router (`router.merge(sub, "py")`-style), so
  `py.posts_create` is a real procedure path — the link routes, it does not
  rewrite. `procedures` covers the second story: an existing path moved to
  Python with the frontend untouched.
- **Type helper `MergedAppRouter<TNode, TPy>`** deep-merges the two router
  records so `createTRPCClient<MergedAppRouter<...>>` resolves inputs and
  outputs across both; Python wins on conflicting paths (it is the
  destination of the migration). Both are real `Router<RootTypes, Record>`
  types, so the merge happens on `_def.record` and re-wraps in `Router`.
- Subscriptions route through the same link, so a migrated subscription
  streams from Python over SSE with no Node hop.
- The package version moves in lockstep with the Python package
  (`scripts/release.sh` already bumps `packages/*/package.json`).

## Consequences

- The frontend changes its links array once; migrated paths then leave Node
  entirely.
- The server-side proxy remains possible userland code (a Node procedure
  calling `fetch`); it is not shipped or supported.
- A Node router type is required to merge against; the helper accepts
  `AnyTRPCRouter` so any official server AppRouter qualifies.
