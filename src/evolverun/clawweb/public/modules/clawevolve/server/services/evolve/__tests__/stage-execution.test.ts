import { describe, expect, it } from "vitest";
import { nextStageExecution, type FrozenStageExtensions } from "../stage-execution.js";

const bindings: FrozenStageExtensions = {
  preprocess: { enabled: true, implementationId: "IMPL-PRE" },
  postprocess: { enabled: true, implementationId: "IMPL-POST" },
  replace: { enabled: false, implementationId: "IMPL-REPLACE" },
};

describe("fixed-flow Stage execution slots", () => {
  it("runs enabled preprocess before the built-in Stage and postprocess after it", () => {
    expect(nextStageExecution(bindings, "start")).toMatchObject({ kind: "extension", mode: "preprocess" });
    expect(nextStageExecution(bindings, "preprocess")).toEqual({ kind: "builtin" });
    expect(nextStageExecution(bindings, "builtin")).toMatchObject({ kind: "extension", mode: "postprocess" });
    expect(nextStageExecution(bindings, "postprocess")).toEqual({ kind: "complete" });
  });

  it("uses replace instead of the built-in Stage without changing the fixed flow", () => {
    const replaced: FrozenStageExtensions = {
      replace: { enabled: true, implementationId: "IMPL-REPLACE" },
      postprocess: { enabled: true, implementationId: "IMPL-POST" },
    };
    expect(nextStageExecution(replaced, "start")).toMatchObject({ kind: "extension", mode: "replace" });
    expect(nextStageExecution(replaced, "replace")).toMatchObject({ kind: "extension", mode: "postprocess" });
  });

  it("ignores disabled slots instead of creating fake Stage work", () => {
    expect(nextStageExecution({
      preprocess: { enabled: false, implementationId: "IMPL-PRE" },
      replace: { enabled: false, implementationId: "IMPL-REPLACE" },
    }, "start")).toEqual({ kind: "builtin" });
    expect(nextStageExecution({}, "builtin")).toEqual({ kind: "complete" });
  });
});
