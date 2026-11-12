// Vanilla TypeScript calls with the official @trpc/client — every procedure
// is typed from the generated AppRouter declaration, no framework needed.
import { createTRPCClient, httpBatchLink } from "@trpc/client";

import type { AppRouter } from "../app-router";

const client = createTRPCClient<AppRouter>({
  links: [
    // Same-origin: vite.config.ts proxies /trpc to the Django dev server.
    httpBatchLink({ url: "/trpc" }),
  ],
});

const output = document.getElementById("output")!;
const lines: string[] = [];
const show = (line: string) => {
  lines.push(line);
  output.textContent = lines.join("\n");
};

const greeting = await client.greeting.query({ name: "Django" });
show(greeting);

document.getElementById("create")!.addEventListener("click", async () => {
  try {
    const user = await client.create_user.mutate({
      user: { name: "Ada", age: 36 },
    });
    const found = await client.user_by_id.query({ user_id: user.id });
    show(`created + fetched #${found.id}: ${found.name} (${found.age})`);
  } catch (error) {
    show(`error: ${error instanceof Error ? error.message : String(error)}`);
  }
});
