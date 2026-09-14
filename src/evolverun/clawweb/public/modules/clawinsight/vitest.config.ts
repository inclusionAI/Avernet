import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    // Server-side monitoring suites opt into Node so the workspace CI runner can execute
    // both browser and server tests in one Vitest invocation.
    environmentMatchGlobs: [["server/**", "node"]],
    hookTimeout: 30000,
    testTimeout: 300000,
    setupFiles: ["./web/test/setup.ts"],
    globals: true,
    css: false,
    exclude: ["e2e/**", "node_modules/**", "dist-server/**", "dist/**"],
  },
});
