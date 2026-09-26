import { createHash } from "node:crypto";

export const PLAN_SOURCE_SCHEMA_VERSION = "plan-source/v2" as const;
export const PLAN_SOURCE_DESCRIPTOR_VERSION = "plan-source-descriptor/v2" as const;

export type PlanSourceCase = {
  case_id: string;
  case_type: "good" | "bad" | "prospective";
  case_split?: string;
  ordinal?: number;
  session_id?: string;
  task_index?: number;
  query: string;
  context?: Record<string, unknown>;
  evidence: Record<string, unknown>;
  analysis?: Record<string, unknown>;
  planning_hints?: Record<string, unknown>;
};

export type PlanSource = {
  schema_version: typeof PLAN_SOURCE_SCHEMA_VERSION;
  generated_at: string;
  source: {
    type: "diagnose" | "insight_improvement" | "direct_goal";
    id: string;
    producer: string;
    adapter_version?: string;
    owner_user_id?: string;
    bot_owner_user_id?: string;
    bot_id: string;
    version: string;
    frozen_at?: string;
  };
  problem: { title: string; user_guidance: string | null };
  cases: PlanSourceCase[];
  analysis: { case_distribution: Record<string, unknown>; root_cause_clusters: unknown[] };
  planning_hints: Record<string, unknown>;
  extensions: Record<string, unknown>;
};

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) throw new Error("Plan Source 不能包含非有限数字");
  if (Object.is(value, -0) || value === 0) return "0";
  const [rawMantissa, rawExponent] = value.toExponential(16).split("e");
  const mantissa = rawMantissa.replace(/(?:\.0+|(?:(\.[0-9]*?)0+))$/, "$1");
  return `${mantissa}e${Number(rawExponent)}`;
}

function compareUnicodeCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) as number);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) as number);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

/** Cross-runtime canonical JSON used by ClawWeb and the Python Resolver. */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return canonicalNumber(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, child]) => child !== undefined)
      .sort(([left], [right]) => compareUnicodeCodePoints(left, right));
    return `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`).join(",")}}`;
  }
  throw new Error(`Plan Source 不能包含 ${typeof value}`);
}

export function digestJson(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

export function digestPlanSource(source: PlanSource): string {
  validatePlanSource(source);
  return digestJson(source);
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} 必须是非空字符串`);
  return value;
}

export function validatePlanSource(value: unknown): asserts value is PlanSource {
  const sourceDocument = record(value, "Plan Source");
  if (sourceDocument.schema_version !== PLAN_SOURCE_SCHEMA_VERSION) {
    throw new Error(`schema_version 不支持: ${String(sourceDocument.schema_version ?? "")}`);
  }
  requiredString(sourceDocument.generated_at, "generated_at");
  const source = record(sourceDocument.source, "source");
  if (!new Set(["diagnose", "insight_improvement", "direct_goal"]).has(String(source.type))) {
    throw new Error("source.type 不支持");
  }
  requiredString(source.id, "source.id");
  requiredString(source.producer, "source.producer");
  requiredString(source.bot_id, "source.bot_id");
  requiredString(source.version, "source.version");
  const problem = record(sourceDocument.problem, "problem");
  requiredString(problem.title, "problem.title");
  if (problem.user_guidance !== null && typeof problem.user_guidance !== "string") {
    throw new Error("problem.user_guidance 必须是字符串或 null");
  }
  if (!Array.isArray(sourceDocument.cases) || sourceDocument.cases.length === 0) {
    throw new Error("cases 必须是非空数组");
  }
  const caseIds = new Set<string>();
  for (const [index, item] of sourceDocument.cases.entries()) {
    const sourceCase = record(item, `cases[${index}]`);
    const caseId = requiredString(sourceCase.case_id, `cases[${index}].case_id`);
    if (caseIds.has(caseId)) throw new Error(`cases[${index}].case_id 重复`);
    caseIds.add(caseId);
    if (!new Set(["good", "bad", "prospective"]).has(String(sourceCase.case_type))) {
      throw new Error(`cases[${index}].case_type 不支持`);
    }
    if (sourceCase.case_type !== "prospective") {
      requiredString(sourceCase.session_id, `cases[${index}].session_id`);
    } else if (sourceCase.session_id != null && sourceCase.session_id !== "") {
      throw new Error(`cases[${index}].session_id must be absent for prospective cases`);
    }
    requiredString(sourceCase.query, `cases[${index}].query`);
    record(sourceCase.evidence, `cases[${index}].evidence`);
  }
  const analysis = record(sourceDocument.analysis, "analysis");
  record(analysis.case_distribution, "analysis.case_distribution");
  if (!Array.isArray(analysis.root_cause_clusters)) throw new Error("analysis.root_cause_clusters 必须是数组");
  record(sourceDocument.planning_hints, "planning_hints");
  record(sourceDocument.extensions, "extensions");
}
