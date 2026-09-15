import { describe, expect, it } from "vitest";
import {
  botEvolutionFlow,
  skillHardeningFlow,
  skillEvolutionFlow,
  type FlowStartInput,
} from "../evolution-flow.js";

function fullInput(overrides: Partial<FlowStartInput> = {}): FlowStartInput {
  return {
    taskType: "full",
    inputMode: "diagnose_goal",
    goal: "提升真实任务完成率",
    hasTargetSkill: false,
    ...overrides,
  };
}

describe("fixed Evolve flows", () => {
  it.each([botEvolutionFlow, skillEvolutionFlow])("offers a goal-backed Diagnose toggle without changing $key defaults", (flow) => {
    const input = fullInput({ hasTargetSkill: flow.key === "skill_evolution" });
    expect(flow.describe(input).stages.find((stage) => stage.key === "diagnose"))
      .toMatchObject({ enabled: true, canDisable: true });
    expect(flow.resolveSelection(input, undefined).diagnose).toBe(true);
    expect(() => flow.resolveSelection(input, { diagnose: false }))
      .toThrow("没有直接进化目标时不能关闭诊断");
    expect(flow.resolveSelection({ ...input, inputMode: "direct_goal" }, { diagnose: false }).diagnose)
      .toBe(false);
  });

  it.each([botEvolutionFlow, skillEvolutionFlow])("keeps the $key toggle disabled without a nonempty goal", (flow) => {
    const input = fullInput({ hasTargetSkill: flow.key === "skill_evolution", goal: "  ", inputMode: "direct_goal" });
    expect(flow.describe(input).stages.find((stage) => stage.key === "diagnose"))
      .toMatchObject({ enabled: true, canDisable: false });
    expect(() => flow.resolveSelection(input, { diagnose: false }))
      .toThrow("没有直接进化目标时不能关闭诊断");
  });

  it("describes Bot evolution from the same policy used to validate it", () => {
    const input = fullInput({ inputMode: "direct_goal" });
    const description = botEvolutionFlow.describe(input);

    expect(description.key).toBe("bot_evolution");
    expect(description.stages.map((stage) => [stage.key, stage.enabled, stage.canDisable])).toEqual([
      ["diagnose", false, true],
      ["plan", true, false],
      ["optimize", true, false],
    ]);
    expect(botEvolutionFlow.resolveSelection(input, { diagnose: false })).toEqual({
      diagnose: false,
      hardening: false,
      plan: true,
      optimize: true,
    });
  });

  it("keeps Diagnose mandatory when a full task has no direct evolution goal", () => {
    const input = fullInput({ inputMode: "diagnose_goal" });

    expect(() => botEvolutionFlow.resolveSelection(input, { diagnose: false }))
      .toThrow("没有直接进化目标时不能关闭诊断");
  });

  it("requires a target Skill and the full Plan/Optimize tail for Skill evolution", () => {
    expect(() => skillEvolutionFlow.resolveSelection(fullInput(), {}))
      .toThrow("请选择待进化 Skill");

    const input = fullInput({ hasTargetSkill: true, inputMode: "direct_goal" });
    expect(skillEvolutionFlow.resolveSelection(input, { diagnose: false })).toEqual({
      diagnose: false,
      hardening: false,
      plan: true,
      optimize: true,
    });
    expect(() => skillEvolutionFlow.resolveSelection(input, { plan: false }))
      .toThrow("Skill 自进化必须执行规划和优化");
  });

  it("keeps diagnose-only Bot evolution compatible with the existing flow", () => {
    const input = fullInput({ taskType: "diagnose", inputMode: "diagnose_goal", goal: "" });

    expect(botEvolutionFlow.resolveSelection(input, { plan: false })).toEqual({
      diagnose: true,
      hardening: false,
      plan: false,
      optimize: false,
    });
  });

  it("runs Skill hardening as one independent required Stage", () => {
    const input = fullInput({ taskType: "hardening", hasTargetSkill: true });
    expect(skillHardeningFlow.describe(input)).toMatchObject({
      key: "skill_hardening",
      stages: [{ key: "hardening", enabled: true, canDisable: false }],
    });
    expect(skillHardeningFlow.resolveSelection(input, undefined)).toEqual({
      diagnose: false,
      hardening: true,
      plan: false,
      optimize: false,
    });
    expect(skillHardeningFlow.firstStage(skillHardeningFlow.resolveSelection(input, undefined))).toBe("hardening");
    expect(() => skillHardeningFlow.resolveSelection({ ...input, hasTargetSkill: false }, undefined))
      .toThrow("请选择待加固 Skill");
  });
});
