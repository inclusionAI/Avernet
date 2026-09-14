import { describe, expect, it } from "vitest";
import { defaultNodeCommand } from "../task-registry.js";

describe("Evolve runtime command defaults", () => {
  it("uses the currently supported GLM-5.2 model for Agent-driven Bench stages", () => {
    expect(defaultNodeCommand("bench")).toContain("--model antchat/GLM-5.2");
    expect(defaultNodeCommand("bench_plan")).toContain("--model antchat/GLM-5.2");
    expect(defaultNodeCommand("optimize")).toContain("--model antchat/GLM-5.2");
  });
});
