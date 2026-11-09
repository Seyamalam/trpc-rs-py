# Draft announcement — trpc-rs-py 0.3.0

Adapt the first line per venue. Lead with the pain, not the Rust.

---

## Hacker News (Show HN)

**Show HN: trpc-rs-py – a tRPC v11 backend for Python, with live TypeScript types**

We built a tRPC server for Python backends so a TypeScript frontend can call
Python through the official `@trpc/client` — queries, mutations, batching,
and SSE subscriptions, wire-compatible with `@trpc/server` v11 (verified in
CI against the real client on live FastAPI/Starlette servers).

The three things that make it more than "FastAPI + OpenAPI codegen":

* **Live type sync.** `trpc-py dev` watches your Python and regenerates the
  `AppRouter` .d.ts on every save — a Rust `notify` watcher, debounced,
  atomic writes, under a second. Edit a field, and your TS code turns red
  (or green) immediately.
* **AI streaming as a first-class primitive.** An `async def` generator is
  a typed subscription over SSE with resumable `lastEventId`, keep-alives,
  and — the part that saves money — client disconnect *cancels the
  generator*. No more LLM streams running to completion after the tab closes.
* **No lock-in.** The same router opts procedures into REST + an OpenAPI 3.1
  document (`rest=RestRoute("GET", "/posts/{id}")`), validated against
  openapi-spec-validator and exercised by an openapi-typescript client.

`trpc-py init` scaffolds a Next.js + FastAPI starter in one command.

The protocol core is Rust (PyO3) — that's why it beats a plain FastAPI JSON
view by ~39% in our benchmarks — but the pitch is the workflow, not the
runtime: this is for teams whose frontend is already tRPC and whose backend
should be Python (AI apps especially).

Repo + docs: https://github.com/Seyamalam/trpc-rs-py

---

## r/Python

**[Project] trpc-rs-py: tRPC v11 for Python — live TS types, SSE streaming, OpenAPI out**

If your frontend team writes tRPC and your backend wants to be Python, this
is the missing piece. Official-`@trpc/client`-compatible server for
FastAPI/Starlette with a Rust protocol core.

Highlights: `.d.ts` regenerates on save (`trpc-py dev`), `async def`
generators become resumable SSE subscriptions with real disconnect
cancellation (ideal for LLM streaming), and opting a procedure into REST +
OpenAPI 3.1 is one decorator argument.

Would love feedback on the API shape — `rest=RestRoute(...)`, `tracked()`
events, and the `trpc-py` CLI are all new in 0.3.0.

https://github.com/Seyamalam/trpc-rs-py

---

## tRPC Discord (#general or #showcase)

Hey! Built a Python backend that speaks tRPC v11 wire format — the official
`@trpc/client` (incl. `httpSubscriptionLink`) works against it unchanged;
there's a live compatibility suite pinning that down.

Differentiators vs. codegen approaches: `trpc-py dev` live-regenerates
`AppRouter` types on Python save (<1s), and subscriptions get real
cancellation on disconnect for LLM streaming. Same router also serves
REST + OpenAPI 3.1 for non-TS clients.

Early days (0.3.0) — very open to feedback on procedure ergonomics and
what would unblock adoption for your stack.

https://github.com/Seyamalam/trpc-rs-py

---

## Before posting

- [ ] Record the two GIFs (`live-types.tape`, `ai-chat.tape`) and embed them.
- [ ] Confirm the benchmark numbers quoted match `docs/benchmarks.md`.
- [ ] Check `docs/feature-matrix.md` for anything marked "planned" that the
      post implies exists.
- [ ] Tag v0.3.0 and update the version line in each draft.
