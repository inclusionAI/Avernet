import { describe, expect, it } from "vitest";
import { defaultNodeCommand } from "../task-registry.js";

describe("Evolve runtime command defaults", () => {
  it("leaves the model to the task-level model configuration", () => {
    expect(defaultNodeCommand("bench")).not.toContain("--model");
    expect(defaultNodeCommand("bench_plan")).not.toContain("--model");
    expect(defaultNodeCommand("optimize")).not.toContain("--model");
  });
});
