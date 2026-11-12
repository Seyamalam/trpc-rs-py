/**
 * `@trpc-rs-py/link` — migration link for moving a Node tRPC app to
 * trpc-rs-py one procedure at a time (ADR 0008, MISSION §2.2).
 *
 * `migrationLink` routes operations to either the Python or the Node server
 * by procedure path; `MergedAppRouter` merges the two router types so a
 * single `createTRPCClient` sees every procedure on both sides.
 *
 * @example
 * ```ts
 * import { createTRPCClient } from "@trpc/client";
 * import { migrationLink, type MergedAppRouter } from "@trpc-rs-py/link";
 * import type { AppRouter as NodeRouter } from "./node/router";
 * import type { AppRouter as PyRouter } from "./app-router"; // generated
 *
 * type AppRouter = MergedAppRouter<NodeRouter, PyRouter>;
 * const client = createTRPCClient<AppRouter>({
 *   links: [
 *     migrationLink({
 *       nodeUrl: "http://localhost:3000/trpc",
 *       pythonUrl: "http://localhost:8000/trpc",
 *       prefix: "py", // routes py.* to Python
 *       procedures: ["posts.byId"], // plus any explicit paths
 *     }),
 *   ],
 * });
 * ```
 */
import {
  httpBatchLink,
  httpSubscriptionLink,
  splitLink,
  type HTTPHeaders,
  type Operation,
  type TRPCLink,
} from "@trpc/client";
import type {
  AnyRouter,
  EventSourceLike,
  Router,
  RouterRecord,
} from "@trpc/server/unstable-core-do-not-import";

/** Options accepted by {@link migrationLink}. */
export interface MigrationLinkOptions {
  /** Base URL of the remaining Node tRPC server, e.g. `http://localhost:3000/trpc`. */
  nodeUrl: string;
  /** Base URL of the Python trpc-rs-py server, e.g. `http://localhost:8000/trpc`. */
  pythonUrl: string;
  /**
   * Path prefix routed to Python — an operation whose path starts with
   * `<prefix>.` goes to `pythonUrl`. Defaults to `"py"`, matching the
   * `router.merge(sub, "py")` nesting convention.
   */
  prefix?: string;
  /**
   * Explicit procedure paths routed to Python — for procedures that keep
   * their original path after migrating (the frontend calls them exactly as
   * before). Wins over the prefix rule; paths are sent unrewritten.
   */
  procedures?: readonly string[];
  /** Custom `fetch` implementation forwarded to both HTTP links. */
  fetch?: typeof globalThis.fetch;
  /** Headers forwarded to both HTTP links (value or resolver). */
  headers?:
    | HTTPHeaders
    | ((opts: { opList: Operation[] }) => HTTPHeaders | Promise<HTTPHeaders>);
  /** `EventSource` ponyfill forwarded to both subscription links. */
  EventSource?: EventSourceLike.AnyConstructor;
}

/**
 * A `TRPCLink` that sends each operation to the Python or Node server.
 *
 * Subscriptions are routed through `httpSubscriptionLink` on the chosen
 * side (a migrated subscription streams from Python over SSE with no Node
 * hop); queries and mutations go through `httpBatchLink` on the chosen
 * side. Paths are never rewritten — what the client calls is what the
 * server sees.
 */
export function migrationLink<TRouter extends AnyRouter = AnyRouter>(
  opts: MigrationLinkOptions,
): TRPCLink<TRouter> {
  const prefix = `${opts.prefix ?? "py"}.`;
  const explicit = new Set(opts.procedures ?? []);
  const toPython = (op: Operation): boolean =>
    explicit.has(op.path) || op.path.startsWith(prefix);

  // Sublinks are instantiated with AnyRouter: link behaviour is
  // router-agnostic, and the per-router `TransformerOptions` conditional
  // type cannot be checked against an unresolved generic. The cast to
  // `TRPCLink<TRouter>` is a no-op at runtime.
  const http = { fetch: opts.fetch, headers: opts.headers };
  const sse = { EventSource: opts.EventSource };
  const pySub = httpSubscriptionLink<AnyRouter, EventSourceLike.AnyConstructor>(
    {
      url: opts.pythonUrl,
      ...sse,
    },
  ) as TRPCLink<TRouter>;
  const nodeSub = httpSubscriptionLink<
    AnyRouter,
    EventSourceLike.AnyConstructor
  >({
    url: opts.nodeUrl,
    ...sse,
  }) as TRPCLink<TRouter>;
  const pyHttp = httpBatchLink<AnyRouter>({
    url: opts.pythonUrl,
    ...http,
  }) as TRPCLink<TRouter>;
  const nodeHttp = httpBatchLink<AnyRouter>({
    url: opts.nodeUrl,
    ...http,
  }) as TRPCLink<TRouter>;

  return splitLink<TRouter>({
    condition: (op) => op.type === "subscription",
    true: splitLink<TRouter>({
      condition: toPython,
      true: pySub,
      false: nodeSub,
    }),
    false: splitLink<TRouter>({
      condition: toPython,
      true: pyHttp,
      false: nodeHttp,
    }),
  });
}

export type RootOf<TRouter extends AnyRouter> =
  TRouter["_def"]["_config"]["$types"];

export type RecordOf<TRouter extends AnyRouter> = TRouter["_def"]["record"];

/**
 * Deep-merges two router records; the Python record wins on every conflict.
 *
 * Values carrying a `_def` (procedures and routers) are leaves — the Python
 * side replaces them outright. Plain nested records merge recursively.
 */
export type MergedRecord<A extends RouterRecord, B extends RouterRecord> = {
  [K in keyof A | keyof B]: K extends keyof B
    ? K extends keyof A
      ? A[K] extends { _def: unknown }
        ? B[K]
        : B[K] extends RouterRecord
          ? A[K] extends RouterRecord
            ? MergedRecord<A[K], B[K]>
            : B[K]
          : B[K]
      : B[K]
    : K extends keyof A
      ? A[K]
      : never;
} extends infer R
  ? R extends RouterRecord
    ? R
    : never
  : never;

/**
 * Root types of the merged router: the Node router's context, meta and
 * transformer, with `errorShape` widened to a union so errors from either
 * side (including Python's `AppRouterErrorShape` union) type-check.
 */
export type MergedRootTypes<TNode extends AnyRouter, TPy extends AnyRouter> = {
  ctx: RootOf<TNode>["ctx"];
  meta: RootOf<TNode>["meta"];
  transformer: RootOf<TNode>["transformer"];
  errorShape: RootOf<TNode>["errorShape"] | RootOf<TPy>["errorShape"];
};

/**
 * A router type whose procedures are the union of the Node and Python
 * routers — Python wins on conflicting paths (it is the destination of the
 * migration). Pass to `createTRPCClient`/`createTRPCProxyClient` so every
 * migrated and unmigrated procedure resolves with full types.
 */
export type MergedAppRouter<
  TNode extends AnyRouter,
  TPy extends AnyRouter,
> = Router<
  MergedRootTypes<TNode, TPy>,
  MergedRecord<RecordOf<TNode>, RecordOf<TPy>>
>;
