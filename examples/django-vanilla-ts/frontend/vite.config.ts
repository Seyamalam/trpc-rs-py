import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // Same-origin calls to the Django backend — the dev server proxies /trpc,
    // so the browser never makes a cross-origin request (no CORS needed).
    proxy: {
      "/trpc": {
        // Point this at the Django dev server (app.py serves on :8000).
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
