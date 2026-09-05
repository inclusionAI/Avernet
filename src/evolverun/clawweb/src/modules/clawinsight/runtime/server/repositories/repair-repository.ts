import { createHash } from "node:crypto";
import type { IDatabase } from "../db.js";
import { nowForDb } from "../db.js";

const MAX_PAYLOAD_BYTES = 256 * 1024;
const ACTIVE_STATUSES = ["pending", "executing"] as const;
const TERMINAL_STATUSES = ["succeeded", "failed", "unknown", "canceled"] as const;

export type RepairToolCallStatus =
  | (typeof ACTIVE_STATUSES)[number]
  | (typeof TERMINAL_STATUSES)[number];
export type RepairToolCallTerminalStatus = (typeof TERMINAL_STATUSES)[number];
export type RepairPhase = "repair_plan" | "repair_apply";

type RepairToolCallRow = {
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
  status: RepairToolCallStatus;
  lease_owner: string | null;
  lease_expires_at: number | null;
  result_json: string | null;
  result_digest: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type RepairToolCall = {
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
  status: RepairToolCallStatus;
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
export type RepairToolCallLedgerContext = Readonly<{
  /** Version read from the same locked Task config as the ledger; null for legacy config. */
  runtimeTargetVersion: number | null;
}>;

export type RepairToolCallLedgerGuard = (
  calls: readonly RepairToolCall[],
  context: RepairToolCallLedgerContext,
) => void;

export type CreateRepairToolCallInput = {
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
  toolCallLedgerGuard?: RepairToolCallLedgerGuard;
};

export type CreateRepairToolCallResult = {
  call: RepairToolCall;
  created: boolean;
};

export type ClaimRepairToolCallInput = {
  callId: string;
  executionId: string;
  authorizationScopeDigest: string;
  leaseOwner: string;
  /** Unix epoch seconds and strictly later than now. */
  leaseExpiresAt: number;
  now?: number;
};

export type CompleteRepairToolCallInput = {
  callId: string;
  executionId: string;
  authorizationScopeDigest: string;
  status: RepairToolCallTerminalStatus;
  /** Required for an executing call; omitted only to cancel a still-pending call. */
  leaseOwner?: string;
  result?: unknown;
  errorCode?: string | null;
  errorMessage?: string | null;
  downstreamTraceId?: string | null;
  now?: number;
};

export type CompleteRepairToolCallResult = {
  call: RepairToolCall;
  outcome: "completed" | "duplicate";
};

export type ListRepairToolCallsOptions = {
  stepId?: string;
  callIds?: string[];
  isWrite?: boolean;
  statuses?: RepairToolCallStatus[];
  afterId?: number;
  limit?: number;
  recordKind?: "all" | "source" | "conclusion";
  clientRequestIds?: string[];
};

export type CreateRepairTaskWithStepInput = {
  task: {
    taskId: string;
    userId: string;
    botId: string;
    taskName: string;
    remark?: string | null;
    config: unknown;
    createdBy: string;
  };
  step: {
    stepId: string;
    stepType: RepairPhase;
    stepNo: number;
    roundNo?: number | null;
    command: string;
  };
};

export type TransitionRepairStepInput = {
  taskId: string;
  expectedTaskStatuses: string[];
  expectedCurrentStepId: string;
  /** Digest of the exact config_json observed by the caller; prevents stale config overwrite. */
  expectedTaskConfigDigest?: string;
  previousStep?: {
    stepId: string;
    expectedStatuses: string[];
    status: string;
    output?: unknown;
    summary?: string | null;
    errorCode?: string | null;
    errorMessage?: string | null;
    retryable?: boolean | null;
  };
  nextTaskStatus: string;
  nextConfig: unknown;
  /** Excludes only the report call that is atomically driving this transition. */
  ignoreActiveToolCallId?: string;
  nextStep?: {
    stepId: string;
    stepType: RepairPhase;
    stepNo: number;
    roundNo?: number | null;
    command: string;
  };
  /** Reuses only the current Step's already-persisted AIStudio job id. */
  reuseJobId?: string | null;
  /** Optional semantic invariant evaluated atomically before Step/Task mutation. */
  toolCallLedgerGuard?: RepairToolCallLedgerGuard;
};

export type TransitionRepairStepResult =
  | { outcome: "transitioned" }
  | {
    outcome: "conflict";
    reason: "task_state" | "task_config" | "current_step" | "previous_step" | "active_tool_calls" | "reuse_job";
  };

export type CompareAndSetRepairTaskConfigInput = {
  taskId: string;
  expectedTaskStatuses: string[];
  expectedCurrentStepId: string;
  expectedTaskConfigDigest: string;
  nextConfig: unknown;
};

export class RepairToolCallIdempotencyConflictError extends Error {
  constructor(readonly stepId: string, readonly clientRequestId: string) {
    super(`Repair tool call idempotency conflict: ${stepId}/${clientRequestId}`);
    this.name = "RepairToolCallIdempotencyConflictError";
  }
}

export class RepairToolCallCompletionConflictError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call already has a different terminal result: ${callId}`);
    this.name = "RepairToolCallCompletionConflictError";
  }
}

export class RepairToolCallLeaseLostError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call lease is not owned by this executor: ${callId}`);
    this.name = "RepairToolCallLeaseLostError";
  }
}

export class RepairToolCallScopeMismatchError extends Error {
  constructor(readonly callId: string) {
    super(`Repair tool call execution or authorization scope mismatch: ${callId}`);
    this.name = "RepairToolCallScopeMismatchError";
  }
}

