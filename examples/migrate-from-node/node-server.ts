// The "existing" Node tRPC app being migrated. `posts.byId` is still
// implemented here, but migrationLink routes it to Python — that is the
// point: procedures can move without touching the frontend.
import { initTRPC } from "@trpc/server";
import { createHTTPServer } from "@trpc/server/adapters/standalone";
import { z } from "zod";

const t = initTRPC.create();

const posts = [
  { id: 1, title: "Node post one", author: "ada" },
  { id: 2, title: "Node post two", author: "grace" },
];

export const appRouter = t.router({
  posts: t.router({
    list: t.procedure.query(() => posts),
    byId: t.procedure
      .input(z.object({ id: z.number() }))
      .query(({ input }) => ({
        id: input.id,
        title: "still served by Node",
        author: "node",
      })),
  }),
  users: t.router({
    me: t.procedure.query(() => ({ id: 7, name: "Ada (Node)", admin: true })),
  }),
});

export type AppRouter = typeof appRouter;

if (process.env.NODE_SERVER_PORT) {
  createHTTPServer({ router: appRouter }).listen(
    Number(process.env.NODE_SERVER_PORT),
    "127.0.0.1",
  );
}
