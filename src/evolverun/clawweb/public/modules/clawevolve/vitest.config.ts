import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    setupFiles: ["./server/test/setup.ts"],
    exclude: ["node_modules/**", "dist-server/**", "dist/**"],
  },
});
