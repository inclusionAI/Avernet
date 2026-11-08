import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Resolve shared to source so tests exercise the package boundary directly instead of
  // built dist artifacts, and never reach across packages with relative paths.
  resolve: {
    alias: {
      "@avernet/clawweb-shared": fileURLToPath(new URL("../../shared", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./web/test/setup.ts"],
    globals: true,
    css: false,
    // The module includes real-git integration tests (clawflows git client) that
    // spawn several git processes per case; the 5s default times out under
    // full-suite parallel load.
    testTimeout: 15_000,
    exclude: ["e2e/**", "node_modules/**", "dist-server/**", "dist/**"],
  },
});
