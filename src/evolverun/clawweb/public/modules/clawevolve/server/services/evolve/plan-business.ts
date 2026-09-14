import {
  digestPlanSource, validatePlanSource, type PlanSource,
  PLAN_SOURCE_DESCRIPTOR_VERSION, PLAN_SOURCE_SCHEMA_VERSION,
} from "@avernet/clawweb-shared/server/contracts/plan-source";

export const PLAN_BUSINESS_CONTRACT = "clawevolve.plan-business/v1";

/** Opaque runtime identifiers, never sourced from the user's form fields. */
export function planBusinessResume(value: unknown): { request_id: string; question_sha256: string } {
  if (!isRecord(value) || typeof value.request_id !== "string" || typeof value.question_sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(value.request_id) || !/^[0-9a-f]{64}$/.test(value.question_sha256)) {
    throw new Error("规划交互缺少有效的运行时恢复标识");
  }
  return { request_id: value.request_id, question_sha256: value.question_sha256 };
}

export type PlanSourceProducer = { taskId: string; stepId: string; userId: string; botId: string };
export type FrozenDiagnosePlanSource = {
  descriptorVersion: typeof PLAN_SOURCE_DESCRIPTOR_VERSION;
  schemaVersion: typeof PLAN_SOURCE_SCHEMA_VERSION;
  sourceType: "diagnose";
  digest: string;
  producer: PlanSourceProducer;
  delivery: { type: "inline"; content: PlanSource };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function planBusinessContract(validationJson: string, stage: string, mode: string): string | undefined {
  const validation: unknown = JSON.parse(validationJson);
  if (!isRecord(validation)) throw new Error("Stage 校验记录无效");
  if (validation.executionContract === undefined) return undefined;
  if (validation.executionContract !== PLAN_BUSINESS_CONTRACT || stage !== "plan" || mode !== "replace") {
    throw new Error("Stage 执行协议与点位不一致或不受支持");
  }
  return PLAN_BUSINESS_CONTRACT;
}

/** Freeze the producer's full document, never expand a summary into evidence. */
export function freezeDiagnosePlanSource(
  value: unknown, producer: PlanSourceProducer, output: Record<string, unknown>,
): FrozenDiagnosePlanSource {
  validatePlanSource(value);
  const source = value.source;
  if (source.type !== "diagnose" || source.id !== `diagnose:${producer.taskId}`
    || source.producer !== "clawevolve-diagnose" || source.bot_id !== producer.botId
    || (source.owner_user_id !== undefined && source.owner_user_id !== producer.userId)
    || (source.bot_owner_user_id !== undefined && source.bot_owner_user_id !== producer.userId)) {
    throw new Error("Plan Source 与本次诊断来源身份不一致");
  }
  const cases = isRecord(output.cases) && Array.isArray(output.cases.items) ? output.cases.items : [];
  const reported = new Map(cases.map((item) => isRecord(item) ? [item.caseId, item.type] : [undefined, undefined]));
  if (reported.size !== cases.length || reported.size !== value.cases.length
    || value.cases.some((item) => reported.get(item.case_id) !== item.case_type)) {
    throw new Error("Plan Source 与本次诊断案例不一致");
  }
  if (Buffer.byteLength(JSON.stringify(value), "utf8") > 1024 * 1024) {
    throw new Error("Plan Source 超过 1 MiB 内联交接上限");
  }
  return {
    descriptorVersion: PLAN_SOURCE_DESCRIPTOR_VERSION, schemaVersion: PLAN_SOURCE_SCHEMA_VERSION,
    sourceType: "diagnose", digest: digestPlanSource(value), producer: { ...producer },
    delivery: { type: "inline", content: structuredClone(value) },
  };
}

export function validateFrozenDiagnosePlanSource(
  value: unknown, producer: PlanSourceProducer, output: Record<string, unknown>,
): FrozenDiagnosePlanSource {
  if (!isRecord(value) || !isRecord(value.delivery) || value.delivery.type !== "inline"
    || !isRecord(value.producer)) {
    throw new Error("冻结的 Plan Source 来源引用不一致");
  }
  const identity = value.producer;
  if (Object.entries(producer).some(([key, entry]) => identity[key] !== entry)) {
    throw new Error("冻结的 Plan Source 来源引用不一致");
  }
  const expected = freezeDiagnosePlanSource(value.delivery.content, producer, output);
  if (value.descriptorVersion !== expected.descriptorVersion || value.schemaVersion !== expected.schemaVersion
    || value.sourceType !== expected.sourceType || value.digest !== expected.digest) {
    throw new Error("冻结的 Plan Source 协议或摘要不一致");
  }
  return expected;
}
