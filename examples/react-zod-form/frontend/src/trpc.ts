// tRPC hooks bound to the Python router: `createTRPCReact` gives typed
// `trpc.<procedure>.useQuery` / `useMutation` hooks inferred from the
// generated `AppRouter` declaration.
import { createTRPCReact } from "@trpc/react-query";

import type { AppRouter } from "../app-router";

export const trpc = createTRPCReact<AppRouter>();
