# Feature Matrix

Generated from test results so the matrix cannot drift (MISSION.md section 14).
Until the generator lands (milestone 0.2.0), cells marked **planned** are
aspirational and each will link to the test that proves it.

Rows: features. Columns: FastAPI, Starlette, Flask, Django sync, Django async,
standalone server, official `@trpc/server`, FastAPI + OpenAPI codegen.

| Feature | FastAPI | Starlette | Flask | Django sync | Django async | Standalone | @trpc/server | FastAPI + OpenAPI codegen |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Queries | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | planned | planned | planned | planned | supported | supported |
| Mutations | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | planned | planned | planned | planned | supported | supported |
| Batching | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | planned | planned | planned | planned | supported | not supported |
| Subscriptions (SSE) | planned | planned | planned | planned | planned | planned | supported | not supported |
| AI streaming helpers | planned | planned | planned | planned | planned | planned | not supported | not supported |
| Stream cancellation | planned | planned | planned | planned | planned | planned | supported | not supported |
| Middleware | planned | planned | planned | planned | planned | planned | supported | supported |
| Context | planned | planned | planned | planned | planned | planned | supported | partial |
| Error codes | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) | planned | planned | planned | planned | supported | partial |
| Typed errors | planned | planned | planned | planned | planned | planned | not supported | not supported |
| Input validation | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) ([validator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_validation.py); Pydantic v2 for annotated handlers) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) ([validator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_validation.py); Pydantic v2 for annotated handlers) | planned | planned | planned | planned | supported | supported |
| Output validation | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) ([validator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_validation.py); annotated handlers) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_asgi.py) ([validator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_validation.py); annotated handlers) | planned | planned | planned | planned | supported | supported |
| Transformers (superjson) | planned | planned | planned | planned | planned | planned | supported | not supported |
| File uploads | planned | planned | planned | planned | planned | planned | partial | supported |
| CORS | planned | planned | planned | planned | planned | planned | planned | supported |
| CSRF | planned | planned | planned | planned | planned | planned | planned | planned |
| Rate limiting | planned | planned | planned | planned | planned | planned | planned | planned |
| Tracing | planned | planned | planned | planned | planned | planned | planned | planned |
| TS codegen | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/clients/ts-tests/src/index.ts) (same Router codegen for every framework; [generator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_codegen.py)) | [supported](https://github.com/Seyamalam/trpc-rs-py/blob/main/clients/ts-tests/src/index.ts) (same Router codegen for every framework; [generator tests](https://github.com/Seyamalam/trpc-rs-py/blob/main/tests/test_codegen.py)) | planned | planned | planned | planned | not needed | supported |
| Live type sync | planned | planned | planned | planned | planned | planned | not supported | not supported |
| Zod codegen | planned | planned | planned | planned | planned | planned | not needed | not supported |
| OpenAPI/REST output | planned | planned | planned | planned | planned | planned | not supported | supported |
| Playground | planned | planned | planned | planned | planned | planned | not supported | not supported |
| Node migration link | planned | planned | planned | planned | planned | planned | supported | not supported |
| Python client | supported | supported | supported | supported | supported | supported | not supported | not supported |
