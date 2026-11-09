// Runtime wire-compatibility tests: the OFFICIAL `@trpc/client` v11.25.0
// (installed from npm, not a mock) calling live Python ASGI servers (FastAPI
// and Starlette, spawned as child processes) over real HTTP on 127.0.0.1.
//
// Every assertion here encodes the official tRPC v11 wire contract, verified
// against a real `@trpc/server` 11.25.0 reference server. These tests must
// never be weakened to match a server deviation; a failure means the server
// implementation is incompatible (see docs/protocol.md for the current
// deviations and evidence).
import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";

import {
  TRPCClientError,
  createTRPCClient,
  createTRPCUntypedClient,
  httpBatchLink,
  httpSubscriptionLink,
} from "@trpc/client";
import { EventSource } from "eventsource";

import type { AppRouter } from "../fixtures/app-router";

import {
  RequestLog,
  makeCountingFetch,
  startFixtureServer,
  type FixtureServer,
} from "./runtime-helpers";

// Shared context for one adapter's test run.
interface AdapterContext {
  server: FixtureServer;
  /** Typed client mirroring a real application (generated AppRouter types). */
  client: ReturnType<typeof createTRPCClient<AppRouter>>;
  /** Untyped client for paths the type system cannot express (unknown procedure). */
  untyped: ReturnType<typeof createTRPCUntypedClient<AppRouter>>;
  log: RequestLog;
}

async function makeContext(
  framework: "fastapi" | "starlette",
): Promise<AdapterContext> {
  const server = await startFixtureServer(framework);
  const log = new RequestLog();
  const client = createTRPCClient<AppRouter>({
    links: [
      httpBatchLink({
        url: `${server.baseUrl}/trpc`,
        fetch: makeCountingFetch(log),
      }),
    ],
  });
  const untyped = createTRPCUntypedClient<AppRouter>({
    links: [
      httpBatchLink({
        url: `${server.baseUrl}/trpc`,
        fetch: makeCountingFetch(log),
      }),
    ],
  });
  return { server, client, untyped, log };
}

/** Assert the rejection is the official client's error with the given shape. */
function assertTrpcError(
  error: unknown,
  expected: { code: string; httpStatus: number; message?: string },
): void {
  assert.ok(
    error instanceof TRPCClientError,
    `expected TRPCClientError, got ${String(error)}`,
  );
  assert.equal(
    error.data?.code,
    expected.code,
    "data.code (machine-readable name)",
  );
  assert.equal(error.data?.httpStatus, expected.httpStatus, "data.httpStatus");
  assert.equal(error.shape?.data?.code, expected.code, "shape.data.code");
  assert.equal(
    error.shape?.data?.httpStatus,
    expected.httpStatus,
    "shape.data.httpStatus",
  );
  if (expected.message !== undefined) {
    assert.equal(error.message, expected.message);
  }
}

