import { createHash } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";


export const MAX_PAYLOAD_BYTES = 256 * 1024;
export const ACTIVE_STATUSES = ["pending", "executing"] as const;
export const TERMINAL_STATUSES = ["succeeded", "failed", "unknown", "canceled"] as const;

export type AgentToolCallStatus =
  | (typeof ACTIVE_STATUSES)[number]
  | (typeof TERMINAL_STATUSES)[number];
export type AgentToolCallTerminalStatus = (typeof TERMINAL_STATUSES)[number];

export type AgentToolCallRow = {
  id: number;
  call_id: string;
  task_id: string;
  step_id: string;
  execution_id: string;
  authorization_scope_digest: string;
  client_request_id: string;
  tool_name: string;
  operation: string;
  action_id: string | null;
  deadline_at: number | null;
  request_json: string;
  is_write: number;
  status: AgentToolCallStatus;
  lease_owner: string | null;
  lease_expires_at: number | null;
  result_json: string | null;
  result_digest: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type AgentToolCall = {
  id: number;
  callId: string;
  taskId: string;
  stepId: string;
  executionId: string;
  authorizationScopeDigest: string;
  clientRequestId: string;
  toolName: string;
  operation: string;
  actionId: string | null;
  /** Unix epoch seconds; null means the call has no context-wait deadline. */
  deadlineAt: number | null;
  request: unknown;
  isWrite: boolean;
  status: AgentToolCallStatus;
  leaseOwner: string | null;
  /** Unix epoch seconds. */
  leaseExpiresAt: number | null;
  result: unknown | null;
  resultDigest: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  downstreamTraceId: string | null;
  gmtCreate: number | string;
  gmtModified: number | string;
};

/**
 * A synchronous invariant checked while the owning Repair Task row is locked.
 * It must not perform I/O: throwing aborts the surrounding repository transaction.
 */
export type AgentToolCallLedgerContext = Readonly<{
  /** Version read from the same locked Task config as the ledger; null for legacy config. */
  runtimeTargetVersion: number | null;
  taskConfig?: Readonly<Record<string, unknown>>;
  taskStatus?: string;
}>;

export type AgentToolCallLedgerGuard = (
  calls: readonly AgentToolCall[],
  context: AgentToolCallLedgerContext,
) => void;

export type CreateAgentToolCallInput = {
  callId: string;
  taskId: string;
  stepId: string;
  executionId: string;
  authorizationScopeDigest: string;
  clientRequestId: string;
  toolName: string;
  operation: string;
  actionId?: string | null;
  /** Unix epoch seconds. */
  deadlineAt?: number | null;
  isWrite?: boolean;
  /** Already scoped and redacted tool input. Authentication material is rejected. */
  request: unknown;
  /** Optional semantic invariant evaluated atomically before a new call is inserted. */
  toolCallLedgerGuard?: AgentToolCallLedgerGuard;
};

export type CreateAgentToolCallResult = {
  call: AgentToolCall;
  created: boolean;
};

export type ClaimAgentToolCallInput = {
  callId: string;
  executionId: string;
  authorizationScopeDigest: string;
  leaseOwner: string;
  /** Unix epoch seconds and strictly later than now. */
  leaseExpiresAt: number;
  now?: number;
};

export type CompleteAgentToolCallInput = {
  callId: string;
  executionId: string;
  authorizationScopeDigest: string;
  status: AgentToolCallTerminalStatus;
  /** Required for an executing call; omitted only to cancel a still-pending call. */
  leaseOwner?: string;
  result?: unknown;
  errorCode?: string | null;
  errorMessage?: string | null;
  downstreamTraceId?: string | null;
  now?: number;
};

export type CompleteAgentToolCallResult = {
  call: AgentToolCall;
  outcome: "completed" | "duplicate";
};

export type ListAgentToolCallsOptions = {
  stepId?: string;
  callIds?: string[];
  isWrite?: boolean;
  statuses?: AgentToolCallStatus[];
  afterId?: number;
  limit?: number;
  recordKind?: "all" | "source" | "conclusion";
  clientRequestIds?: string[];
};

export class AgentToolCallIdempotencyConflictError extends Error {
  constructor(readonly stepId: string, readonly clientRequestId: string) {
    super(`Repair tool call idempotency conflict: ${stepId}/${clientRequestId}`);
    this.name = "AgentToolCallIdempotencyConflictError";
  }
}

export class AgentToolCallCompletionConflictError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call already has a different terminal result: ${callId}`);
    this.name = "AgentToolCallCompletionConflictError";
  }
}

export class AgentToolCallLeaseLostError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call lease is not owned by this executor: ${callId}`);
    this.name = "AgentToolCallLeaseLostError";
  }
}