export class RepairWriteSlotBusyError extends Error {
  constructor(readonly taskId: string, readonly activeCallId: string) {
    super(`Repair task already has an active write tool call: ${taskId}/${activeCallId}`);
    this.name = "RepairWriteSlotBusyError";
  }
}

export class RepairToolCallWorkloadConflictError extends Error {
  constructor(
    readonly taskId: string,
    readonly reason: "task_state" | "current_step" | "execution" | "authorization_scope",
  ) {
    super(`Repair tool call workload is stale: ${taskId}/${reason}`);
    this.name = "RepairToolCallWorkloadConflictError";
  }
}

export class RepairToolCallSecretPersistenceError extends Error {
  constructor(readonly path: string) {
    super(`Repair tool call payload contains forbidden authentication material at ${path}`);
    this.name = "RepairToolCallSecretPersistenceError";
  }
}

export class RepairToolCallDataCorruptionError extends Error {
  constructor(readonly callId: string, field: string) {
    super(`Repair tool call ${callId} has invalid ${field}`);
    this.name = "RepairToolCallDataCorruptionError";
  }
}

const FORBIDDEN_AUTH_KEYS = new Set([
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
const SIGNED_QUERY_CREDENTIAL = /[?&](?:x[-_]?amz[-_]?(?:credential|signature)|x[-_]?oss[-_]?signature|x[-_]?goog[-_]?signature|ossaccesskeyid|signature|sig)=([^&#\s]+)/giu;

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function assertSecretFree(value: unknown, path: string, seen = new Set<object>()): void {
  if (typeof value === "string") {
    const containsSignedQueryCredential = [...value.matchAll(SIGNED_QUERY_CREDENTIAL)]
      .some((match) => match[1] !== "[REDACTED]" && match[1] !== "[REDACTED_SECRET_TEXT]");
    if (containsSignedQueryCredential
      || /(?:^|[\s,{;])(?:cookie|authorization|auth[_-]?code|authorization[_-]?code|cfuse[_-]?auth[_-]?code|api[_-]?key|model[_-]?key|(?:raw|execution|step|workload)[_-]?ticket)\s*[:=]\s*\S+/iu.test(value)
      || /\bsk-[A-Za-z0-9_-]{12,}\b/u.test(value)
      || /\bce_repair_[A-Za-z0-9_-]{20,}\b/u.test(value)) {
      throw new RepairToolCallSecretPersistenceError(path);
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
      throw new RepairToolCallSecretPersistenceError(itemPath);
    }
    assertSecretFree(item, itemPath, seen);
  }
}

/**
 * Preflights values that will cross the immutable Repair audit boundary.
 * Repository writes still run the same fail-closed check; this export lets
 * optional producers safely degrade before they create a terminal audit row.
 */
export function assertRepairAuditSecretFree(value: unknown, path = "repairAudit"): void {
  assertSecretFree(value, path);
}

function normalizeJson(value: unknown, path: string, seen = new Set<object>()): unknown {
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

function serializePayload(value: unknown, field: string): string {
  assertSecretFree(value, field);
  const serialized = JSON.stringify(normalizeJson(value, field));
  if (serialized === undefined) throw new TypeError(`${field} must be JSON serializable`);
  if (Buffer.byteLength(serialized, "utf8") > MAX_PAYLOAD_BYTES) {
    throw new RangeError(`${field} exceeds ${MAX_PAYLOAD_BYTES} bytes`);
  }
  return serialized;
}

/** Uses the exact serialization, size, and secret rules applied by Repair persistence. */
export function assertRepairAuditPersistable(value: unknown, path = "repairAudit"): void {
  serializePayload(value, path);
}

function resultDigest(serializedResult: string): string {
  return createHash("sha256").update(serializedResult, "utf8").digest("hex");
}

function requiredText(value: string, field: string, maxLength: number): string {
  const normalized = value.trim();
  if (!normalized || normalized.length > maxLength || /[\r\n\0]/u.test(normalized)) {
    throw new TypeError(`${field} must be a non-empty string of at most ${maxLength} characters`);
  }
  return normalized;
}

function optionalText(value: string | null | undefined, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  const normalized = requiredText(value, field, maxLength);
  assertSecretFree(normalized, field);
  return normalized;
}

function optionalLongText(value: string | null | undefined, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  const normalized = value.trim();
  if (!normalized || normalized.length > maxLength || normalized.includes("\0")) {
    throw new TypeError(`${field} must be a non-empty string of at most ${maxLength} characters`);
  }
  assertSecretFree(normalized, field);
  return normalized;
}

function positiveInteger(value: number, field: string): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new TypeError(`${field} must be a positive safe integer`);
  return value;
}

function sha256Digest(value: string, field: string): string {
  const digest = requiredText(value, field, 64).toLowerCase();
  if (!/^[a-f0-9]{64}$/u.test(digest)) throw new TypeError(`${field} must be a SHA-256 digest`);
  return digest;
}

function boundedLimit(value: number | undefined): number {
  const limit = value ?? 100;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) {
    throw new TypeError("limit must be between 1 and 500");
  }
  return limit;
}

function assertStatus(value: RepairToolCallStatus): void {
  if (![...ACTIVE_STATUSES, ...TERMINAL_STATUSES].includes(value)) {
    throw new TypeError(`Unsupported Repair tool call status: ${String(value)}`);
  }
}

function repairStep(input: CreateRepairTaskWithStepInput["step"], field = "step") {
  const step = {
    stepId: requiredText(input.stepId, `${field}.stepId`, 64),
    stepType: input.stepType,
    stepNo: positiveInteger(input.stepNo, `${field}.stepNo`),
    roundNo: input.roundNo ?? null,
    command: requiredText(input.command, `${field}.command`, 1_000),
  };
  if (step.stepType !== "repair_plan" && step.stepType !== "repair_apply") {
    throw new TypeError(`${field}.stepType must be a Repair phase`);
  }
  if (step.roundNo != null) positiveInteger(step.roundNo, `${field}.roundNo`);
  return step;
}

function stateList(values: string[], field: string): string[] {
  if (!Array.isArray(values) || values.length === 0 || values.length > 20) {
    throw new TypeError(`${field} must contain between 1 and 20 statuses`);
  }
  return [...new Set(values.map((value) => requiredText(value, field, 32)))];
}

function currentStepId(configJson: string): string | null {
  try {
    const config = JSON.parse(configJson) as { current?: { stepId?: unknown } };
    return typeof config?.current?.stepId === "string" ? config.current.stepId : null;
  } catch {
    return null;
  }
}

function workloadIdentity(configJson: string): {
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

function supportsForUpdate(db: IDatabase): boolean {
  return db.dbType === "mysql" || db.dbType === "zdas";
}

function forUpdate(db: IDatabase): string {
  return supportsForUpdate(db) ? " FOR UPDATE" : "";
}

function parsePayload(raw: string, callId: string, field: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw new RepairToolCallDataCorruptionError(callId, field);
  }
}

type RepairToolRequestEnvelope = {
  schemaVersion: "repair-tool-request/v1";
  runtimeTargetVersion: number;
  payload: unknown;
};

function repairToolRequestEnvelope(value: unknown): RepairToolRequestEnvelope | null {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.schemaVersion !== "repair-tool-request/v1"
    || !Number.isSafeInteger(candidate.runtimeTargetVersion)
    || (candidate.runtimeTargetVersion as number) < 1
    || !Object.prototype.hasOwnProperty.call(candidate, "payload")) {
    return null;
  }
  return candidate as RepairToolRequestEnvelope;
}

function comparableRequestPayload(value: unknown): string {
  return JSON.stringify(normalizeJson(value, "request.payload"));
}

function sameRequestPayload(row: RepairToolCallRow, input: NormalizedCreateRepairToolCall): boolean {
  if (row.request_json === input.requestJson) return true;
  const stored = parsePayload(row.request_json, row.call_id, "request_json");
  const retried = parsePayload(input.requestJson, input.callId, "request_json");
  const storedEnvelope = repairToolRequestEnvelope(stored);
  const retriedEnvelope = repairToolRequestEnvelope(retried);
  if (storedEnvelope == null && retriedEnvelope == null) return false;
  return comparableRequestPayload(storedEnvelope?.payload ?? stored)
    === comparableRequestPayload(retriedEnvelope?.payload ?? retried);
}

type RepairToolCallTerminalEnvelope = {
  status: RepairToolCallTerminalStatus;
  result: unknown;
  error: { code: string | null; message: string | null } | null;
  downstreamTraceId: string | null;
};

function terminalEnvelope(input: {
  status: RepairToolCallTerminalStatus;
  result: unknown;
  errorCode: string | null;
  errorMessage: string | null;
  downstreamTraceId: string | null;
}): RepairToolCallTerminalEnvelope {
  return {
    status: input.status,
    result: input.result,
    error: input.errorCode == null && input.errorMessage == null
      ? null
      : { code: input.errorCode, message: input.errorMessage },
    downstreamTraceId: input.downstreamTraceId,
  };
}

function parseTerminalEnvelope(row: RepairToolCallRow): RepairToolCallTerminalEnvelope | null {
  if (row.result_json == null) return null;
  const parsed = parsePayload(row.result_json, row.call_id, "result_json");
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new RepairToolCallDataCorruptionError(row.call_id, "result_json");
  }
  const envelope = parsed as Record<string, unknown>;
  if (!TERMINAL_STATUSES.includes(envelope.status as RepairToolCallTerminalStatus)
    || envelope.status !== row.status
    || !("result" in envelope)
    || (envelope.downstreamTraceId != null && typeof envelope.downstreamTraceId !== "string")) {
    throw new RepairToolCallDataCorruptionError(row.call_id, "result_json");
  }
  const error = envelope.error;
  if (error != null && (typeof error !== "object" || Array.isArray(error)
    || ((error as Record<string, unknown>).code != null
      && typeof (error as Record<string, unknown>).code !== "string")
    || ((error as Record<string, unknown>).message != null
      && typeof (error as Record<string, unknown>).message !== "string"))) {
    throw new RepairToolCallDataCorruptionError(row.call_id, "result_json");
  }
  return {
    status: envelope.status as RepairToolCallTerminalStatus,
    result: envelope.result,
    error: error == null ? null : {
      code: ((error as Record<string, unknown>).code as string | null | undefined) ?? null,
      message: ((error as Record<string, unknown>).message as string | null | undefined) ?? null,
    },
    downstreamTraceId: (envelope.downstreamTraceId as string | null | undefined) ?? null,
  };
}

function toToolCall(row: RepairToolCallRow): RepairToolCall {
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

type NormalizedCreateRepairToolCall = {
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

type RepairTaskStateRow = {
  task_id: string;
  task_type: string;
  status: string;
  config_json: string;
};

type RepairStepStateRow = {
  step_id: string;
  task_id: string;
  status: string;
  bot_run_id: string | null;
};

class TransitionMutationConflict extends Error {
  constructor(readonly reason: "previous_step" | "task_state") {
    super(reason);
  }
}

function sameCreateRequest(row: RepairToolCallRow, input: NormalizedCreateRepairToolCall): boolean {
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

/**
 * Owns Repair tool-call persistence semantics: scoped idempotency, leased CAS
 * execution, immutable terminal facts, and secret-free audit payloads.
 */
export class RepairRepository {
  constructor(private readonly db: IDatabase) {}

  async createTaskWithStep(input: CreateRepairTaskWithStepInput): Promise<void> {
    const task = {
      taskId: requiredText(input.task.taskId, "task.taskId", 64),
      userId: requiredText(input.task.userId, "task.userId", 128),
      botId: requiredText(input.task.botId, "task.botId", 128),
      taskName: requiredText(input.task.taskName, "task.taskName", 128),
      remark: optionalLongText(input.task.remark, "task.remark", 1_000),
      configJson: serializePayload(input.task.config, "task.config"),
      createdBy: requiredText(input.task.createdBy, "task.createdBy", 128),
    };
    const step = repairStep(input.step);
    if (currentStepId(task.configJson) !== step.stepId) {
      throw new TypeError("task.config.current.stepId must match step.stepId");
    }
    await this.db.transaction(async (tx) => {
      const now = nowForDb(tx.dbType);
      await tx.exec(
        `INSERT INTO ce_tasks
         (task_id, task_type, task_name, remark, user_id, bot_id, status, config_json,
          error_message, created_by, gmt_create, gmt_modified)
         VALUES (?, 'repair', ?, ?, ?, ?, 'pending', ?, NULL, ?, ?, ?)`,
        [task.taskId, task.taskName, task.remark, task.userId, task.botId,
          task.configJson, task.createdBy, now, now],
      );
      await tx.exec(
        `INSERT INTO ce_steps
         (step_id, task_id, step_type, step_no, round_no, command, status,
          gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)`,
        [step.stepId, task.taskId, step.stepType, step.stepNo, step.roundNo,
          step.command, now, now],
      );
    });
  }

  async transitionStep(input: TransitionRepairStepInput): Promise<TransitionRepairStepResult> {
    const taskId = requiredText(input.taskId, "taskId", 64);
    const expectedTaskStatuses = stateList(input.expectedTaskStatuses, "expectedTaskStatuses");
    const expectedCurrentStepId = requiredText(
      input.expectedCurrentStepId, "expectedCurrentStepId", 64,
    );
    const expectedTaskConfigDigest = input.expectedTaskConfigDigest == null
      ? null
      : sha256Digest(input.expectedTaskConfigDigest, "expectedTaskConfigDigest");
    const nextTaskStatus = requiredText(input.nextTaskStatus, "nextTaskStatus", 32);
    const nextConfigJson = serializePayload(input.nextConfig, "nextConfig");
    const ignoreActiveToolCallId = optionalText(
      input.ignoreActiveToolCallId, "ignoreActiveToolCallId", 64,
    );
    const nextStep = input.nextStep == null ? null : repairStep(input.nextStep, "nextStep");
    if (nextStep != null && currentStepId(nextConfigJson) !== nextStep.stepId) {
      throw new TypeError("nextConfig.current.stepId must match nextStep.stepId");
    }
    const reuseJobId = optionalText(input.reuseJobId, "reuseJobId", 255);
    if (reuseJobId != null && nextStep == null) {
      throw new TypeError("reuseJobId requires nextStep");
    }
    const previousStep = input.previousStep == null ? null : {
      stepId: requiredText(input.previousStep.stepId, "previousStep.stepId", 64),
      expectedStatuses: stateList(input.previousStep.expectedStatuses, "previousStep.expectedStatuses"),
      status: requiredText(input.previousStep.status, "previousStep.status", 32),
      outputJson: input.previousStep.output === undefined
        ? undefined
        : serializePayload(input.previousStep.output, "previousStep.output"),
      summary: input.previousStep.summary === undefined
        ? undefined
        : optionalLongText(input.previousStep.summary, "previousStep.summary", 4_000),
      errorCode: input.previousStep.errorCode === undefined
        ? undefined
        : optionalText(input.previousStep.errorCode, "previousStep.errorCode", 128),
      errorMessage: input.previousStep.errorMessage === undefined
        ? undefined
        : optionalLongText(input.previousStep.errorMessage, "previousStep.errorMessage", 4_000),
      retryable: input.previousStep.retryable,
    };
    if (previousStep != null && previousStep.stepId !== expectedCurrentStepId) {
      throw new TypeError("previousStep.stepId must match expectedCurrentStepId");
    }
    if (input.toolCallLedgerGuard != null && typeof input.toolCallLedgerGuard !== "function") {
      throw new TypeError("toolCallLedgerGuard must be a function");
    }

    try {
      return await this.db.transaction(async (tx) => {
        const task = await this.lockTask(tx, taskId);
        if (!task || task.task_type !== "repair" || !expectedTaskStatuses.includes(task.status)) {
          return { outcome: "conflict", reason: "task_state" } as const;
        }
        const workload = workloadIdentity(task.config_json);
        if (workload.stepId !== expectedCurrentStepId) {
          return { outcome: "conflict", reason: "current_step" } as const;
        }
        if (expectedTaskConfigDigest != null
          && resultDigest(task.config_json) !== expectedTaskConfigDigest) {
          return { outcome: "conflict", reason: "task_config" } as const;
        }
        if (input.toolCallLedgerGuard != null) {
          input.toolCallLedgerGuard(
            await this.toolCallLedger(tx, taskId, expectedCurrentStepId),
            { runtimeTargetVersion: workload.runtimeTargetVersion },
          );
        }
        const activeClauses = ["task_id = ?", "status IN ('pending', 'executing')"];
        const activeParams: unknown[] = [taskId];
        if (ignoreActiveToolCallId != null) {
          activeClauses.push("call_id <> ?");
          activeParams.push(ignoreActiveToolCallId);
        }
        const activeToolCall = (await tx.query<{ id: number }>(
          `SELECT id FROM ce_repair_tool_calls
            WHERE ${activeClauses.join(" AND ")}
            ORDER BY id ASC LIMIT 1${forUpdate(tx)}`,
          activeParams,
        ))[0];
        if (activeToolCall) {
          return { outcome: "conflict", reason: "active_tool_calls" } as const;
        }

        let previousRow: RepairStepStateRow | null = null;
        if (previousStep != null || reuseJobId != null) {
          const previousStepId = previousStep?.stepId ?? expectedCurrentStepId;
          previousRow = (await tx.query<RepairStepStateRow>(
            `SELECT step_id, task_id, status, bot_run_id FROM ce_steps WHERE step_id = ?${forUpdate(tx)}`,
            [previousStepId],
          ))[0] ?? null;
          if (previousStep != null && (!previousRow || previousRow.task_id !== taskId
            || !previousStep.expectedStatuses.includes(previousRow.status))) {
            return { outcome: "conflict", reason: "previous_step" } as const;
          }
        }
        if (reuseJobId != null
          && (previousRow?.task_id !== taskId || previousRow.bot_run_id !== reuseJobId)) {
          return { outcome: "conflict", reason: "reuse_job" } as const;
        }

        const now = nowForDb(tx.dbType);
        if (previousStep != null) {
          const assignments = ["status = ?", "started_at = COALESCE(started_at, ?)"];
          const params: unknown[] = [previousStep.status, now];
          if (["succeeded", "failed", "canceled", "interrupted"].includes(previousStep.status)) {
            assignments.push("completed_at = COALESCE(completed_at, ?)");
            params.push(now);
          }
          if (previousStep.outputJson !== undefined) {
            assignments.push("output_json = ?");
            params.push(previousStep.outputJson);
          }
          if (previousStep.summary !== undefined) {
            assignments.push("summary = ?");
            params.push(previousStep.summary);
          }
          if (previousStep.errorCode !== undefined) {
            assignments.push("error_code = ?");
            params.push(previousStep.errorCode);
          }
          if (previousStep.errorMessage !== undefined) {
            assignments.push("error_message = ?");
            params.push(previousStep.errorMessage);
          }
          if (previousStep.retryable !== undefined) {
            assignments.push("retryable = ?");
            params.push(previousStep.retryable == null ? null : Number(previousStep.retryable));
          }
          assignments.push("gmt_modified = ?");
          params.push(now, previousStep.stepId, taskId, ...previousStep.expectedStatuses);
          const updated = await tx.exec(
            `UPDATE ce_steps SET ${assignments.join(", ")}
              WHERE step_id = ? AND task_id = ?
                AND status IN (${previousStep.expectedStatuses.map(() => "?").join(", ")})`,
            params,
          );
          if (updated.affectedRows !== 1) throw new TransitionMutationConflict("previous_step");
        }

        if (nextStep != null) {
          await tx.exec(
            `INSERT INTO ce_steps
             (step_id, task_id, step_type, step_no, round_no, command, status, bot_run_id,
              gmt_create, gmt_modified)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
            [nextStep.stepId, taskId, nextStep.stepType, nextStep.stepNo, nextStep.roundNo,
              nextStep.command, reuseJobId == null ? "created" : "dispatched", reuseJobId,
              now, now],
          );
        }
        const taskUpdated = await tx.exec(
          `UPDATE ce_tasks SET status = ?, config_json = ?, error_message = NULL, gmt_modified = ?
            WHERE task_id = ? AND config_json = ?
              AND status IN (${expectedTaskStatuses.map(() => "?").join(", ")})`,
          [nextTaskStatus, nextConfigJson, now, taskId, task.config_json, ...expectedTaskStatuses],
        );
        if (taskUpdated.affectedRows !== 1) throw new TransitionMutationConflict("task_state");
        return { outcome: "transitioned" } as const;
      });
    } catch (error) {
      if (error instanceof TransitionMutationConflict) {
        return { outcome: "conflict", reason: error.reason };
      }
      throw error;
    }
  }

  async compareAndSetTaskConfig(input: CompareAndSetRepairTaskConfigInput): Promise<boolean> {
    const taskId = requiredText(input.taskId, "taskId", 64);
    const expectedTaskStatuses = stateList(input.expectedTaskStatuses, "expectedTaskStatuses");
    const expectedCurrentStepId = requiredText(
      input.expectedCurrentStepId, "expectedCurrentStepId", 64,
    );
    const expectedTaskConfigDigest = sha256Digest(
      input.expectedTaskConfigDigest, "expectedTaskConfigDigest",
    );
    const nextConfigJson = serializePayload(input.nextConfig, "nextConfig");
    if (currentStepId(nextConfigJson) !== expectedCurrentStepId) {
      throw new TypeError("nextConfig.current.stepId must match expectedCurrentStepId");
    }
    return this.db.transaction(async (tx) => {
      const task = await this.lockTask(tx, taskId);
      if (!task || task.task_type !== "repair"
        || !expectedTaskStatuses.includes(task.status)
        || currentStepId(task.config_json) !== expectedCurrentStepId
        || resultDigest(task.config_json) !== expectedTaskConfigDigest) {
        return false;
      }
      const updated = await tx.exec(
        `UPDATE ce_tasks SET config_json = ?, gmt_modified = ?
          WHERE task_id = ? AND config_json = ?
            AND status IN (${expectedTaskStatuses.map(() => "?").join(", ")})`,
        [nextConfigJson, nowForDb(tx.dbType), taskId, task.config_json, ...expectedTaskStatuses],
      );
      return updated.affectedRows === 1;
    });
  }

  async createToolCall(input: CreateRepairToolCallInput): Promise<CreateRepairToolCallResult> {
    const normalized = {
      callId: requiredText(input.callId, "callId", 64),
      taskId: requiredText(input.taskId, "taskId", 64),
      stepId: requiredText(input.stepId, "stepId", 64),
      executionId: requiredText(input.executionId, "executionId", 64),
      authorizationScopeDigest: sha256Digest(input.authorizationScopeDigest, "authorizationScopeDigest"),
      clientRequestId: requiredText(input.clientRequestId, "clientRequestId", 128),
      toolName: requiredText(input.toolName, "toolName", 64),
      operation: requiredText(input.operation, "operation", 256),
      actionId: optionalText(input.actionId, "actionId", 128),
      deadlineAt: input.deadlineAt ?? null,
      isWrite: input.isWrite ?? false,
      requestJson: serializePayload(input.request, "request"),
    };
    if (normalized.deadlineAt != null) positiveInteger(normalized.deadlineAt, "deadlineAt");
    if (typeof normalized.isWrite !== "boolean") throw new TypeError("isWrite must be a boolean");
    if (input.toolCallLedgerGuard != null && typeof input.toolCallLedgerGuard !== "function") {
      throw new TypeError("toolCallLedgerGuard must be a function");
    }

    try {
      return await this.db.transaction(async (tx) => {
        const task = await this.lockTask(tx, normalized.taskId);
        const existing = await this.findRawByClientRequestId(
          normalized.stepId, normalized.clientRequestId, tx,
        );
        // Stable retries remain readable even after the workload has advanced.
        if (existing) return this.resolveCreateRetry(existing, normalized);
        if (!task || task.task_type !== "repair" || !["pending", "running"].includes(task.status)) {
          throw new RepairToolCallWorkloadConflictError(normalized.taskId, "task_state");
        }
        const workload = workloadIdentity(task.config_json);
        if (workload.stepId !== normalized.stepId || workload.executionStepId !== normalized.stepId) {
          throw new RepairToolCallWorkloadConflictError(normalized.taskId, "current_step");
        }
        if (workload.executionId !== normalized.executionId) {
          throw new RepairToolCallWorkloadConflictError(normalized.taskId, "execution");
        }
        if (workload.authorizationScopeDigest !== normalized.authorizationScopeDigest) {
          throw new RepairToolCallWorkloadConflictError(normalized.taskId, "authorization_scope");
        }
        if (input.toolCallLedgerGuard != null) {
          input.toolCallLedgerGuard(
            await this.toolCallLedger(tx, normalized.taskId, normalized.stepId),
            { runtimeTargetVersion: workload.runtimeTargetVersion },
          );
        }
        if (normalized.isWrite) {
          const active = (await tx.query<{ call_id: string }>(
            `SELECT call_id FROM ce_repair_tool_calls
              WHERE task_id = ? AND is_write = 1 AND status IN ('pending', 'executing')
              ORDER BY id ASC LIMIT 1${forUpdate(tx)}`,
            [normalized.taskId],
          ))[0];
          if (active) throw new RepairWriteSlotBusyError(normalized.taskId, active.call_id);
        }
        return this.insertToolCall(tx, normalized);
      });
    } catch (error) {
      // Preserve idempotency when racing a caller deployed before task-row locking.
      const raced = await this.findRawByClientRequestId(normalized.stepId, normalized.clientRequestId);
      if (!raced) throw error;
      return this.resolveCreateRetry(raced, normalized);
    }
  }

  async findToolCall(callId: string): Promise<RepairToolCall | null> {
    const row = await this.findRawByCallId(requiredText(callId, "callId", 64));
    return row ? toToolCall(row) : null;
  }

  async findToolCallByClientRequestId(stepId: string, clientRequestId: string): Promise<RepairToolCall | null> {
    const row = await this.findRawByClientRequestId(
      requiredText(stepId, "stepId", 64),
      requiredText(clientRequestId, "clientRequestId", 128),
    );
    return row ? toToolCall(row) : null;
  }

  async listToolCalls(taskId: string, options: ListRepairToolCallsOptions = {}): Promise<RepairToolCall[]> {
    const clauses = ["task_id = ?"];
    const params: unknown[] = [requiredText(taskId, "taskId", 64)];
    if (options.stepId != null) {
      clauses.push("step_id = ?");
      params.push(requiredText(options.stepId, "stepId", 64));
    }
    if (options.callIds != null) {
      if (options.callIds.length === 0) return [];
      if (options.callIds.length > 500) {
        throw new TypeError("callIds must contain at most 500 items");
      }
      const callIds = [...new Set(options.callIds.map((value) =>
        requiredText(value, "callId", 64)))];
      clauses.push(`call_id IN (${callIds.map(() => "?").join(", ")})`);
      params.push(...callIds);
    }
    if (options.isWrite != null) {
      clauses.push("is_write = ?");
      params.push(Number(options.isWrite));
    }
    if (options.statuses != null) {
      if (options.statuses.length === 0) return [];
      options.statuses.forEach(assertStatus);
      clauses.push(`status IN (${options.statuses.map(() => "?").join(", ")})`);
      params.push(...options.statuses);
    }
    if (options.afterId != null) {
      clauses.push("id > ?");
      params.push(positiveInteger(options.afterId, "afterId"));
    }
    if (options.clientRequestIds != null) {
      if (options.clientRequestIds.length === 0) return [];
      if (options.clientRequestIds.length > 500) {
        throw new TypeError("clientRequestIds must contain at most 500 items");
      }
      const clientRequestIds = [...new Set(options.clientRequestIds.map((value) =>
        requiredText(value, "clientRequestId", 128)))];
      clauses.push(`client_request_id IN (${clientRequestIds.map(() => "?").join(", ")})`);
      params.push(...clientRequestIds);
    }
    const recordKind = options.recordKind ?? "all";
    if (recordKind === "source") {
      clauses.push("NOT (tool_name = 'repair_control' AND operation = 'record_conclusion')");
    } else if (recordKind === "conclusion") {
      clauses.push("tool_name = 'repair_control' AND operation = 'record_conclusion'");
    } else if (recordKind !== "all") {
      throw new TypeError("recordKind must be all, source, or conclusion");
    }
    params.push(boundedLimit(options.limit));
    const rows = await this.db.query<RepairToolCallRow>(
      `SELECT * FROM ce_repair_tool_calls WHERE ${clauses.join(" AND ")} ORDER BY id ASC LIMIT ?`,
      params,
    );
    return rows.map(toToolCall);
  }

  async listPendingToolCalls(taskId: string, limit?: number): Promise<RepairToolCall[]> {
    return this.listToolCalls(taskId, { statuses: ["pending"], limit });
  }

  async listActiveToolCalls(taskId: string, limit?: number): Promise<RepairToolCall[]> {
    return this.listToolCalls(taskId, { statuses: [...ACTIVE_STATUSES], limit });
  }

  /**
   * Claims a pending call or takes over an expired executing lease in one CAS.
   * Retrying with the same active owner is idempotent.
   */
  async claimToolCall(input: ClaimRepairToolCallInput): Promise<RepairToolCall | null> {
    const callId = requiredText(input.callId, "callId", 64);
    const executionId = requiredText(input.executionId, "executionId", 64);
    const authorizationScopeDigest = sha256Digest(input.authorizationScopeDigest, "authorizationScopeDigest");
    const leaseOwner = requiredText(input.leaseOwner, "leaseOwner", 128);
    assertSecretFree(leaseOwner, "leaseOwner");
    const nowSeconds = positiveInteger(input.now ?? Math.floor(Date.now() / 1_000), "now");
    const leaseExpiresAt = positiveInteger(input.leaseExpiresAt, "leaseExpiresAt");
    if (leaseExpiresAt <= nowSeconds) throw new TypeError("leaseExpiresAt must be later than now");
    await this.db.exec(
      `UPDATE ce_repair_tool_calls
          SET status = 'executing', lease_owner = ?, lease_expires_at = ?, gmt_modified = ?
        WHERE call_id = ?
          AND execution_id = ? AND authorization_scope_digest = ?
          AND (status = 'pending'
            OR (status = 'executing' AND is_write = 0
              AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))
          AND (deadline_at IS NULL OR deadline_at > ?)`,
      [leaseOwner, leaseExpiresAt, nowForDb(this.db.dbType), callId,
        executionId, authorizationScopeDigest, nowSeconds, nowSeconds],
    );
    const row = await this.findRawByCallId(callId);
    if (!row) return null;
    if (row.execution_id === executionId
      && row.authorization_scope_digest === authorizationScopeDigest
      && row.status === "executing"
      && row.lease_owner === leaseOwner
      && row.lease_expires_at != null
      && row.lease_expires_at > nowSeconds
      && (row.deadline_at == null || row.deadline_at > nowSeconds)) {
      return toToolCall(row);
    }
    return null;
  }

  async renewLease(input: ClaimRepairToolCallInput): Promise<RepairToolCall | null> {
    const callId = requiredText(input.callId, "callId", 64);
    const executionId = requiredText(input.executionId, "executionId", 64);
    const authorizationScopeDigest = sha256Digest(input.authorizationScopeDigest, "authorizationScopeDigest");
    const leaseOwner = requiredText(input.leaseOwner, "leaseOwner", 128);
    assertSecretFree(leaseOwner, "leaseOwner");
    const nowSeconds = positiveInteger(input.now ?? Math.floor(Date.now() / 1_000), "now");
    const leaseExpiresAt = positiveInteger(input.leaseExpiresAt, "leaseExpiresAt");
    if (leaseExpiresAt <= nowSeconds) throw new TypeError("leaseExpiresAt must be later than now");
    await this.db.exec(
      `UPDATE ce_repair_tool_calls
          SET lease_expires_at = ?, gmt_modified = ?
        WHERE call_id = ? AND execution_id = ? AND authorization_scope_digest = ?
          AND status = 'executing' AND lease_owner = ?
          AND (lease_expires_at IS NULL OR lease_expires_at < ?)`,
      [leaseExpiresAt, nowForDb(this.db.dbType), callId, executionId,
        authorizationScopeDigest, leaseOwner, leaseExpiresAt],
    );
    const row = await this.findRawByCallId(callId);
    return row?.execution_id === executionId
      && row.authorization_scope_digest === authorizationScopeDigest
      && row.status === "executing" && row.lease_owner === leaseOwner ? toToolCall(row) : null;
  }

  /**
   * Finalizes a call exactly once. A byte-equivalent retry returns duplicate;
   * a different terminal status or result digest is an explicit conflict.
   */
  async completeToolCall(input: CompleteRepairToolCallInput): Promise<CompleteRepairToolCallResult | null> {
    const callId = requiredText(input.callId, "callId", 64);
    const executionId = requiredText(input.executionId, "executionId", 64);
    const authorizationScopeDigest = sha256Digest(input.authorizationScopeDigest, "authorizationScopeDigest");
    if (!TERMINAL_STATUSES.includes(input.status)) {
      throw new TypeError(`Unsupported terminal Repair tool call status: ${String(input.status)}`);
    }
    const leaseOwner = input.leaseOwner == null
      ? null
      : requiredText(input.leaseOwner, "leaseOwner", 128);
    if (leaseOwner != null) assertSecretFree(leaseOwner, "leaseOwner");
    if (input.status !== "canceled" && leaseOwner == null) {
      throw new TypeError("leaseOwner is required to complete an executing Repair tool call");
    }
    const errorCode = optionalText(input.errorCode, "errorCode", 128);
    const errorMessage = optionalText(input.errorMessage, "errorMessage", 4_000);
    const downstreamTraceId = optionalText(input.downstreamTraceId, "downstreamTraceId", 255);
    const serializedResult = serializePayload(terminalEnvelope({
      status: input.status,
      result: input.result ?? null,
      errorCode,
      errorMessage,
      downstreamTraceId,
    }), "terminalEnvelope");
    const digest = resultDigest(serializedResult);
    const ownershipClause = leaseOwner == null
      ? "status = 'pending'"
      : "status = 'executing' AND lease_owner = ?";
    // Resolve the immutable Task id first, then serialize all active/terminal
    // predicate changes with createToolCall and transitionStep on that Task row.
    const observed = await this.findRawByCallId(callId);
    if (!observed) return null;
    return this.db.transaction(async (tx) => {
      const task = await this.lockTask(tx, observed.task_id);
      if (!task || task.task_type !== "repair") return null;
      const current = await this.findRawByCallId(callId, tx);
      if (!current) return null;
      if (current.execution_id !== executionId
        || current.authorization_scope_digest !== authorizationScopeDigest) {
        throw new RepairToolCallScopeMismatchError(callId);
      }
      const params: unknown[] = [input.status, serializedResult, digest, nowForDb(tx.dbType),
        callId, executionId, authorizationScopeDigest];
      if (leaseOwner != null) params.push(leaseOwner);
      const result = await tx.exec(
        `UPDATE ce_repair_tool_calls
            SET status = ?, result_json = ?, result_digest = ?, lease_owner = NULL,
                lease_expires_at = NULL, gmt_modified = ?
          WHERE call_id = ? AND execution_id = ? AND authorization_scope_digest = ?
            AND ${ownershipClause}`,
        params,
      );
      const row = await this.findRawByCallId(callId, tx);
      if (!row) return null;
      if (result.affectedRows === 1) return { call: toToolCall(row), outcome: "completed" };
      if (TERMINAL_STATUSES.includes(row.status as RepairToolCallTerminalStatus)) {
        if (row.status === input.status && row.result_digest === digest) {
          return { call: toToolCall(row), outcome: "duplicate" };
        }
        throw new RepairToolCallCompletionConflictError(callId);
      }
      throw new RepairToolCallLeaseLostError(callId);
    });
  }

  async cancelPendingToolCall(
    callId: string,
    executionId: string,
    authorizationScopeDigest: string,
    result: unknown = null,
    now?: number,
  ): Promise<CompleteRepairToolCallResult | null> {
    return this.completeToolCall({
      callId, executionId, authorizationScopeDigest, status: "canceled", result, now,
    });
  }

  private async findRawByCallId(callId: string, db: IDatabase = this.db): Promise<RepairToolCallRow | null> {
    return (await db.query<RepairToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE call_id = ? LIMIT 1",
      [callId],
    ))[0] ?? null;
  }

  private async findRawByClientRequestId(
    stepId: string,
    clientRequestId: string,
    db: IDatabase = this.db,
  ): Promise<RepairToolCallRow | null> {
    return (await db.query<RepairToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE step_id = ? AND client_request_id = ? LIMIT 1",
      [stepId, clientRequestId],
    ))[0] ?? null;
  }

  private resolveCreateRetry(
    row: RepairToolCallRow,
    normalized: NormalizedCreateRepairToolCall,
  ): CreateRepairToolCallResult {
    if (!sameCreateRequest(row, normalized)) {
      throw new RepairToolCallIdempotencyConflictError(normalized.stepId, normalized.clientRequestId);
    }
    return { call: toToolCall(row), created: false };
  }

  private async insertToolCall(
    db: IDatabase,
    normalized: NormalizedCreateRepairToolCall,
  ): Promise<CreateRepairToolCallResult> {
    const now = nowForDb(db.dbType);
    await db.exec(
      `INSERT INTO ce_repair_tool_calls
       (call_id, task_id, step_id, execution_id, authorization_scope_digest,
        client_request_id, tool_name, operation, action_id, deadline_at,
        request_json, is_write, status, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)`,
      [normalized.callId, normalized.taskId, normalized.stepId, normalized.executionId,
        normalized.authorizationScopeDigest, normalized.clientRequestId, normalized.toolName,
        normalized.operation, normalized.actionId, normalized.deadlineAt, normalized.requestJson,
        Number(normalized.isWrite), now, now],
    );
    const created = await this.findRawByCallId(normalized.callId, db);
    if (!created) throw new Error(`Failed to create Repair tool call ${normalized.callId}`);
    return { call: toToolCall(created), created: true };
  }

  private async lockTask(db: IDatabase, taskId: string): Promise<RepairTaskStateRow | null> {
    return (await db.query<RepairTaskStateRow>(
      `SELECT task_id, task_type, status, config_json FROM ce_tasks WHERE task_id = ?${forUpdate(db)}`,
      [taskId],
    ))[0] ?? null;
  }

  private async toolCallLedger(
    db: IDatabase,
    taskId: string,
    stepId: string,
  ): Promise<RepairToolCall[]> {
    const rows = await db.query<RepairToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE task_id = ? AND step_id = ? ORDER BY id ASC",
      [taskId, stepId],
    );
    return rows.map(toToolCall);
  }
}
