# 0015: Procedure timeout and SSE limit coverage

## Status

Accepted

## Context

MISSION.md section 7 lists six enforced, configurable limits. On review
for the 0.8.0 milestone, four already exist:

- `max_body_size`, `max_batch_size`, `max_json_depth` — Rust protocol
  layer, tested.
- `max_sse_event_size` — existing mount kwarg capping one encoded SSE
  frame (default 1 MiB); oversized events get a serialized-error frame
  and clean close. Enforced in `asgi.py`'s stream pump; tested in
  `test_subscriptions.py`.

Two were missing or needed review:

- **procedure timeout** — a hung handler pins a worker/task
  indefinitely. Genuinely absent.
- **subscription count per connection** — unbounded concurrent streams.
  Reviewed below: structurally bounded, no new knob.
- **SSE buffer size per client** — covered by `max_sse_event_size`
  (per-frame cap) plus pull-based backpressure; no new knob.

## Decision

Add one new `Limits` field and document the structural bounds:

1. `procedure_timeout` (seconds, default `None` = off): the ASGI
   dispatch wraps each handler await in `asyncio.wait_for`; expiry
   raises `TRPCError(TIMEOUT)` → the existing `-32008`/HTTP 408
   envelope, so clients see a real tRPC code rather than a generic 500.
   Covers queries, mutations, and each procedure inside a batch
   independently. Sync handlers cannot be preempted mid-call (documented
   caveat — the bound is on *waiting*, not CPU burn; CPU-bound handlers
   belong off the event loop). Subscription generators are excluded
   (long-lived by design; `sse_ping_interval` bounds idle time and
   disconnect cancellation handles cleanup).
2. Per-connection subscription count is **structurally bounded**: the
   transport is HTTP+SSE — one request carries at most `max_batch_size`
   subscription procedures (batched subscriptions share the batch cap),
   and HTTP/1.1 gives one stream per connection. The standalone server
   is HTTP/1.1; HTTP/2 multiplexing is an ASGI-server concern, not ours.
   Rationale recorded in the threat model rather than duplicated as a
   knob equivalent to `max_batch_size`.
3. Per-client SSE buffering is **structurally bounded**: generators are
   pull-based (the loop only reads when `send()` completes) and the
   standalone bridge has a 16-chunk channel; the residual surface —
   per-frame size — is already closed by `max_sse_event_size`.

## Consequences

- `Limits` gains one optional field; default keeps current behavior.
- The timeout wraps the *handler* call only — not middleware `next()`
  (avoids double-counting middleware time).
- Docstrings, `docs/limits.md`, and the threat model get the semantics;
  `tests/test_limits.py` covers the knob.
- ADR scope was corrected during implementation review:
  `max_sse_message_size` was found to already exist as
  `max_sse_event_size`; the ADR was rewritten to reflect that (this
  note is the audit trail).
