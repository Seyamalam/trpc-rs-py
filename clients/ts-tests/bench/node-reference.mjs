// Node @trpc/server standalone reference for the HTTP load suite (MISSION §10).
// No input validation, matching the unannotated Python benchmark handlers.
// 11.25.0 only populates `input` via an .input() parser, so read getRawInput().
import { createHTTPServer } from "@trpc/server/adapters/standalone";
import { initTRPC } from "@trpc/server";

const t = initTRPC.create();
const appRouter = t.router({
  greet: t.procedure.query(
    async ({ getRawInput }) => `Hello ${(await getRawInput()).name}`,
  ),
  echo: t.procedure.mutation(
    async ({ getRawInput }) => (await getRawInput()).message,
  ),
});

const port = Number(process.argv[2] ?? "3101");
const server = createHTTPServer({ router: appRouter });
server.listen(port, "127.0.0.1");
