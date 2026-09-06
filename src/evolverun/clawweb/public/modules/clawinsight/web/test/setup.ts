import "@testing-library/jest-dom/vitest";
import { configureClawWebRuntimeConfig } from "@avernet/clawweb-shared/server/db";

configureClawWebRuntimeConfig({
  baas: {
    environments: {
      pre: { apiKey: "test-pre-key", baseUrl: "https://baas-pre.example.test" },
      prod: { apiKey: "test-prod-key", baseUrl: "https://baas-prod.example.test" },
    },
    evolveScriptPaths: {
      dev: "/runner/dev.sh",
      pre: "/runner/pre.sh",
      prod: "/runner/prod.sh",
    },
  },
  repair: {
    ais: {
      snapshotId: 62_380_375,
      snapshots: { pre: 62_510_265, prod: 62_380_375 },
    },
  },
});
