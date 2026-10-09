import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["server/services/monitoring/__tests__/*.test.ts", "server/routes/__tests__/monitoring.test.ts"],
    fileParallelism: false,
    hookTimeout: 30000,
    testTimeout: 15000,
  },
});
