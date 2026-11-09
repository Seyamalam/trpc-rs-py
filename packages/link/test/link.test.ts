// Unit tests for @trpc-rs-py/link: path routing to Python vs Node and the
// MergedAppRouter type merge. Uses createTRPCUntypedClient with a stubbed
// fetch/EventSource so no real servers are needed.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createTRPCUntypedClient } from "@trpc/client";
import type {
  AnyRootTypes,
  DefaultErrorShape,
  EventSourceLike,
  QueryProcedure,
  Router,
} from "@trpc/server/unstable-core-do-not-import";
import type { TRPCErrorShape } from "@trpc/server";

import { migrationLink, type MergedAppRouter } from "../src/index.ts";

const NODE = "http://node.test/trpc";
const PY = "http://py.test/trpc";

/** Canned batch response: `[{result:{data:null}}]` per the v11 shape. */
const okBody = JSON.stringify([{ result: { data: null } }]);

function recordingFetch(urls: string[]) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    urls.push(String(input));
    return new Response(okBody, {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
}

/** Minimal EventSource stand-in that only records its URL. */
class FakeEventSource {
  static lastUrl: string | undefined;
  constructor(url: string | URL) {
    FakeEventSource.lastUrl = String(url);
  }
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

function client(urls: string[]) {
  return createTRPCUntypedClient({
    links: [
      migrationLink({
        nodeUrl: NODE,
        pythonUrl: PY,
        procedures: ["posts.byId"],
        fetch: recordingFetch(urls),
        EventSource:
          FakeEventSource as unknown as EventSourceLike.AnyConstructor,
      }),
    ],
  });
}

describe("migrationLink routing", () => {
  it("routes prefix-pathed queries to Python", async () => {
    const urls: string[] = [];
    await client(urls).query("py.posts.byId", { id: 1 });
    assert.equal(urls.length, 1);
    assert.ok(urls[0]!.startsWith(PY), urls[0]);
  });

  it("routes explicitly listed procedures to Python", async () => {
    const urls: string[] = [];
    await client(urls).query("posts.byId", { id: 1 });
    assert.ok(urls[0]!.startsWith(PY), urls[0]);
  });

  it("routes everything else to Node", async () => {
    const urls: string[] = [];
    await client(urls).query("users.me", {});
    await client(urls).mutation("users.rename", { name: "x" });
    assert.equal(urls.length, 2);
    assert.ok(
      urls.every((u) => u.startsWith(NODE)),
      urls.join(),
    );
  });

  it("does not rewrite the wire path", async () => {
    const urls: string[] = [];
    await client(urls).query("py.posts.byId", { id: 1 });
    assert.ok(
      !urls[0]!.includes("posts.byId") || urls[0]!.includes("py.posts.byId"),
    );
  });

  it("routes subscriptions through the SSE link on the chosen side", async () => {
    const c = client([]);
    const sub = c.subscription("py.ticks", {}, {});
    void sub.next(); // EventSource is constructed lazily on first next()
    await new Promise((r) => setTimeout(r, 50));
    assert.ok(FakeEventSource.lastUrl!.startsWith(PY), FakeEventSource.lastUrl);
    await sub.return?.();
    const nodeSub = c.subscription("ticks", {}, {});
    void nodeSub.next();
    await new Promise((r) => setTimeout(r, 50));
    assert.ok(
      FakeEventSource.lastUrl!.startsWith(NODE),
      FakeEventSource.lastUrl,
    );
    await nodeSub.return?.();
  });
});

// --- Type-level tests: MergedAppRouter -------------------------------------

type NodeRoot = {
  ctx: object;
  meta: object;
  errorShape: DefaultErrorShape;
  transformer: false;
};

interface UsersMeProcedure extends QueryProcedure<{
  meta: unknown;
  input: { id: number };
  output: { name: string };
  errorShape: DefaultErrorShape;
}> {}

interface SharedProcedure extends QueryProcedure<{
  meta: unknown;
  input: { node: boolean };
  output: string;
  errorShape: DefaultErrorShape;
}> {}

interface NodeRouter extends Router<
  NodeRoot,
  { users_me: UsersMeProcedure; shared: SharedProcedure }
> {}

type PyErrorShape =
  DefaultErrorShape | TRPCErrorShape<{ code: "NOT_FOUND"; httpStatus: 404 }>;

type PyRoot = {
  ctx: object;
  meta: object;
  errorShape: PyErrorShape;
  transformer: false;
};

interface PySharedProcedure extends QueryProcedure<{
  meta: unknown;
  input: { py: boolean };
  output: number;
  errorShape: PyErrorShape;
}> {}

interface PyPostsProcedure extends QueryProcedure<{
  meta: unknown;
  input: { id: number };
  output: { title: string };
  errorShape: PyErrorShape;
}> {}

interface PyRouter extends Router<
  PyRoot,
  {
    shared: PySharedProcedure;
    posts: { by_id: PyPostsProcedure };
  }
> {}

type Merged = MergedAppRouter<NodeRouter, PyRouter>;
type MergedRecordType = Merged["_def"]["record"];

describe("MergedAppRouter types", () => {
  it("merges records with Python winning conflicts", () => {
    type Assert<T extends true> = T;
    // Node-only and Python-only procedures both present.
    type _HasNode = Assert<
      MergedRecordType["users_me"] extends UsersMeProcedure ? true : false
    >;
    type _HasPy = Assert<
      MergedRecordType["posts"]["by_id"] extends PyPostsProcedure ? true : false
    >;
    // Python wins the conflicting path: its input/output shape survives.
    type _PyWins = Assert<
      MergedRecordType["shared"] extends PySharedProcedure ? true : false
    >;
    // errorShape widens to the union of both sides.
    type _ErrUnion = Assert<
      Merged["_def"]["_config"]["$types"]["errorShape"] extends
        | DefaultErrorShape
        | TRPCErrorShape<{ code: "NOT_FOUND"; httpStatus: 404 }>
        ? true
        : false
    >;
    const ok: [_HasNode, _HasPy, _PyWins, _ErrUnion] = [true, true, true, true];
    assert.deepEqual(ok, [true, true, true, true]);
  });
});
