import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./web/test/setup.ts"],
    globals: true,
    css: false,
    exclude: ["e2e/**", "node_modules/**", "dist-server/**", "dist/**"],
  },
});