for (const framework of ["fastapi", "starlette"] as const) {
  describe(`official @trpc/client v11 runtime compat: ${framework}`, () => {
    let ctx: AdapterContext;

    before(async () => {
      ctx = await makeContext(framework);
    });

    after(async () => {
      await ctx.server.stop();
    });

    it("single query succeeds with the typed output", async () => {
      const message: string = await ctx.client.echo.query({ message: "hi" });
      assert.equal(message, "echo: hi");
    });

    it("Pydantic-validated query round-trips the generated types", async () => {
      const out = await ctx.client.greet.query({
        params: { name: "Ada", excited: true },
      });
      assert.equal(out.greeting, "Hello Ada!");
    });

    it("single mutation succeeds with the typed output", async () => {
      const created = await ctx.client.create_post.mutate({ title: "hello" });
      assert.deepEqual(created, { title: "hello", created: true });
    });

    it("two concurrent queries are batched into exactly one HTTP request, in order", async () => {
      ctx.log.reset();
      const [first, second] = await Promise.all([
        ctx.client.echo.query({ message: "yo" }),
        ctx.client.echo.query({ message: "hello" }),
      ]);
      assert.equal(first, "echo: yo");
      assert.equal(second, "echo: hello");
      assert.equal(
        ctx.log.count,
        1,
        `expected one batched request, saw ${ctx.log.count}`,
      );
      assert.match(ctx.log.last().url, /batch=1/);
      // Note: the batched path joins with a plain (unencoded) comma.
      assert.match(ctx.log.last().url, /\/echo,echo\?/);
    });

    it("a concurrent query and mutation use separate batches (official batching is per operation type)", async () => {
      ctx.log.reset();
      const [echoed, created] = await Promise.all([
        ctx.client.echo.query({ message: "q" }),
        ctx.client.create_post.mutate({ title: "t" }),
      ]);
      assert.equal(echoed, "echo: q");
      assert.equal(created.created, true);
      assert.equal(
        ctx.log.count,
        2,
        "query and mutation must not share a batch",
      );
      const methods = ctx.log.requests.map((r) => r.method).sort();
      assert.deepEqual(methods, ["GET", "POST"]);
    });

    it("a NOT_FOUND TRPCError rejects with the official error shape", async () => {
      await assert.rejects(ctx.client.boom.query(), (error: unknown) => {
        assertTrpcError(error, {
          code: "NOT_FOUND",
          httpStatus: 404,
          message: "deliberate not found",
        });
        return true;
      });
    });

    it("an unexpected server exception rejects as INTERNAL_SERVER_ERROR", async () => {
      await assert.rejects(ctx.client.explode.query(), (error: unknown) => {
        assertTrpcError(error, {
          code: "INTERNAL_SERVER_ERROR",
          httpStatus: 500,
        });
        return true;
      });
    });

    it("an unknown procedure rejects with NOT_FOUND", async () => {
      await assert.rejects(
        ctx.untyped.query("definitely_not_a_procedure", undefined),
        (error: unknown) => {
          assertTrpcError(error, { code: "NOT_FOUND", httpStatus: 404 });
          return true;
        },
      );
    });

    it("invalid Pydantic input rejects with BAD_REQUEST", async () => {
      await assert.rejects(
        ctx.untyped.query("greet", { params: { name: 42 } }),
        (error: unknown) => {
          assertTrpcError(error, { code: "BAD_REQUEST", httpStatus: 400 });
          return true;
        },
      );
    });

    it("a batch mixing a failing and a succeeding query resolves each call independently", async () => {
      const [boom, pong] = await Promise.allSettled([
        ctx.client.boom.query(),
        ctx.client.ping.query(),
      ]);
      assert.equal(pong.status, "fulfilled");
      if (pong.status === "fulfilled") {
        assert.equal(pong.value, "pong");
      }
      assert.equal(boom.status, "rejected");
      if (boom.status === "rejected") {
        assertTrpcError(boom.reason, {
          code: "NOT_FOUND",
          httpStatus: 404,
          message: "deliberate not found",
        });
      }
    });

    it("a no-input query works through the official batch link (input={} wire form)", async () => {
      const pong: string = await ctx.client.ping.query();
      assert.equal(pong, "pong");
    });

    it("a no-input mutation works through the official batch link", async () => {
      const result: string = await ctx.client.no_input.mutate();
      assert.equal(result, "mutated");
    });

    it("methodOverride POST sends queries as POST and works", async () => {
      const postLog = new RequestLog();
      const postClient = createTRPCClient<AppRouter>({
        links: [
          httpBatchLink({
            url: `${ctx.server.baseUrl}/trpc`,
            fetch: makeCountingFetch(postLog),
            methodOverride: "POST",
          }),
        ],
      });
      const message: string = await postClient.echo.query({ message: "post" });
      assert.equal(message, "echo: post");
      assert.equal(postLog.count, 1);
      assert.equal(postLog.last().method, "POST");
    });

    it("mutations are POST-only: a direct GET on a mutation path returns the METHOD_NOT_SUPPORTED envelope", async () => {
      // The official client never sends GET for a mutation; this direct request
      // pins the server-side method enforcement for any client that tries.
      // The request is batched, and the official server enforces the method
      // per call (inside its calls.map loop), so the body is an array of
      // per-call envelopes; a uniform batch carries that call's HTTP status.
      const response = await fetch(
        `${ctx.server.baseUrl}/trpc/no_input?batch=1&input=%7B%220%22%3Anull%7D`,
        { method: "GET" },
      );
      assert.equal(response.status, 405);
      const body: unknown = await response.json();
      assert.ok(Array.isArray(body));
      assert.equal(body.length, 1);
      const envelope = body[0] as {
        error: {
          code: number;
          message: string;
          data: { code: string; httpStatus: number };
        };
      };
      assert.equal(envelope.error.code, -32005);
      assert.equal(envelope.error.data.code, "METHOD_NOT_SUPPORTED");
      assert.equal(envelope.error.data.httpStatus, 405);
    });

    it("subscription streams tracked events over SSE and completes", async () => {
      // The official httpSubscriptionLink drives a real EventSource against
      // the Python server: tracked yields arrive as {id, data} values and the
      // stream ends when the generator returns.
      const subClient = createTRPCClient<AppRouter>({
        links: [
          httpSubscriptionLink({
            url: `${ctx.server.baseUrl}/trpc`,
            EventSource,
          }),
        ],
      });
      const seen: Array<{ id: string; data: { tick: number } }> = [];
      await new Promise<void>((resolve, reject) => {
        subClient.ticks.subscribe(undefined, {
          onData: (value) => {
            seen.push(value);
          },
          onError: reject,
          onComplete: resolve,
        });
      });
      assert.deepEqual(
        seen.map((event) => event.data.tick),
        [0, 1, 2],
      );
      assert.deepEqual(
        seen.map((event) => event.id),
        ["0", "1", "2"],
      );
    });

    it("subscription via async iteration yields the same events", async () => {
      const subClient = createTRPCClient<AppRouter>({
        links: [
          httpSubscriptionLink({
            url: `${ctx.server.baseUrl}/trpc`,
            EventSource,
          }),
        ],
      });
      const ticks: number[] = [];
      for await (const event of subClient.ticks.subscribe(undefined)) {
        ticks.push(event.data.tick);
      }
      assert.deepEqual(ticks, [0, 1, 2]);
    });
  });
}
