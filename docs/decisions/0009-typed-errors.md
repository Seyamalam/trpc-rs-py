# ADR 0009: Typed errors — declared error classes and the errorShape union

Status: Accepted
Date: 2026-11-09

## Context

MISSION section 2.6: procedures declare the errors they can raise
(`errors=[NotFound, RateLimited]`), the generated TypeScript gives the
client a discriminated union, and the wire format stays compatible with
the official tRPC error shape "using the error formatter mechanism".

Three sub-decisions: how error classes declare typed data, where extra
data lands in the wire envelope, and how the union is expressed in the
official type machinery.

## Options

1. **Data merged into `error.data` (official errorFormatter semantics).**
   On the wire, extra fields sit inside `error.data` next to `code` and
   `httpStatus` — exactly what a Node server's `errorFormatter` produces
   when it returns `{ ...shape.data, customField }`. Indistinguishable
   from a first-party Node server; the official client reads
   `error.data.<field>` the same way.
2. **Data nested under `error.data.payload`.** Safer against key
   collisions, but a Node errorFormatter does not produce this shape, so
   clients written against a migrated Node app would diverge.

For the class API: a subclass of `TRPCError` with class attributes is the
minimal declarative surface (versus generics over a data type or a
decorator registry).

## Decision

- **Error classes are `TRPCError` subclasses** with two class attributes:
  `code` (a `TRPCErrorCode`) and `data_model` (a Pydantic model or
  `None`). `TRPCError.__init__` treats the `code` kwarg as optional and
  falls back to the class attribute, defaulting to
  `INTERNAL_SERVER_ERROR` on the base class.
- **Declaration:** `@router.query(errors=[PostNotFound, RateLimited])`
  stores the classes on `Procedure.errors`. Declaration is a type
  contract, not a runtime gate — undeclared `TRPCError`s still serialize
  normally (the union always includes the default shape).
- **Wire format:** when a raised `TRPCError` carries a Pydantic model in
  `data`, its `model_dump(mode="json")` fields are merged into
  `error.data` alongside `code`/`httpStatus`/`path`. Reserved keys
  (`code`, `httpStatus`, `path`, `stack`) cannot be overridden — the
  envelope invariants win. `CallResult.error` grows an optional payload
  argument plumbed through the Rust `TrpcError` as an `extra` object.
- **Type generation:** `AppRouterRootTypes.errorShape` becomes
  `AppRouterErrorShape`. Each declared error contributes a member
  `TRPCErrorShape<{ code: "CODE"; httpStatus: N; path?; stack? } &
  DataModel>`, and the default member's `data.code` is narrowed via
  `Exclude<TRPC_ERROR_CODE_KEY, ...declared codes>` — so
  `switch (error.data.code)` narrows to exactly one member and declared
  fields are directly accessible. (A plain `DefaultErrorShape | E`
  union leaves the default branch in every case, making declared fields
  unreachable without `in` checks — the exclusion form is what makes
  the promise "switch on the error code with full types" true.) The
  slight optimism — a handler raising an undeclared bare error with a
  declared code would not carry the model fields — is exactly the
  contract `errors=[...]` states.
- **Per-procedure narrowing:** `$types.errorShape` on a declared
  procedure emits its own restricted union (default-minus-its-codes plus
  its members); undeclared procedures emit `AppRouterErrorShape`, the
  same router-wide shape a Node app with a global `errorFormatter`
  produces. `TRPCClientError<AppRouter>` resolves through
  `RootTypes.errorShape`; `inferProcedureErrorShape` honours the
  per-procedure member for procedure-level inference.

## Consequences

- Frontend `switch (error.data?.code)` narrows to exactly the declared
  member for that code — model fields are directly accessible. All
  undeclared codes narrow to the default-data member.
- Two procedures declaring the same code with different data models both
  appear in the union; `data.code` alone does not distinguish them, so
  codegen emits both members (clients can discriminate further on a
  model field).
- `error.data` stays an opaque escape hatch: non-model dict payloads are
  serialized but never enter the type union.