export class AgentToolCallScopeMismatchError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call execution or authorization scope mismatch: ${callId}`);
    this.name = "AgentToolCallScopeMismatchError";
  }
}

export class AgentWriteSlotBusyError extends Error {
  constructor(readonly taskId: string, readonly activeCallId: string) {
    super(`Repair task already has an active write tool call: ${taskId}/${activeCallId}`);
    this.name = "AgentWriteSlotBusyError";
  }
}

export class AgentToolCallWorkloadConflictError extends Error {
  constructor(
    readonly taskId: string,
    readonly reason: "task_state" | "current_step" | "execution" | "authorization_scope",
  ) {
    super(`Repair tool call workload is stale: ${taskId}/${reason}`);
    this.name = "AgentToolCallWorkloadConflictError";
  }
}

export class AgentToolCallSecretPersistenceError extends Error {
  constructor(readonly path: string) {
    super(`Repair tool call payload contains forbidden authentication material at ${path}`);
    this.name = "AgentToolCallSecretPersistenceError";
  }
}

export class AgentToolCallDataCorruptionError extends Error {
  constructor(readonly callId: string, field: string) {
    super(`Repair tool call ${callId} has invalid ${field}`);
    this.name = "AgentToolCallDataCorruptionError";
  }
}

export const FORBIDDEN_AUTH_KEYS = new Set([
  "apikey",
  "authcode",
  "authorization",
  "authorizationcode",
  "bearertoken",
  "cfuseauthcode",
  "cookie",
  "cookies",
  "executionticket",
  "llmapikey",
  "modelapikey",
  "modelkey",
  "proxyauthorization",
  "rawticket",
  "setcookie",
  "stepticket",
  "ticket",
  "workloadticket",
]);
export const SIGNED_QUERY_PATTERN = /[?&](?:x[-_]?amz[-_]?(?:credential|signature)|x[-_]?oss[-_]?signature|x[-_]?goog[-_]?signature|ossaccesskeyid|signature|sig)=([^&#\s]+)/giu;

export function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

export function assertSecretFree(value: unknown, path: string, seen = new Set<object>()): void {
  if (typeof value === "string") {
    const containsSignedQueryCredential = [...value.matchAll(SIGNED_QUERY_PATTERN)]
      .some((match) => match[1] !== "[REDACTED]" && match[1] !== "[REDACTED_SECRET_TEXT]");
    if (containsSignedQueryCredential
      || /(?:^|[\s,{;])(?:cookie|authorization|auth[_-]?code|authorization[_-]?code|cfuse[_-]?auth[_-]?code|api[_-]?key|model[_-]?key|(?:raw|execution|step|workload)[_-]?ticket)\s*[:=]\s*\S+/iu.test(value)
      || /\bsk-[A-Za-z0-9_-]{12,}\b/u.test(value)
      || /\bce_repair_[A-Za-z0-9_-]{20,}\b/u.test(value)) {
      throw new AgentToolCallSecretPersistenceError(path);
    }
    return;
  }
  if (value == null || typeof value !== "object") return;
  if (seen.has(value)) return;
  seen.add(value);
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertSecretFree(item, `${path}[${index}]`, seen));
    return;
  }
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    const itemPath = `${path}.${key}`;
    if (FORBIDDEN_AUTH_KEYS.has(normalizedKey(key))) {
      throw new AgentToolCallSecretPersistenceError(itemPath);
    }
    assertSecretFree(item, itemPath, seen);
  }
}

/**
 * Preflights values that will cross the immutable Repair audit boundary.
 * Repository writes still run the same fail-closed check; this export lets
 * optional producers safely degrade before they create a terminal audit row.
 */
export function assertAgentAuditSecretFree(value: unknown, path = "repairAudit"): void {
  assertSecretFree(value, path);
}

export function normalizeJson(value: unknown, path: string, seen = new Set<object>()): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError(`${path} must contain finite JSON numbers`);
    return value;
  }
  if (typeof value !== "object") throw new TypeError(`${path} must be JSON serializable`);
  if (seen.has(value)) throw new TypeError(`${path} must not contain circular references`);
  seen.add(value);
  try {
    if (Array.isArray(value)) {
      return value.map((item, index) => item === undefined ? null : normalizeJson(item, `${path}[${index}]`, seen));
    }
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const item = (value as Record<string, unknown>)[key];
      if (item !== undefined) result[key] = normalizeJson(item, `${path}.${key}`, seen);
    }
    return result;
  } finally {
    seen.delete(value);
  }
}

export function serializePayload(value: unknown, field: string): string {
  assertSecretFree(value, field);
  const serialized = JSON.stringify(normalizeJson(value, field));
  if (serialized === undefined) throw new TypeError(`${field} must be JSON serializable`);
  if (Buffer.byteLength(serialized, "utf8") > MAX_PAYLOAD_BYTES) {
    throw new RangeError(`${field} exceeds ${MAX_PAYLOAD_BYTES} bytes`);
  }
  return serialized;
}

/** Uses the exact serialization, size, and secret rules applied by Repair persistence. */
export function assertAgentAuditPersistable(value: unknown, path = "repairAudit"): void {
  serializePayload(value, path);
}

export function resultDigest(serializedResult: string): string {
  return createHash("sha256").update(serializedResult, "utf8").digest("hex");
}

export function requiredText(value: string, field: string, maxLength: number): string {
  const normalized = value.trim();
  if (!normalized || normalized.length > maxLength || /[\r\n\0]/u.test(normalized)) {
    throw new TypeError(`${field} must be a non-empty string of at most ${maxLength} characters`);
  }
  return normalized;
}

export function optionalText(value: string | null | undefined, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  const normalized = requiredText(value, field, maxLength);
  assertSecretFree(normalized, field);
  return normalized;
}

export function optionalLongText(value: string | null | undefined, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  const normalized = value.trim();
  if (!normalized || normalized.length > maxLength || normalized.includes("\0")) {
    throw new TypeError(`${field} must be a non-empty string of at most ${maxLength} characters`);
  }
  assertSecretFree(normalized, field);
  return normalized;
}

export function positiveInteger(value: number, field: string): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new TypeError(`${field} must be a positive safe integer`);
  return value;
}

export function sha256Digest(value: string, field: string): string {
  const digest = requiredText(value, field, 64).toLowerCase();
  if (!/^[a-f0-9]{64}$/u.test(digest)) throw new TypeError(`${field} must be a SHA-256 digest`);
  return digest;
}

export function boundedLimit(value: number | undefined): number {
  const limit = value ?? 100;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) {
    throw new TypeError("limit must be between 1 and 500");
  }
  return limit;
}

export function assertStatus(value: AgentToolCallStatus): void {
  if (![...ACTIVE_STATUSES, ...TERMINAL_STATUSES].includes(value)) {
    throw new TypeError(`Unsupported Repair tool call status: ${String(value)}`);
  }
}

export function stateList(values: string[], field: string): string[] {
  if (!Array.isArray(values) || values.length === 0 || values.length > 20) {
    throw new TypeError(`${field} must contain between 1 and 20 statuses`);
  }
  return [...new Set(values.map((value) => requiredText(value, field, 32)))];
}

export function currentStepId(configJson: string): string | null {
  try {
    const config = JSON.parse(configJson) as { current?: { stepId?: unknown } };
    return typeof config?.current?.stepId === "string" ? config.current.stepId : null;
  } catch {
    return null;
  }
}

export function workloadIdentity(configJson: string): {
  stepId: string | null;
  executionStepId: string | null;
  executionId: string | null;
  authorizationScopeDigest: string | null;
  runtimeTargetVersion: number | null;
} {
  try {
    const config = JSON.parse(configJson) as {
      current?: { stepId?: unknown };
      execution?: { stepId?: unknown; executionId?: unknown };
      authorizationScopeDigest?: unknown;
      runtimeTarget?: { version?: unknown };
    };
    const runtimeTargetVersion = config?.runtimeTarget?.version;
    return {
      stepId: typeof config?.current?.stepId === "string" ? config.current.stepId : null,
      executionStepId: typeof config?.execution?.stepId === "string"
        ? config.execution.stepId
        : null,
      executionId: typeof config?.execution?.executionId === "string"
        ? config.execution.executionId
        : null,
      authorizationScopeDigest: typeof config?.authorizationScopeDigest === "string"
        ? config.authorizationScopeDigest
        : null,
      runtimeTargetVersion: Number.isSafeInteger(runtimeTargetVersion)
          && Number(runtimeTargetVersion) > 0
        ? Number(runtimeTargetVersion)
        : null,
    };
  } catch {
    return {
      stepId: null,
      executionStepId: null,
      executionId: null,
      authorizationScopeDigest: null,
      runtimeTargetVersion: null,
    };
  }
}

export function supportsForUpdate(db: IDatabase): boolean {
  return db.dbType === "mysql" || db.dbType === "zdas";
}

export function forUpdate(db: IDatabase): string {
  return supportsForUpdate(db) ? " FOR UPDATE" : "";
}

export function parsePayload(raw: string, callId: string, field: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw new AgentToolCallDataCorruptionError(callId, field);
  }
}

export type AgentToolRequestEnvelope = {
  schemaVersion: "repair-tool-request/v1";
  runtimeTargetVersion: number;
  payload: unknown;
};

export function repairToolRequestEnvelope(value: unknown): AgentToolRequestEnvelope | null {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.schemaVersion !== "repair-tool-request/v1"
    || !Number.isSafeInteger(candidate.runtimeTargetVersion)
    || (candidate.runtimeTargetVersion as number) < 1
    || !Object.prototype.hasOwnProperty.call(candidate, "payload")) {
    return null;
  }
  return candidate as AgentToolRequestEnvelope;
}

export function comparableRequestPayload(value: unknown): string {
  return JSON.stringify(normalizeJson(value, "request.payload"));
}

export function sameRequestPayload(row: AgentToolCallRow, input: NormalizedCreateAgentToolCall): boolean {
  if (row.request_json === input.requestJson) return true;
  const stored = parsePayload(row.request_json, row.call_id, "request_json");
  const retried = parsePayload(input.requestJson, input.callId, "request_json");
  // A request digest binds the full actual request, including fields outside
  // legacy envelope.payload. Older requests without a digest keep their contract.
  const storedDigest = stored && typeof stored === "object" ? (stored as Record<string, unknown>).requestDigest : undefined;
  const retriedDigest = retried && typeof retried === "object" ? (retried as Record<string, unknown>).requestDigest : undefined;
  if ((storedDigest !== undefined || retriedDigest !== undefined) && storedDigest !== retriedDigest) return false;
  const storedEnvelope = repairToolRequestEnvelope(stored);
  const retriedEnvelope = repairToolRequestEnvelope(retried);
  if (storedEnvelope == null && retriedEnvelope == null) return false;
  return comparableRequestPayload(storedEnvelope?.payload ?? stored)
    === comparableRequestPayload(retriedEnvelope?.payload ?? retried);
}

export type AgentToolCallTerminalEnvelope = {
  status: AgentToolCallTerminalStatus;
  result: unknown;
  error: { code: string | null; message: string | null } | null;
  downstreamTraceId: string | null;
};

export function terminalEnvelope(input: {
  status: AgentToolCallTerminalStatus;
  result: unknown;
  errorCode: string | null;
  errorMessage: string | null;
  downstreamTraceId: string | null;
}): AgentToolCallTerminalEnvelope {
  return {
    status: input.status,
    result: input.result,
    error: input.errorCode == null && input.errorMessage == null
      ? null
      : { code: input.errorCode, message: input.errorMessage },
    downstreamTraceId: input.downstreamTraceId,
  };
}

export function parseTerminalEnvelope(row: AgentToolCallRow): AgentToolCallTerminalEnvelope | null {
  if (row.result_json == null) return null;
  const parsed = parsePayload(row.result_json, row.call_id, "result_json");
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new AgentToolCallDataCorruptionError(row.call_id, "result_json");
  }
  const envelope = parsed as Record<string, unknown>;
  if (!TERMINAL_STATUSES.includes(envelope.status as AgentToolCallTerminalStatus)
    || envelope.status !== row.status
    || !("result" in envelope)
    || (envelope.downstreamTraceId != null && typeof envelope.downstreamTraceId !== "string")) {
    throw new AgentToolCallDataCorruptionError(row.call_id, "result_json");
  }
  const error = envelope.error;
  if (error != null && (typeof error !== "object" || Array.isArray(error)
    || ((error as Record<string, unknown>).code != null
      && typeof (error as Record<string, unknown>).code !== "string")
    || ((error as Record<string, unknown>).message != null
      && typeof (error as Record<string, unknown>).message !== "string"))) {
    throw new AgentToolCallDataCorruptionError(row.call_id, "result_json");
  }
  return {
    status: envelope.status as AgentToolCallTerminalStatus,
    result: envelope.result,
    error: error == null ? null : {
      code: ((error as Record<string, unknown>).code as string | null | undefined) ?? null,
      message: ((error as Record<string, unknown>).message as string | null | undefined) ?? null,
    },
    downstreamTraceId: (envelope.downstreamTraceId as string | null | undefined) ?? null,
  };
}

export function toToolCall(row: AgentToolCallRow): AgentToolCall {
  const terminal = parseTerminalEnvelope(row);
  return {
    id: row.id,
    callId: row.call_id,
    taskId: row.task_id,
    stepId: row.step_id,
    executionId: row.execution_id,
    authorizationScopeDigest: row.authorization_scope_digest,
    clientRequestId: row.client_request_id,
    toolName: row.tool_name,
    operation: row.operation,
    actionId: row.action_id,
    deadlineAt: row.deadline_at,
    request: parsePayload(row.request_json, row.call_id, "request_json"),
    isWrite: row.is_write === 1,
    status: row.status,
    leaseOwner: row.lease_owner,
    leaseExpiresAt: row.lease_expires_at,
    result: terminal?.result ?? null,
    resultDigest: row.result_digest,
    errorCode: terminal?.error?.code ?? null,
    errorMessage: terminal?.error?.message ?? null,
    downstreamTraceId: terminal?.downstreamTraceId ?? null,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

export type NormalizedCreateAgentToolCall = {
  callId: string;
  taskId: string;
  stepId: string;
  executionId: string;
  authorizationScopeDigest: string;
  clientRequestId: string;
  toolName: string;
  operation: string;
  actionId: string | null;
  deadlineAt: number | null;
  isWrite: boolean;
  requestJson: string;
};

export type AgentTaskStateRow = {
  task_id: string;
  task_type: string;
  status: string;
  config_json: string;
};

export function sameCreateRequest(row: AgentToolCallRow, input: NormalizedCreateAgentToolCall): boolean {
  return row.task_id === input.taskId
    && row.step_id === input.stepId
    && row.execution_id === input.executionId
    && row.authorization_scope_digest === input.authorizationScopeDigest
    && row.client_request_id === input.clientRequestId
    && row.tool_name === input.toolName
    && row.operation === input.operation
    && row.action_id === input.actionId
    && row.is_write === Number(input.isWrite)
    && sameRequestPayload(row, input);
}
