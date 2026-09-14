import type { EvolveRepository } from "../../repositories/evolve-repository.js";
import type { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { BenchTemplateRepository } from "../../repositories/bench-template-repository.js";
import { findOfficialStage, validateJsonSchema } from "./stage-catalog.js";
import { stageTestFixtureInput, type FrozenStageTestFixture } from "./stage-test-fixture.js";

export type FrozenStageTestPlanResult = {
  producer: { taskId: string; stepId: string; userId: string; botId: string; stepType: "plan" | "stage_extension" };
  output: Record<string, unknown>;
  skillTarget?: { name: string; baseline: { ref: string; sha256: string } };
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function identifier(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$/.test(value) && !value.includes("..");
}
function nonempty(value: unknown): value is string {
  return typeof value === "string" && Boolean(value.trim());
}

function validateOutput(value: unknown): asserts value is Record<string, unknown> {
  const schema = findOfficialStage("plan")!.resultSchema;
  const error = validateJsonSchema(schema, value, "Plan output");
  if (error) throw new Error(error);
  const output = value as Record<string, Record<string, unknown>>;
  if (!nonempty(output.spec.version) || !nonempty(output.spec.content)
    || !nonempty(output.benchDomains.trainBenchDomainId) || !nonempty(output.benchDomains.testBenchDomainId)) {
    throw new Error("Plan 来源缺少完整 Spec 或 Bench Domain");
  }
  const cases = output.benchCases.items as Array<Record<string, unknown>>;
  for (const item of cases) {
    const template = item.template as Record<string, unknown>;
    if (!nonempty(item.sourceCaseId) || !nonempty(item.taskId)
      || !nonempty(template.ownerUserId) || !nonempty(template.domainId) || !nonempty(template.templateName)) {
      throw new Error("Plan 来源 Case 或模板引用为空");
    }
  }
}

/** Resolve an explicit platform identity before any test task/resource writes. */
export async function freezeStageTestPlanResult(input: {
  ref: unknown; stage: string; caseInput: Record<string, unknown>; ownerUserId: string; botId: string;
  repo: Pick<EvolveRepository, "findTask" | "findStep">;
  stages: Pick<StageSkillRepository, "findExtensionRun">;
  templates: Pick<BenchTemplateRepository, "findByOwnerDomainAndName"> | null;
  requireSkillCandidate?: boolean;
}): Promise<FrozenStageTestPlanResult> {
  const ref = input.ref;
  if (input.stage !== "optimize") throw new Error("planSourceRef 仅用于 Optimize 独立测试");
  if (!record(ref) || Object.keys(ref).sort().join(",") !== "stepId,taskId"
    || !identifier(ref.taskId) || !identifier(ref.stepId)) {
    throw new Error("planSourceRef 只能包含准确的 taskId、stepId，不能提供 output/path/url");
  }
  if (Object.hasOwn(input.caseInput, "plan_result")) throw new Error("不能混用 planSourceRef 和手工 plan_result");
  const task = await input.repo.findTask(ref.taskId);
  const step = await input.repo.findStep(ref.stepId);
  if (!task || !step || task.user_id !== input.ownerUserId || task.bot_id !== input.botId
    || task.status !== "completed" || step.task_id !== task.task_id || step.status !== "succeeded") {
    throw new Error("Plan 来源必须是同一用户、同一 Bot 已完成任务中的成功 Step");
  }
  if (step.step_type === "stage_extension") {
    const run = await input.stages.findExtensionRun(step.step_id);
    if (!run || run.task_id !== task.task_id || run.stage_key !== "plan" || run.extension_mode !== "replace") {
      throw new Error("Plan 来源 Stage 必须具有本任务的 plan/replace 运行记录");
    }
  } else if (step.step_type !== "plan" || step.command === "stage-test supplied Plan result") {
    throw new Error("Plan 来源必须是默认 Plan 或真实 plan/replace，不能使用测试手填结果");
  }
  const output: unknown = JSON.parse(step.output_json ?? "null");
  validateOutput(output);
  if (!input.templates) throw new Error("Plan 来源模板校验服务不可用");
  const domains = output.benchDomains as Record<string, unknown>;
  for (const item of (output.benchCases as { items: Array<Record<string, unknown>> }).items) {
    const template = item.template as Record<string, unknown>;
    const owner = String(template.ownerUserId);
    const domain = String(template.domainId);
    const published = await input.templates.findByOwnerDomainAndName(owner, domain, String(template.templateName));
    if (owner !== input.ownerUserId || domain !== (item.split === "train" ? domains.trainBenchDomainId : domains.testBenchDomainId)
      || !published || published.owner_user_id !== owner || published.domain_id !== domain
      || published.status !== "published" || published.published_version == null
      || (template.version !== undefined && template.version !== Number(published.published_version))) {
      throw new Error("Plan 来源模板未发布或 owner/domain/version 与引用不一致");
    }
  }
  let skillTarget: FrozenStageTestPlanResult["skillTarget"];
  if (input.requireSkillCandidate) {
    const config: unknown = JSON.parse(task.config_json ?? "null");
    const sourceTest = record(config) && record(config.stageTest) ? config.stageTest : null;
    if (task.task_type === "stage_test" && sourceTest?.stage === "plan" && sourceTest.mode === "replace"
      && record(sourceTest.fixture)) {
      const fixture = sourceTest.fixture as unknown as FrozenStageTestFixture;
      const target = stageTestFixtureInput(task.task_id, fixture);
      skillTarget = { name: target.name, baseline: { ref: fixture.ref, sha256: fixture.sha256 } };
    }
    // A full task's final candidate may include later Optimize changes. It is
    // not a snapshot of what this Plan analyzed, even when the task completed.
    if (!skillTarget) throw new Error("Skill 优化测试需要来源 Plan 的冻结 Skill 候选包，不能直接修改 Bot 安装源");
  }
  return { producer: { taskId: task.task_id, stepId: step.step_id, userId: task.user_id,
    botId: task.bot_id, stepType: step.step_type as "plan" | "stage_extension" }, output: structuredClone(output),
    ...(skillTarget ? { skillTarget } : {}) };
}

/** Read the frozen delivery, not the producer's subsequently revised output. */
export function stageTestPlanDiagnosisInput(value: unknown, ownerUserId: string, botId: string) {
  if (!record(value) || !record(value.producer) || value.producer.userId !== ownerUserId || value.producer.botId !== botId
    || !identifier(value.producer.taskId) || !identifier(value.producer.stepId)
    || !["plan", "stage_extension"].includes(String(value.producer.stepType))) {
    throw new Error("冻结 Plan 来源身份不一致");
  }
  validateOutput(value.output);
  return { taskId: value.producer.taskId, role: "primary", diagnose: null,
    plan: { stepId: value.producer.stepId, output: structuredClone(value.output) } };
}
