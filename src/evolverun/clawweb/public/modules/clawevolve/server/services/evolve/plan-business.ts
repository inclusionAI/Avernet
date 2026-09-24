export const PLAN_BUSINESS_CONTRACT = "clawevolve.plan-business/v1";

/** Opaque runtime identifiers, never sourced from the user's form fields. */
export function planBusinessResume(value: unknown): { request_id: string; question_sha256: string } {
  if (!isRecord(value) || typeof value.request_id !== "string" || typeof value.question_sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(value.request_id) || !/^[0-9a-f]{64}$/.test(value.question_sha256)) {
    throw new Error("规划交互缺少有效的运行时恢复标识");
  }
  return { request_id: value.request_id, question_sha256: value.question_sha256 };
}

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
