// A server component: the official @trpc/client calls the Python backend
// server-to-server while the page renders (no CORS, no client JS needed for
// the query). TRPC_URL overrides the default Flask dev address.
import { createTRPCClient, httpBatchLink } from "@trpc/client";

import type { AppRouter } from "../app-router";

const client = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: process.env.TRPC_URL ?? "http://127.0.0.1:8000/trpc",
    }),
  ],
});

export const dynamic = "force-dynamic";

export default async function Page() {
  const greeting = await client.greeting.query({ name: "Next.js" });
  const user = await client.create_user.mutate({ user: { name: "Ada", age: 36 } });
  const found = await client.user_by_id.query({ user_id: user.id });

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 480, margin: "2rem auto" }}>
      <h1>flask-nextjs</h1>
      <p>Next.js server component → Flask + tRPC backend.</p>
      <p>{greeting}</p>
      <p>
        Created and fetched user #{found.id}: {found.name} ({found.age})
      </p>
    </main>
  );
}
