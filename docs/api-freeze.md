# API freeze — reviewed for 1.0.0-beta.1

Reviewed 2026-11-12 against `main` (`abda30a`). This document enumerates
the public surface and the stability commitment that takes effect at
`1.0.0-beta.1`: **no breaking changes to the surfaces below without a
major version bump.** New parameters may be added only as keyword-only
with defaults that preserve current behavior.

## Frozen at 1.0.0

### Package root (`trpc_rs_py`)

`Router`, `Procedure`, `ProcedureKind`, `RestRoute`, `mount`, `attach`,
`mount_wsgi`, `attach_flask`, `wsgi_from_asgi`, `mount_rest`,
`generate_types`, `generate_zod`, `generate_openapi`, `merge_openapi`,
`Limits`, `TRPCError`, `TRPCErrorCode`, `data_code_for`,
`RequestContext`, `ContextFactory`, `Tracked`, `tracked`,
`build_input_validator`, `build_output_validator`, `ASGIApp`,
`__version__`.

### Adapter entry points (all frozen)

`mount`, `attach` (ASGI); `mount_wsgi`, `attach_flask` (WSGI);
`trpc_view`, `trpc_view_async`, `asgi_view`, `asgi_view_async`,
`urlpatterns` (Django); `serve` (standalone). Common kwargs that are
part of the contract: `production`, `context_factory`, `middleware`,
`limits`, `sse_ping_interval`, `max_sse_event_size`, `base_path`,
`response_headers`, `lifespan` handling on `attach`.

### Behavioral contract (frozen semantics)

- Wire envelopes: official tRPC v11 `result`/`error` shapes, batch
  `?batch=1` semantics, GET query input encoding, SSE subscription
  frames (`data:`/`id:`/`serialized-error`/`return`), production error
  redaction.
- `ctx` injection rule: a leading parameter literally named `ctx` opts
  in and is excluded from validation/codegen.
- Middleware: outermost-first order, `Call`/`Next`, `next(ctx=)`
  replacement.
- `Limits` fields and their enforcement points.
- Error code mapping (`TRPCErrorCode` ↔ wire code ↔ HTTP status).

### Submodules

`trpc_rs_py.middleware` (`Call`, `Next`, `run_middleware`, `logging_middleware`,
`timing_middleware`, `rate_limit`, `require_auth`, `tracing_middleware`),
`trpc_rs_py.cors.cors`, `trpc_rs_py.csrf.csrf_protect`,
`trpc_rs_py.streaming` (subscription helpers + `ai_text_stream`,
`ai_message_stream`, `ai_data_part`, `ai_sdk_endpoint`, `Tracked`,
`tracked`),
`trpc_rs_py.testing` (`TestClient`/`AsyncTestClient` + framework wrappers),
`trpc_rs_py.client` (`create_client`, `create_async_client`, `Client`,
`AsyncClient`, `TrpcClientError`, `SubscriptionEvent`),
`trpc_rs_py.serve.serve`, `trpc_rs_py.pyclient.generate_python_client`.

### CLI

`trpc-py` subcommands: `init`, `dev`, `routes`, `check`, `serve`,
`codegen {ts, zod, openapi, py-client}`. Flags are additive-only; JSON
output shapes (`--json`) are frozen once documented.

## Explicitly unstable (documented, may change without a major bump)

- `_core` — the extension module is private by name and convention;
  consumers must not import it directly.
- `trpc_rs_py.dev` internals and file-watching behavior details.
- `wsgi_from_asgi` bridge internals beyond the public return type.
- The generated file formats' internal helpers (generated `.d.ts`,
  `.py` clients are stable *output*, but `pyclient.py`'s internal
  rendering helpers are private).
- `mount_rest` behavior details beyond documented semantics (it does
  not enforce `procedure_timeout` — see `limits.md`).

## Review notes

- The surface is intentionally flat at the package root — everything a
  FastAPI user needs imports from `trpc_rs_py`. Helper modules keep
  deeper APIs out of the root namespace.
- `RestRoute`/`Procedure`/`ProcedureKind` are exposed because adapters
  and codegen accept them; their construction API (decorator, `merge`,
  `nest`) is frozen alongside.
- `Tracked`/`tracked` is the public id-resume API; frame ids remain
  `msg-N`-style documented values.
