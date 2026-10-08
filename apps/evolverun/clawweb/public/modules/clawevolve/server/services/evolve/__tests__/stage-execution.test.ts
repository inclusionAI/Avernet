import { describe, expect, it } from "vitest";
import {
  completeStageExtension,
  mergeStageResultPatch,
  nextStageExecution,
  resolveStageImplementationEntrypoint,
  validateStageExtensions,
  type FrozenStageExtensions,
} from "../stage-execution.js";
import { findOfficialStage } from "../stage-catalog.js";

const bindings: FrozenStageExtensions = {
  preprocess: { enabled: true, implementationId: "IMPL-PRE" },
  postprocess: { enabled: true, implementationId: "IMPL-POST" },
  replace: { enabled: false, implementationId: "IMPL-REPLACE" },
};

describe("inspected Stage Skill entrypoints", () => {
  it.each(["SKILL.md", "my-skill/SKILL.md", "implementation/SKILL.md"])("uses the frozen inspected entrypoint %s", (entrypoint) => {
    expect(resolveStageImplementationEntrypoint(JSON.stringify({ manifest: { entrypoint } }))).toBe(entrypoint);
  });

  it("keeps historical validation records without an entrypoint runnable", () => {
    expect(resolveStageImplementationEntrypoint("{}")).toBe("implementation/SKILL.md");
  });

  it.each(["../SKILL.md", "/SKILL.md", "./SKILL.md", "a/b/SKILL.md", "a\\SKILL.md", "", null])("rejects an invalid frozen entrypoint %s", (entrypoint) => {
    expect(() => resolveStageImplementationEntrypoint(JSON.stringify({ manifest: { entrypoint } }))).toThrow("入口路径无效");
  });

  it("does not silently recover corrupt validation records", () => {
    expect(() => resolveStageImplementationEntrypoint("invalid json")).toThrow();
    expect(() => resolveStageImplementationEntrypoint("null")).toThrow("校验记录必须是对象");
  });
});

describe("fixed-flow Stage execution slots", () => {
  it("runs enabled preprocess before the built-in Stage and postprocess after it", () => {
    expect(nextStageExecution(bindings, "start")).toMatchObject({ kind: "extension", mode: "preprocess" });
    expect(nextStageExecution(bindings, "preprocess")).toEqual({ kind: "builtin" });
    expect(nextStageExecution(bindings, "builtin")).toMatchObject({ kind: "extension", mode: "postprocess" });
    expect(nextStageExecution(bindings, "postprocess")).toEqual({ kind: "complete" });
  });

  it("keeps replace inside the built-in Handler slot without changing the fixed flow", () => {
    const replaced: FrozenStageExtensions = {
      replace: { enabled: true, implementationId: "IMPL-REPLACE" },
    };
    expect(nextStageExecution(replaced, "start")).toEqual({ kind: "builtin" });
    expect(nextStageExecution(replaced, "builtin")).toEqual({ kind: "complete" });
  });

  it("rejects replace combined with preprocess or postprocess", () => {
    expect(() => validateStageExtensions({
      replace: { enabled: true, implementationId: "IMPL-REPLACE" },
      postprocess: { enabled: true, implementationId: "IMPL-POST" },
    })).toThrow("整体替换不能与前置处理或后置处理同时启用");
  });

  it("ignores disabled slots instead of creating fake Stage work", () => {
    expect(nextStageExecution({
      preprocess: { enabled: false, implementationId: "IMPL-PRE" },
      replace: { enabled: false, implementationId: "IMPL-REPLACE" },
    }, "start")).toEqual({ kind: "builtin" });
    expect(nextStageExecution({}, "builtin")).toEqual({ kind: "complete" });
  });

  it("lets postprocess return only an allowed result patch", () => {
    expect(mergeStageResultPatch(
      {
        diagnosis: { summary: "原结论", issues: [] },
        cases: { items: [] },
      },
      { diagnosis: { summary: "复核后的结论" } },
      ["diagnosis.summary", "diagnosis.issues"],
    )).toEqual({
      diagnosis: { summary: "复核后的结论", issues: [] },
      cases: { items: [] },
    });

    expect(() => mergeStageResultPatch(
      { diagnosis: { summary: "原结论" }, cases: { items: [] } },
      { cases: { items: [{ caseId: "forbidden" }] } },
      ["diagnosis.summary"],
    )).toThrow("不允许修改结果字段: cases.items");
  });

  it("keeps preprocess as a small receipt instead of a full Stage result", () => {
    const stage = findOfficialStage("diagnose")!;
    expect(completeStageExtension({
      stage,
      mode: "preprocess",
      result: { summary: "补齐了候选 Skill 的说明", changed: true, changed_files: ["SKILL.md"] },
    })).toEqual({
      kind: "preprocessed",
      receipt: { summary: "补齐了候选 Skill 的说明", changed: true, changed_files: ["SKILL.md"] },
    });
    expect(() => completeStageExtension({
      stage,
      mode: "preprocess",
      result: { summary: "越权返回", changed: false, diagnosis: {} },
    })).toThrow("不是允许的字段");
  });

  it("merges a postprocess patch into the platform result", () => {
    const stage = findOfficialStage("diagnose")!;
    const builtinOutput = {
      diagnosis: { summary: "原结论", issues: [] },
      cases: { total: 0, goodCount: 0, badCount: 0, items: [] },
    };
    expect(completeStageExtension({
      stage,
      mode: "postprocess",
      builtinOutput,
      result: { result_patch: { diagnosis: { summary: "业务规则复核后的结论" } } },
    })).toEqual({
      kind: "stage_output",
      output: {
        diagnosis: { summary: "业务规则复核后的结论", issues: [] },
        cases: { total: 0, goodCount: 0, badCount: 0, items: [] },
      },
    });
  });

  it("requires only replace implementations to return the complete Stage output", () => {
    const stage = findOfficialStage("diagnose")!;
    expect(() => completeStageExtension({
      stage,
      mode: "replace",
      result: { diagnosis: { summary: "不完整", issues: [] } },
    })).toThrow("result.cases 为必填项");
  });
});
