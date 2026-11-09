// Runtime test infrastructure: spawns the Python fixture servers (FastAPI and
// Starlette, both serving the router in `fixtures/router.py` through the ASGI
// adapter under `/trpc`) as child processes, waits for readiness, and provides
// a request-counting fetch wrapper so tests can assert that a batch is exactly
// one HTTP request.
import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TS_TESTS_DIR = path.resolve(HERE, "..");
const REPO_ROOT = path.resolve(TS_TESTS_DIR, "..", "..");

/** One recorded HTTP request made through the counting fetch wrapper. */
export interface RecordedRequest {
  method: string;
  url: string;
}

/** Collects every request made through `countingFetch`. */
export class RequestLog {
  public readonly requests: RecordedRequest[] = [];

  public get count(): number {
    return this.requests.length;
  }

  public last(): RecordedRequest {
    const request = this.requests.at(-1);
    if (request === undefined) {
      throw new Error("no request recorded");
    }
    return request;
  }

  public reset(): void {
    this.requests.length = 0;
  }
}

/**
 * A `fetch` implementation that records every request and forwards to the
 * real fetch. Pass it as the `fetch` option of an official tRPC link.
 */
export function makeCountingFetch(log: RequestLog): typeof fetch {
  return (input, init) => {
    log.requests.push({
      method: init?.method ?? "GET",
      url: String(input),
    });
    return fetch(input, init);
  };
}

/** Pick a free TCP port on localhost by binding port 0 and closing. */
async function pickFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close(() => reject(new Error("unexpected listen address")));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

/** A running fixture server child process plus its base URL. */
export interface FixtureServer {
  baseUrl: string;
  stop(): Promise<void>;
}

/**
 * Spawn `uv run python fixtures/<framework>_app.py` from the repository root
 * (so the compiled `trpc_rs_py` extension from the uv venv is used), wait for
 * the server to accept HTTP requests, and return its base URL.
 */
export async function startFixtureServer(
  framework: "fastapi" | "starlette",
): Promise<FixtureServer> {
  const port = await pickFreePort();
  const script = path.join(TS_TESTS_DIR, "fixtures", `${framework}_app.py`);
  const child = spawn("uv", ["run", "python", script], {
    cwd: REPO_ROOT,
    env: { ...process.env, TRPC_TEST_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stderr: string[] = [];
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr.push(chunk.toString());
  });
  const earlyExit = new Promise<never>((_resolve, reject) => {
    child.once("exit", (code, signal) => {
      reject(
        new Error(
          `fixture server (${framework}) exited early with code ${code} signal ${signal}: ${stderr.join("")}`,
        ),
      );
    });
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      // Any HTTP response (even 400/404) proves uvicorn is serving.
      await fetch(`${baseUrl}/trpc/ping`, {
        signal: AbortSignal.timeout(2000),
      });
      break;
    } catch {
      if (Date.now() > deadline) {
        child.kill("SIGKILL");
        throw new Error(
          `fixture server (${framework}) did not become ready: ${stderr.join("")}`,
        );
      }
      await Promise.race([delay(200), earlyExit]);
    }
  }
  child.removeAllListeners("exit");
  return {
    baseUrl,
    stop: async () => {
      child.kill("SIGTERM");
      const exited = new Promise<void>((resolve) => {
        child.once("exit", () => resolve());
      });
      const timeout = new Promise<void>((resolve) => setTimeout(resolve, 5000));
      await Promise.race([exited, timeout]);
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
      await exited;
    },
  };
}
