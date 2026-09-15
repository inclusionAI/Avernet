import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    // Server-side monitoring suites opt into Node so the workspace CI runner can execute
    // both browser and server tests in one Vitest invocation.
    environmentMatchGlobs: [["server/services/monitoring/**", "node"], ["server/routes/__tests__/monitoring.test.ts", "node"]],
    // Keep this package serial even when the workspace runner supplies maxWorkers=2.
    fileParallelism: false,
    setupFiles: ["./web/test/setup.ts"],
    globals: true,
    css: false,
    exclude: ["e2e/**", "node_modules/**", "dist-server/**", "dist/**"],
  },
});
