import { requirePreparedSkillCandidate, type FrozenSkillTarget } from "./skill-candidate.js";
import { stageTestFixtureInput, type FrozenStageTestFixture } from "./stage-test-fixture.js";
import type { StageKey } from "./stage-catalog.js";
import type { FrozenTaskStageExtensions } from "./stage-execution.js";

/** Core implementation belongs to the native Stage Step, never an extension run. */
export function coreStageBinding(task: { config_json: string }, stepType: string) {
  if (!["diagnose", "plan", "optimize", "hardening"].includes(stepType)) return null;
  const config = JSON.parse(task.config_json) as { stageExtensions?: FrozenTaskStageExtensions };
  const binding = config.stageExtensions?.[stepType as StageKey]?.replace;
  return binding?.enabled && binding.implementationId.trim() ? binding : null;
}

/** All core implementations enter the native Handler through the Runner. */
export function coreStageRequiresAgentMessage(_task: { config_json: string }, _stepType: string): boolean {
  return false;
}

export function preparedStageSkillTarget(task: { task_id: string; config_json: string; task_type?: string }) {
  const config = JSON.parse(task.config_json) as {
    targetSkill?: FrozenSkillTarget;
    stageTest?: { fixture?: FrozenStageTestFixture };
  };
  if (config.targetSkill) return requirePreparedSkillCandidate(config.targetSkill);
  if (task.task_type === "stage_test" && config.stageTest?.fixture) {
    const target = stageTestFixtureInput(task.task_id, config.stageTest.fixture);
    return { workspacePath: target.workspace, skillPath: target.path };
  }
  return null;
}

/** Accept the existing executor's success envelope as well as native Handler output. */
export function stageCoreOutput(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const envelope = value as Record<string, unknown>;
  if (Object.keys(envelope).sort().join(",") === "hitl,result" && envelope.hitl === false
    && envelope.result && typeof envelope.result === "object" && !Array.isArray(envelope.result)) {
    return envelope.result;
  }
  return value;
}
