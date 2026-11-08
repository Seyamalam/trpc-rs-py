# Input and output validation

The ASGI adapter (`mount` and `attach`) builds Pydantic v2 validators once for
procedures registered when the adapter is constructed. Parameter annotations
define the input object's fields, and the return annotation defines the output
schema. This validation applies to FastAPI, Starlette, and other ASGI apps.
Flask, Django, and standalone-server validation are not implemented yet.

## Input objects and defaults

For annotated parameters, send a JSON object keyed by the handler's Python
parameter names. Required parameters are required fields; Python defaults are
used when fields are omitted, including when the entire input is `null`:

```python
from trpc_rs_py import Router

router = Router()

@router.query
def greet(name: str, greeting: str = "Hello") -> str:
    return f"{greeting}, {name}!"
```

For example, `{ "name": "Ada" }` calls `greet(name="Ada", greeting="Hello")`.
Missing `name` is a `BAD_REQUEST`. Pydantic's default lax coercion applies;
for example, the string `"7"` can validate as an `int` value of `7`.

Unknown top-level input keys are rejected by default. If the handler accepts
`**kwargs`, extra keys are allowed and passed to the handler unchanged; they are
not validated. Unannotated parameters are also passed through unchanged (a
missing required unannotated parameter is still a `BAD_REQUEST`). A handler
with no parameters keeps the adapter's no-argument behavior.

Pydantic v2 types supported by the validators include nested `BaseModel`s,
enums, `Literal`s, unions, and `Annotated` constraints such as `Field(gt=0)`.
`Annotated` metadata is preserved. Nested models follow their own Pydantic
configuration for extra fields; Pydantic's default nested-model behavior may
ignore them, so configure nested models explicitly when rejection is required.

Input keys use Python parameter names, not Pydantic field aliases. If a
parameter is declared with `Field(alias="userName")`, `userName` is rejected as
an extra key; send `user_name` instead. Signature-derived top-level alias
keys are not currently supported. Nested `BaseModel` fields use their own
Pydantic configuration and alias behavior.

### Variadic parameters

For a handler whose only parameter is `*args`, a JSON list becomes positional
arguments, a scalar becomes one positional argument, and `null` (or absent
input) becomes zero arguments. The values pass through without per-item type
validation, including when `*args` has an annotation. For example:

```python
from typing import Any

from trpc_rs_py import Router

router = Router()

@router.query
def collect(*values: Any) -> list[Any]:
    return list(values)
```

Calling `collect` with `[1, "two"]` returns `[1, "two"]`; a scalar `5`
returns `[5]`, and `null` returns `[]`. This list/scalar/null mapping is for
`*args`-only handlers; it does not change ordinary object input handling.

## Output validation and errors

When a handler has a return annotation other than `None`, its output is
validated and serialized through a Pydantic `TypeAdapter` before it is sent.
Output validation or serialization failures become
`INTERNAL_SERVER_ERROR`; Pydantic details and procedure output are not included
in the error. In production mode the adapter returns the fixed message
`"Internal server error"`; in development it returns
`"Invalid output from procedure"`. A missing return annotation (or `-> None`)
disables output validation.

Input validation failures become `BAD_REQUEST`. Error messages use a fixed,
value-free description and omit submitted input, custom validator messages,
context, and arbitrary nested keys. For example, the client may receive
`"Invalid input: age: Invalid integer"`, but never the invalid submitted value.

## Validator lifetime

Validators are a snapshot of the router's procedures when `mount(router)` or
`attach(app, router)` is called. Register procedures before constructing the
adapter. If you register more procedures later, rebuild the adapter to ensure
they receive input and output validation; late registrations otherwise use the
legacy invocation checks without these validators.

## Limitations

- Validation is currently wired into the ASGI adapter only.
- Signatures without useful annotations do not get schema validation; their
  values pass through subject to invocation checks and top-level extra-key
  handling described above.
- `*args` elements and `**kwargs` extras are not validated.
- Field aliases are not accepted as wire keys; use Python parameter names.
- The implementation uses Pydantic v2's default lax coercion. Configure strict
  types or constraints in annotations/models when stricter validation is
  needed.
