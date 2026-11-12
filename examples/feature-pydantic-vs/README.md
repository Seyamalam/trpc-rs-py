# feature-pydantic-vs

The **same signup mutation** implemented twice on one router — once with
Pydantic-declared input, once with hand-rolled checks — so the two
validation styles can be compared over the wire and in the generated
TypeScript.

- `pydantic.signup(form: SignupForm)` — the adapter builds a
  `build_input_validator` from the handler signature, so coercion and
  constraint checks run before the handler executes. `form` arrives as a
  validated `SignupForm` model.
- `manual.signup(form: dict)` — a bare `dict` passes through the signature
  validator as "any JSON object"; the handler checks every field itself
  and raises `TRPCError(BAD_REQUEST)` with its own messages.

Validation is wired into the ASGI adapter (`mount`/`attach`) — see
`docs/validation.md` for the exact rules.

## What the wire shows

| Payload | `pydantic.signup` | `manual.signup` |
|---|---|---|
| `{username:"ada", age:36}` | `200` — validated | `200` — checked |
| `{username:"ada", age:"36"}` | `200` — string **coerced** to `36` | `400` — `isinstance` rejects |
| `{username:"ada", age:12}` | `400` — `"Invalid input: form: Invalid value"` | `400` — `"Invalid input: age: integer between 18 and 150 required"` |
| extra key `nickname` | `400` — `"Invalid input: form: Extra inputs are not permitted"` | `400` — `"Invalid input: nickname: unexpected field"` |
| `{age:true}` | `400` | `400` — only because the handler remembers `isinstance(True, int)` is `True` |

## Trade-offs

**Pydantic model input (the default, recommended):**

- The contract lives in one declarative place; every procedure gets
  coercion, missing/extra-key handling, and nested-model validation for
  free.
- Generated TypeScript is **exact**: `form: SignupForm` becomes a real
  interface (`frontend` clients get `username: string`, `age: number`, …)
  and `generate_zod` can emit a matching Zod schema for client-side forms.
- Error messages are deliberately fixed and value-free
  (`"Invalid input: form: Invalid value"`) — submitted input and internals
  can never leak through a validation error.
- Coercion is *lax* by default (`"36"` validates as `36`); annotate with
  `StrictInt`/`StrictStr` etc. when that isn't acceptable.
- Rules the schema language can't express (cross-field dependencies,
  dynamic constraints) don't fit.

**Bare `dict` + manual checks:**

- Full control: every rule is plain Python, and you write the error
  messages — `manual.signup` names the offending field because the
  handler chooses to.
- Full responsibility: the bool-is-an-int trap, forgotten `None` checks,
  and error text that accidentally echoes submitted input are all yours
  to avoid. Custom messages must stay value-free too — never include the
  submitted value or internals.
- The generated type degrades to `form: Record<string, unknown>` — the
  client loses the shape entirely (and no Zod schema can be generated).
- `form: dict` is deliberate: `dict[str, Any]` reads the same but `Any`
  has no JSON-compatible TypeScript type, so codegen rejects it.

**Rule of thumb:** use the Pydantic model whenever the contract is
expressible — the typed client surface is the point of tRPC. Reach for a
bare `dict` only when the rules genuinely can't be declared, and keep the
manual checks as tight as the model would have made them.

## Run from a clean checkout

From the repository root:

```bash
# 1. Install Python dependencies (does not build the project itself).
uv sync --no-install-project

# 2. Build and install the Rust extension into the venv.
uv run maturin develop --release

# 3. Start the example app.
cd examples/feature-pydantic-vs
uv run --project ../.. python app.py
# PORT=9000 to override the listen port (default 8000)
```

## Call it

```bash
# Identical success on both sides.
curl -X POST "http://127.0.0.1:8000/trpc/pydantic.signup" \
  -H 'content-type: application/json' -d '{"form":{"username":"ada","age":36}}'
curl -X POST "http://127.0.0.1:8000/trpc/manual.signup" \
  -H 'content-type: application/json' -d '{"form":{"username":"ada","age":36}}'
# {"result":{"data":{"age":36,"referral":null,"username":"ada"}}}

# The interesting differences (see the table above):
curl -X POST "http://127.0.0.1:8000/trpc/pydantic.signup" \
  -H 'content-type: application/json' -d '{"form":{"username":"ada","age":"36"}}'
curl -X POST "http://127.0.0.1:8000/trpc/manual.signup" \
  -H 'content-type: application/json' -d '{"form":{"username":"ada","age":"36"}}'
```

## Generated types

```bash
uv run python examples/feature-pydantic-vs/generate_types.py
```

The committed `app-router.d.ts` shows the trade-off in one file:
`pydantic.signup` takes `form: SignupForm` (exact interface) while
`manual.signup` takes `form: Record<string, unknown>`.

## Smoke test

```bash
./examples/feature-pydantic-vs/smoke.sh
```

The smoke test starts the app on a free port and asserts every row of the
table above — the identical happy path, the string-age coercion
difference, the fixed-vs-custom error messages, extra-key rejection, and
the boolean-age gotcha — plus drift on the committed `app-router.d.ts`.
No secrets, no network beyond localhost.
