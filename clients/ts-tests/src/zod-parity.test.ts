// Zod/Pydantic parity: the generated zod schemas (fixtures/zod-schemas.ts)
// must accept and reject exactly the values Pydantic does, across the
// hypothesis-generated case set in fixtures/zod-cases.json (MISSION §2.5).
// The cases file is produced by fixtures/generate_zod_cases.py; the `expect`
// verdicts there come from `SignupForm`/`_SignupInput` model validation.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import type { z } from "zod";

import { SignupInput } from "../fixtures/zod-schemas";
import type { RouterInputs, SignupForm } from "../fixtures/app-router";

interface ParityCase {
  schema: string;
  value: unknown;
  expect: boolean;
}

const cases = JSON.parse(
  readFileSync(new URL("../fixtures/zod-cases.json", import.meta.url), "utf8"),
) as ParityCase[];

const schemas = { SignupInput } as const;

describe("zod/pydantic parity", () => {
  it("accepts and rejects the same values as the Pydantic model", () => {
    for (const c of cases) {
      const schema = schemas[c.schema as keyof typeof schemas];
      assert.ok(schema, `unknown schema ${c.schema}`);
      const result = schema.safeParse(c.value);
      assert.equal(
        result.success,
        c.expect,
        `verdict mismatch on ${JSON.stringify(c.value)}: ` +
          (result.success
            ? "zod accepted, pydantic rejected"
            : JSON.stringify(result.error.issues)),
      );
    }
    assert.ok(
      cases.length >= 40,
      "case file should cover accept + reject paths",
    );
    assert.ok(
      cases.some((c) => c.expect),
      "case file needs accept cases",
    );
    assert.ok(
      cases.some((c) => !c.expect),
      "case file needs reject cases",
    );
  });

  it("zod input/output types are assignable to the generated router types", () => {
    // `z.input` (what callers supply) accepts every generated-input-shaped
    // value; `z.output` (post-parse) is assignable back to the model's
    // generated interface — defaults only make fields required, never change
    // their types.
    const _asInput: z.input<typeof SignupInput> = {} as RouterInputs["signup"];
    const _asGenerated: SignupForm = {} as z.output<typeof SignupInput>["form"];
    void _asInput;
    void _asGenerated;
  });
});
