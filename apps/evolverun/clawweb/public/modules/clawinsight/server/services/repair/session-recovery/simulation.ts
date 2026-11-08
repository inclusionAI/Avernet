import { createHash } from "node:crypto";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import { parseRequest } from "./validation.js";
import type { RecoveryRequest } from "./contracts.js";

export type RecoverySimulationCutoff = { messageId: string; traceId?: never; observationId?: never }
  | { traceId: string; observationId?: string; messageId?: never };
export type RecoverySimulation = {
  batchId: string;
  cutoffAtMs: number;
  cutoffNode: RecoverySimulationCutoff;
  inputDigest: string;
};
export type RecoverySimulationAssessment = {
  knowledge?: Array<{ path: string; sha256: string }>;
  decision: "message" | "environment" | "observe" | "insufficient_evidence" | "user_continued";
  summary: string;
  evidence_ids: string[];
  message?: string;
};
export function validateSimulationKnowledge(value: RecoverySimulationAssessment["knowledge"]): boolean {
  return value === undefined || Array.isArray(value) && value.length <= 300 && value.every(item => item && typeof item.path === "string"
    && item.path.length <= 512 && /^[a-f0-9]{64}$/.test(item.sha256));
}
export type RecoverySimulationBatch = {
  batch_id: string;
  cases: Array<{ request: RecoveryRequest; simulation: RecoverySimulation }>;
};
export const simulationReadTools = new Set(["session_read", "trajectory_read", "logs_search", "runtime_inspect"]);
function invalid(message: string): never { throw new SessionRecoveryError(422, "invalid_recovery_simulation", message); }
function text(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) invalid(`${name} 无效`);
  return value;
}
function fields(value: Record<string, unknown>, allowed: string[]): void {
  if (Object.keys(value).some(key => !allowed.includes(key))) invalid("包含未定义的仿真字段");
}
function identifier(value: unknown, name: string): string {
  const id = text(value, name, 255);
  if (!/^[A-Za-z0-9_.:-]+$/.test(id)) invalid(`${name} 无效`);
  return id;
}

/** Bind a historical boundary; evidence is read on demand by the existing tools. */
export function parseRecoverySimulationBatch(raw: unknown, now: number): RecoverySimulationBatch {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) invalid("需要批次对象");
  const body = raw as Record<string, unknown>;
  fields(body, ["batch_id", "cases"]);
  const batchId = text(body.batch_id, "batch_id", 128);
  if (!/^[A-Za-z0-9_-]+$/.test(batchId) || !Array.isArray(body.cases) || !body.cases.length || body.cases.length > 10) invalid("批次需包含 1 到 10 个 case");
  const keys = new Set<string>();
  const cases = body.cases.map((item: Record<string, unknown>) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) invalid("case 必须是对象");
    fields(item, ["request", "cutoff_at_ms", "cutoff_node"]);
    const request = parseRequest(item.request), cutoff = item.cutoff_at_ms;
    if (!Number.isSafeInteger(cutoff) || Number(cutoff) <= 0 || Number(cutoff) > now) invalid("cutoff_at_ms 必须是有效的历史时点");
    if (!item.cutoff_node || typeof item.cutoff_node !== "object" || Array.isArray(item.cutoff_node)) invalid("需要原会话截止节点");
    const node = item.cutoff_node as Record<string, unknown>;
    fields(node, ["message_id", "trace_id", "observation_id"]);
    let cutoffNode: RecoverySimulationCutoff;
    if (request.target.engine === "TE") {
      if (node.message_id !== undefined) invalid("TE 使用轨迹截止节点");
      if (typeof node.trace_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(node.trace_id)) invalid("cutoff_node.trace_id 无效");
      cutoffNode = { traceId: identifier(node.trace_id, "cutoff_node.trace_id"),
        ...(node.observation_id === undefined ? {} : { observationId: identifier(node.observation_id, "cutoff_node.observation_id") }) };
    } else {
      if (node.trace_id !== undefined || node.observation_id !== undefined) invalid("OC/CC 使用原会话截止消息");
      cutoffNode = { messageId: identifier(node.message_id, "cutoff_node.message_id") };
    }
    const payload = JSON.stringify({ request, cutoffAtMs: cutoff, cutoffNode });
    if (Buffer.byteLength(payload) > 262144 || containsSecret(payload)) invalid("case 超出 256 KiB 或含凭据");
    const key = JSON.stringify([request.event_id, request.delivery_key]);
    if (keys.has(key)) invalid("批次不能重复包含同一 case");
    keys.add(key);
    return { request, simulation: { batchId, cutoffAtMs: Number(cutoff), cutoffNode,
      inputDigest: createHash("sha256").update(payload).digest("hex") } };
  });
  return { batch_id: batchId, cases };
}
