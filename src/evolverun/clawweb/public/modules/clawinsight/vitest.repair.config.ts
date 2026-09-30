import { defineConfig } from "vitest/config";
export default defineConfig({ test: { environment: "node", fileParallelism: false, include: ["server/services/repair/__tests__/**/*.test.ts", "server/routes/__tests__/repair-auth.test.ts"] } });
