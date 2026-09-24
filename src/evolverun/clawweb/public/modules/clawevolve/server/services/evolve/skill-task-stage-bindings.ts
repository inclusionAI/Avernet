import type { SkillTaskHostAction, SkillTaskHostPreset, SkillTaskHostPresetContext } from "../../contracts/evolve-extension.js";
import { botEvolutionFlow, skillEvolutionFlow, skillHardeningFlow } from "./evolution-flow.js";
import { findOfficialStage, isStageExtensionMode, type StageKey, type StageExtensionMode } from "./stage-catalog.js";
import type { FrozenTaskStageExtensions } from "./stage-execution.js";

export const SKILL_TASK_STAGE_BINDINGS_KEY = "skill_task_stage_bindings";

export type SkillTaskStageBinding = {
  spaceType: "TEAM" | "PERSONAL";
  spaceId: string;
  action: SkillTaskHostAction;
  stage: StageKey;
  mode: StageExtensionMode;
  stageSkillId: string;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** The table is generic; only this consumer defines the binding value schema. */
export function parseSkillTaskStageBindings(value: unknown): SkillTaskStageBinding[] {
  const invalid = (detail: string) => new Error(`${SKILL_TASK_STAGE_BINDINGS_KEY} 配置不合法：${detail}`);
  if (!record(value) || Object.keys(value).some((key) => key !== "bindings") || !Array.isArray(value.bindings)) {
    throw invalid("必须包含 bindings 数组，且不能有未知字段");
  }
  const keys = ["spaceType", "spaceId", "action", "stage", "mode", "stageSkillId"];
  const seen = new Set<string>();
  return value.bindings.map((item, index) => {
    if (!record(item) || Object.keys(item).length !== keys.length
      || Object.keys(item).some((key) => !keys.includes(key))
      || (item.spaceType !== "TEAM" && item.spaceType !== "PERSONAL")
      || typeof item.spaceId !== "string" || !item.spaceId.trim() || item.spaceId !== item.spaceId.trim() || item.spaceId.length > 128
      || (item.action !== "diagnose" && item.action !== "hardening" && item.action !== "optimize")
      || typeof item.stage !== "string" || !isStageExtensionMode(item.mode)
      || typeof item.stageSkillId !== "string" || !/^[1-9][0-9]*$/.test(item.stageSkillId)
      || item.stageSkillId.length > 64) throw invalid(`bindings[${index}] 字段无效`);
    const stage = findOfficialStage(item.stage);
    if (!stage || !stage.extensionModes.includes(item.mode)) throw invalid(`bindings[${index}] Stage 或扩展方式不受支持`);
    const binding = item as SkillTaskStageBinding;
    const flow = binding.action === "hardening" ? skillHardeningFlow
      : binding.action === "optimize" ? skillEvolutionFlow : botEvolutionFlow;
    const selection = flow.resolveSelection({ taskType: binding.action === "optimize" ? "full" : binding.action,
      inputMode: "sessions", goal: "", hasTargetSkill: true }, undefined);
    if (!selection[binding.stage]) throw invalid(`bindings[${index}] 的 Stage 不属于该任务入口`);
    const key = JSON.stringify([binding.spaceType, binding.spaceId, binding.action, binding.stage, binding.mode]);
    if (seen.has(key)) throw invalid(`bindings[${index}] 重复绑定同一位置`);
    seen.add(key);
    return { ...binding };
  });
}

/** Resolve only visible implementations in the target space; freeze at task creation as before. */
export function resolveSkillTaskStageBindings(
  bindings: readonly SkillTaskStageBinding[], context: SkillTaskHostPresetContext,
): SkillTaskHostPreset | null {
  const matched = bindings.filter((binding) => binding.spaceType === context.targetSkill.spaceType
    && binding.spaceId === context.targetSkill.spaceId && binding.action === context.action);
  if (!matched.length) return null;
  const stageExtensions: FrozenTaskStageExtensions = {};
  for (const binding of matched) {
    const implementation = context.availableStageImplementations.filter((item) =>
      item.spaceType === binding.spaceType && item.spaceId === binding.spaceId
      && item.stageSkillId === binding.stageSkillId && item.stage === binding.stage && item.mode === binding.mode
      && item.status === "registered" && item.integrationTestStatus === "test_passed")
      .sort((left, right) => right.versionNo - left.versionNo)[0];
    if (!implementation) return {
      unavailableReason: `空间默认绑定 ${binding.stage}/${binding.mode} 没有可访问且已注册、集成测试通过的实现。`,
      launchDescription: "当前任务使用空间配置的默认 Stage 实现。",
    };
    stageExtensions[binding.stage] ??= {};
    stageExtensions[binding.stage]![binding.mode] = { enabled: true, implementationId: implementation.implementationId };
  }
  return { stageExtensions, unavailableReason: null, launchDescription: "使用空间配置的默认 Stage 实现执行任务。" };
}
