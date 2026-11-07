// Placeholder until milestone 0.2.0 wires a real Python router.
// From 0.2.0 on, this project type-checks the generated AppRouter .d.ts and
// runs the official @trpc/client against every adapter.
import { createTRPCClient, httpBatchLink } from "@trpc/client";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const _client = createTRPCClient<any>({
  links: [httpBatchLink({ url: "http://127.0.0.1:8000/trpc" })],
});

export type _T = typeof _client;
