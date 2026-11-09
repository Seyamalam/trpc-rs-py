// Type tests: the generated AppRouter declaration from the Python Router
// (clients/ts-tests/router_source.py, generated into app-router.d.ts by
// generate-fixture.py) consumed by the official @trpc/client v11.
//
// Positive cases must type-check with exact procedure input/output types;
// negative cases use @ts-expect-error so a regression in the generated
// types fails this file. Run with `pnpm typecheck` (tsc --noEmit).
import {
  createTRPCClient,
  createTRPCProxyClient,
  httpBatchLink,
} from "@trpc/client";
import type {
  inferProcedureInput,
  inferProcedureOutput,
} from "@trpc/server/unstable-core-do-not-import";

import type {
  AppRouter,
  ProcedureMap,
  RouterInputs,
  RouterOutputs,
} from "./app-router";

const client = createTRPCClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

const proxy = createTRPCProxyClient<AppRouter>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

// --- Positive cases: exact input and output types -------------------------

async function positive(): Promise<void> {
  // No-input query: called with no argument.
  const health: boolean = await client.health.query();

  // Single annotated parameter is an object property.
  const post = await client.post_by_id.query({ post_id: 1 });
  const title: string = post.title;
  const weights: Array<number | undefined> = post.tags.map((tag) => tag.weight);
  const color: "red" | "blue" = post.color;

  // Multi-parameter mutation with literal and nested model inputs.
  const created = await client.create_post.mutate({
    post: { title: "Hello", tags: [{ label: "t", weight: 2 }], color: "blue" },
    author: { name: "Ada", posts: [] },
    source: "web",
  });
  const createdTitle: string = created.title;

  // Optional fields may be omitted.
  const withDefaults = await client.create_post.mutate({
    post: { title: "Hi", tags: [], color: "red", meta: {} },
    author: { name: "Ada" },
  });
  const note: string | null | undefined = withDefaults.note;

  // Query with a default-valued parameter.
  const found = await client["search-items"].query({ q: "hello" });
  const first: string | undefined = found[0];

  // No-input mutation.
  const stamp: string = await client.no_input.mutate();

  // Union input, tuple-or-null output.
  const echoed = await client.echo_all.query({ payload: "x" });
  const pair: readonly [number, string] | null = echoed;

  // Recursive model.
  const tree = await client.node_tree.query({
    root: { value: 1, children: [] },
  });
  const parentValue: number | undefined = tree.parent?.value;

  // Nested namespace path.
  const nested = await client.tree.by_id.query({ node_id: 3 });
  const value: number = nested.value;

  // The proxy client offers the same typed surface.
  const proxyHealth: boolean = await proxy.health.query();

  // Models named after TS type keywords (`never`, `undefined`) sanitize.
  const keywordPair = await client.keyword_round_trip.query({
    item: { value: 1 },
  });
  const keywordValue: number = keywordPair.value;

  // serialize_by_alias model: the wire key is the alias in both directions.
  const wired = await client.wire_alias.mutate({ item: { wire_value: "v" } });
  const wiredValue: string = wired.wire_value;

  // Models named after TS built-in generics (`Array`, `Record`) sanitize, so
  // the generated `Array<...>` / `Record<...>` annotations still refer to the
  // built-ins.
  const shadowed = await client.builtin_shadow.query({
    item: { items: [1, 2] },
  });
  const entries: Record<string, string> = shadowed.entries;

  // Models named after type-context keywords (`infer`, `keyof`, `readonly`,
  // `unique`) sanitize to declaration-safe names.
  const keywords = await client.type_keyword.query({
    first: { value: 1 },
    second: { value: "s" },
    third: { value: 3 },
  });
  const keywordFlag: boolean = keywords.value;

  // Model names matching generated inference helper imports must not shadow
  // the helper references used by RouterInputs/RouterOutputs.
  const helperNames = await client.helper_names.query({ item: { value: 1 } });
  const helperValue: number = helperNames.value;

  // inferRouterInputs / RouterInputs / RouterOutputs helpers.
  type ByIdInput = RouterInputs["post_by_id"];
  type ByIdOutput = RouterOutputs["post_by_id"];
  const byIdInput: ByIdInput = { post_id: 1 };
  const byIdOutput: ByIdOutput = post;

  // Per-procedure inference helpers.
  type CreateInput = inferProcedureInput<ProcedureMap["create_post"]>;
  type CreateOutput = inferProcedureOutput<ProcedureMap["create_post"]>;
  const createInput: CreateInput = {
    post: { title: "x", tags: [], color: "red" },
    author: { name: "a" },
  };
  const createOutput: CreateOutput = created;

  // The `weights`, `createdTitle`, `withDefaults`, `pair`, `parentValue`,
  // `nested`, `proxyHealth`, `keywordValue`, `wired`, `byIdInput`,
  // `createInput`, `shadowed`, `entries`, `keywords`, and `helperValue` values
  // are consumed above; this return keeps the function self-contained.
  return void [
    weights,
    createdTitle,
    note,
    pair,
    parent,
    proxyHealth,
    keywordValue,
    wiredValue,
    byIdInput,
    createOutput,
    entries,
    keywordFlag,
    helperValue,
  ];
}

// --- Negative cases: expected type errors --------------------------------

async function negative(): Promise<void> {
  // Wrong input type for a parameter.
  // @ts-expect-error post_id must be a number, not a string
  await client.post_by_id.query({ post_id: "1" });

  // Unknown key in the input object.
  // @ts-expect-error 'other' does not exist in the input type
  await client.post_by_id.query({ other: 1 });

  // Missing required input.
  // @ts-expect-error post_by_id requires an input object
  await client.post_by_id.query();

  // Literal input restricted to known values. The bad value lives in a
  // separately declared object so the expected error lands on the call line
  // the directive covers, even after formatting.
  const badSourceInput = {
    post: { title: "t", tags: [], color: "red" },
    author: { name: "a" },
    source: "cli",
  };
  // @ts-expect-error source must be "web" or "app"
  await client.create_post.mutate(badSourceInput);

  // Unknown procedure path.
  // @ts-expect-error 'nonexistent' is not in the router
  await client.nonexistent.query();

  // Mutations are called with .mutate, not .query.
  // @ts-expect-error create_post has no .query method
  await client.create_post.query({ post: {}, author: {} });

  // Queries are called with .query, not .mutate.
  // @ts-expect-error health has no .mutate method
  await client.health.mutate();

  // Wrong output assumption: health returns boolean, not string.
  // @ts-expect-error health's output is boolean
  const _bad: string = await client.health.query();

  // Enum-like literal unions reject other strings. Same one-line pattern as
  // above so the expected error stays under the directive.
  const badColorInput = {
    post: { title: "t", tags: [], color: "green" },
    author: { name: "a" },
  };
  // @ts-expect-error color must be "red" or "blue"
  await client.create_post.mutate(badColorInput);

  // The proxy client enforces the same input types.
  // @ts-expect-error node_id must be a number
  await proxy.tree.by_id.query({ node_id: "3" });
}

export { client, negative, positive, proxy };
