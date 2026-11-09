# Typed errors

Procedures declare the errors they can raise, and the generated TypeScript
exposes them as a **discriminated union** — the client can `switch` on
`error.data.code` and get fully typed payload fields. The wire format is the
official tRPC v11 error envelope, so any official client reads the errors
exactly like a Node server's `errorFormatter` output.

## Declaring errors

Subclass `TRPCError`, set the `code` and `data_model` class attributes, and
list the class in the procedure's `errors=[...]`:

```python
from pydantic import BaseModel
from trpc_rs_py import Router, TRPCError, TRPCErrorCode


class PostRef(BaseModel):
    post_id: int


class PostNotFound(TRPCError):
    code = TRPCErrorCode.NOT_FOUND
    data_model = PostRef


class RateLimitInfo(BaseModel):
    retry_after: float


class RateLimited(TRPCError):
    code = TRPCErrorCode.TOO_MANY_REQUESTS
    data_model = RateLimitInfo


router = Router()


@router.query(errors=[PostNotFound, RateLimited])
def post_by_id(post_id: int) -> str:
    raise PostNotFound("no such post", data=PostRef(post_id=post_id))
```

Rules enforced at decoration time (and again at generation):

- Every entry must be a `TRPCError` subclass.
- `code` must be a `TRPCErrorCode` — all 21 official v11 codes are available,
  including `TOO_MANY_REQUESTS` (HTTP 429) for rate limiting.
- `data_model` must be a Pydantic `BaseModel` subclass or `None`.

Declaration is a **type contract, not a runtime gate**: undeclared
`TRPCError`s still serialize normally, and unexpected exceptions still map to
`INTERNAL_SERVER_ERROR` (message redacted in production).

## Wire format

A declared error raised with a Pydantic `data` model serializes into the
official envelope — fields merge into `error.data` next to `code`,
`httpStatus`, and `path`:

```json
{
  "error": {
    "message": "no such post",
    "code": -32004,
    "data": {
      "code": "NOT_FOUND",
      "httpStatus": 404,
      "path": "post_by_id",
      "post_id": 7
    }
  }
}
```

This is byte-for-byte what a Node `errorFormatter` returning
`{...shape.data, post_id}` produces. Notes:

- **Reserved keys cannot be overridden.** `code`, `httpStatus`, `path`, and
  `stack` are dropped from user payloads — the envelope invariants win.
- **Non-mapping payloads** (a `data` value that is not a dict/model) land
  under `error.data.data`.
- **REST errors** carry the same payload: `{"error": {"code", "message",
  "data": {...}}}`.
- **Subscriptions** stream failures as `event: serialized-error` with the
  same flat shape.

## Generated TypeScript

The generated `app-router.d.ts` emits `AppRouterErrorShape` — one union
member per declared `(code, data_model)` pair, plus a default member whose
`code` excludes every declared code:

```ts
export type AppRouterErrorShape =
  | TRPCErrorShape<{
      code: Exclude<TRPC_ERROR_CODE_KEY, "NOT_FOUND" | "TOO_MANY_REQUESTS">;
      httpStatus: number;
      path?: string;
      stack?: string;
    }>
  | TRPCErrorShape<
      { code: "NOT_FOUND"; httpStatus: 404; path?: string; stack?: string } &
        PostRef
    >
  | TRPCErrorShape<
      {
        code: "TOO_MANY_REQUESTS";
        httpStatus: 429;
        path?: string;
        stack?: string;
      } & RateLimitInfo
    >;
```

`AppRouterRootTypes.errorShape` points at the union. A procedure with
declared errors gets its own restricted union in `$types.errorShape`;
undeclared procedures keep the router-wide union — the same shape a Node app
with a global `errorFormatter` produces.

## Client usage

`switch (error.data.code)` narrows to exactly the declared member — the
payload fields are directly accessible:

```ts
import { isTRPCClientError } from "@trpc/client";

try {
  await client.post_by_id_or_fail.query({ post_id: 7 });
} catch (error) {
  if (!isTRPCClientError<AppRouter>(error)) throw error;
  switch (error.data?.code) {
    case "NOT_FOUND":
      console.log(error.data.post_id); // number — fully typed
      break;
    case "TOO_MANY_REQUESTS":
      console.log(error.data.retry_after); // number
      break;
    default:
      // Undeclared codes: error.data is the default shape.
      break;
  }
}
```

Two declared errors sharing one code both appear in the union;
`error.data.code` alone does not distinguish them — discriminate further on
a model field in that case.

See `docs/decisions/0009-typed-errors.md` for the design record.
