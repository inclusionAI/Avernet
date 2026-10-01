import { describe, expect, it } from "vitest";
import { normalizeEvolveModelConfig } from "../model-config.js";

describe("normalizeEvolveModelConfig", () => {
  it("does not invent a public provider default", () => {
    expect(normalizeEvolveModelConfig(undefined)).toEqual({ defaultModel: "", models: [] });
  });

  it("deduplicates host-owned models and keeps the default selectable", () => {
    expect(normalizeEvolveModelConfig({
      defaultModel: "provider/default-model",
      models: ["provider/other-model", "provider/other-model"],
      repairModels: ["provider/repair-model"],
    })).toEqual({
      defaultModel: "provider/default-model",
      models: ["provider/default-model", "provider/other-model"],
      repairModels: ["provider/default-model", "provider/repair-model"],
    });
  });
});
