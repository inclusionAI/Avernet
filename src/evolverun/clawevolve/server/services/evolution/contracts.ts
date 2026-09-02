import { createHash } from "node:crypto";

export const WORKFLOW_EVOLUTION_ANALYSIS_VERSION = "workflow-evolution/v1";
export const WORKFLOW_EVOLUTION_RESULT_VERSION = "workflow-evolution-analysis/v1";
export const WORKFLOW_PATCH_VERSION = "workflow-patch/v1";

export type WorkflowPatchOperation = {
  op: "add" | "replace" | "remove";
  nodeId: string;
  path: string;
  value?: unknown;
};

export type WorkflowPatchProposal = {
  schemaVersion: typeof WORKFLOW_PATCH_VERSION;
  workflowId: string;
  baseSpecDigest: string;
  summary: string;
  operations: WorkflowPatchOperation[];
};

export type WorkflowEvolutionDiagnosis = {
  diagnosisId: string;
  flowIds: string[];
  nodeId: string | null;
  failureSignature: string;
  failureMode: string;
  severity: "low" | "medium" | "high" | "critical";
  reasoning: string;
  evidenceEventIds: string[];
  proposal?: WorkflowPatchProposal;
};

export type WorkflowEvolutionAnalysisResult = {
  schemaVersion: typeof WORKFLOW_EVOLUTION_RESULT_VERSION;
  analysisId: string;
  facts: string[];
  inferences: string[];
  unknowns: string[];
  diagnoses: WorkflowEvolutionDiagnosis[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!isRecord(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

export function digestCanonicalJson(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function requiredText(value: unknown, field: string, maxLength = 4_000): string {
  if (typeof value !== "string" || !value.trim() || value.length > maxLength || value.includes("\0")) {
    throw new Error(`${field} is invalid`);
  }
  return value.trim();
}

function stringArray(value: unknown, field: string, maximum = 200): string[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error(`${field} is invalid`);
  return value.map((item, index) => requiredText(item, `${field}[${index}]`));
}

const ALLOWED_NODE_PATH = /^(?:\/executor\/(?:prompt|message|timeoutMs|timeoutSeconds)|\/executor\/args(?:\/(?:[^/~]|~[01])+)+|\/retry(?:\/(?:[^/~]|~[01])+)+|\/outputContract(?:\/(?:[^/~]|~[01])+)+|\/knowledgeBaseId|\/knowledgeQuery)$/u;
const DANGEROUS_SEGMENTS = new Set(["__proto__", "prototype", "constructor"]);

function decodePointer(path: string): string[] {
  if (!ALLOWED_NODE_PATH.test(path)) throw new Error(`path is not allowed: ${path}`);
  return path.slice(1).split("/").map((part) => {
    const decoded = part.replaceAll("~1", "/").replaceAll("~0", "~");
    if (DANGEROUS_SEGMENTS.has(decoded)) throw new Error(`path is not allowed: ${path}`);
    return decoded;
  });
}

export function validateWorkflowPatchProposal(value: unknown): WorkflowPatchProposal {
  if (!isRecord(value) || value.schemaVersion !== WORKFLOW_PATCH_VERSION) {
    throw new Error("invalid workflow patch proposal version");
  }
  const workflowId = requiredText(value.workflowId, "workflowId", 190);
  const baseSpecDigest = requiredText(value.baseSpecDigest, "baseSpecDigest", 64);
  if (!/^[a-f0-9]{64}$/u.test(baseSpecDigest)) throw new Error("baseSpecDigest is invalid");
  const summary = requiredText(value.summary, "summary", 2_000);
  if (!Array.isArray(value.operations) || value.operations.length < 1 || value.operations.length > 50) {
    throw new Error("operations is invalid");
  }
  const operations = value.operations.map((raw, index): WorkflowPatchOperation => {
    if (!isRecord(raw) || !new Set(["add", "replace", "remove"]).has(String(raw.op))) {
      throw new Error(`operations[${index}].op is invalid`);
    }
    const op = raw.op as WorkflowPatchOperation["op"];
    const nodeId = requiredText(raw.nodeId, `operations[${index}].nodeId`, 190);
    const path = requiredText(raw.path, `operations[${index}].path`, 2_048);
    decodePointer(path);
    const keys = new Set(Object.keys(raw));
    for (const key of ["op", "nodeId", "path", "value"]) keys.delete(key);
    if (keys.size > 0) throw new Error(`operations[${index}] has unsupported fields`);
    if (op === "remove") {
      if (Object.prototype.hasOwnProperty.call(raw, "value")) throw new Error(`operations[${index}].value is forbidden`);
      return { op, nodeId, path };
    }
    if (!Object.prototype.hasOwnProperty.call(raw, "value")) throw new Error(`operations[${index}].value is required`);
    return { op, nodeId, path, value: structuredClone(raw.value) };
  });
  return { schemaVersion: WORKFLOW_PATCH_VERSION, workflowId, baseSpecDigest, summary, operations };
}

function arrayIndex(segment: string, length: number, allowEnd: boolean): number {
  if (allowEnd && segment === "-") return length;
  if (!/^(?:0|[1-9][0-9]*)$/u.test(segment)) throw new Error("patch array index is invalid");
  const index = Number(segment);
  if (index < 0 || index > length || (!allowEnd && index === length)) throw new Error("patch array index is out of bounds");
  return index;
}

function applyRelativePatch(target: Record<string, unknown>, operation: WorkflowPatchOperation): void {
  const segments = decodePointer(operation.path);
  let parent: unknown = target;
  for (const segment of segments.slice(0, -1)) {
    if (Array.isArray(parent)) {
      parent = parent[arrayIndex(segment, parent.length, false)];
    } else if (isRecord(parent) && Object.prototype.hasOwnProperty.call(parent, segment)) {
      parent = parent[segment];
    } else if (operation.op === "add" && isRecord(parent)) {
      parent[segment] = {};
      parent = parent[segment];
    } else {
      throw new Error(`patch path does not exist: ${operation.path}`);
    }
  }
  const key = segments.at(-1)!;
  if (Array.isArray(parent)) {
    if (operation.op === "add") parent.splice(arrayIndex(key, parent.length, true), 0, structuredClone(operation.value));
    else if (operation.op === "replace") parent[arrayIndex(key, parent.length, false)] = structuredClone(operation.value);
    else parent.splice(arrayIndex(key, parent.length, false), 1);
    return;
  }
  if (!isRecord(parent)) throw new Error(`patch parent is not an object: ${operation.path}`);
  if (operation.op !== "add" && !Object.prototype.hasOwnProperty.call(parent, key)) {
    throw new Error(`patch path does not exist: ${operation.path}`);
  }
  if (operation.op === "remove") delete parent[key];
  else parent[key] = structuredClone(operation.value);
}

export function applyWorkflowPatchProposal(
  currentSpec: Record<string, unknown>,
  rawProposal: WorkflowPatchProposal,
): Record<string, unknown> {
  const proposal = validateWorkflowPatchProposal(rawProposal);
  if (digestCanonicalJson(currentSpec) !== proposal.baseSpecDigest) throw new Error("base spec digest mismatch");
  if (currentSpec.id !== proposal.workflowId) throw new Error("workflow id mismatch");
  const output = structuredClone(currentSpec);
  const nodes = Array.isArray(output.nodes) ? output.nodes : [];
  for (const operation of proposal.operations) {
    const node = nodes.find((item) => isRecord(item) && item.id === operation.nodeId);
    if (!isRecord(node)) throw new Error(`proposal node not found: ${operation.nodeId}`);
    applyRelativePatch(node, operation);
  }
  return output;
}

export function validateWorkflowEvolutionAnalysisResult(value: unknown): WorkflowEvolutionAnalysisResult {
  if (!isRecord(value) || value.schemaVersion !== WORKFLOW_EVOLUTION_RESULT_VERSION) {
    throw new Error("invalid workflow evolution result version");
  }
  const analysisId = requiredText(value.analysisId, "analysisId", 64);
  const facts = stringArray(value.facts, "facts");
  const inferences = stringArray(value.inferences, "inferences");
  const unknowns = stringArray(value.unknowns, "unknowns");
  if (!Array.isArray(value.diagnoses) || value.diagnoses.length > 100) throw new Error("diagnoses is invalid");
  const diagnoses = value.diagnoses.map((raw, index): WorkflowEvolutionDiagnosis => {
    if (!isRecord(raw)) throw new Error(`diagnoses[${index}] is invalid`);
    const severity = String(raw.severity);
    if (!new Set(["low", "medium", "high", "critical"]).has(severity)) throw new Error(`diagnoses[${index}].severity is invalid`);
    return {
      diagnosisId: requiredText(raw.diagnosisId, `diagnoses[${index}].diagnosisId`, 64),
      flowIds: stringArray(raw.flowIds, `diagnoses[${index}].flowIds`, 500),
      nodeId: raw.nodeId == null ? null : requiredText(raw.nodeId, `diagnoses[${index}].nodeId`, 190),
      failureSignature: requiredText(raw.failureSignature, `diagnoses[${index}].failureSignature`, 512),
      failureMode: requiredText(raw.failureMode, `diagnoses[${index}].failureMode`, 64),
      severity: severity as WorkflowEvolutionDiagnosis["severity"],
      reasoning: requiredText(raw.reasoning, `diagnoses[${index}].reasoning`, 8_000),
      evidenceEventIds: stringArray(raw.evidenceEventIds, `diagnoses[${index}].evidenceEventIds`, 1_000),
      ...(raw.proposal == null ? {} : { proposal: validateWorkflowPatchProposal(raw.proposal) }),
    };
  });
  if (new Set(diagnoses.map((item) => item.diagnosisId)).size !== diagnoses.length) throw new Error("duplicate diagnosisId");
  return { schemaVersion: WORKFLOW_EVOLUTION_RESULT_VERSION, analysisId, facts, inferences, unknowns, diagnoses };
}
