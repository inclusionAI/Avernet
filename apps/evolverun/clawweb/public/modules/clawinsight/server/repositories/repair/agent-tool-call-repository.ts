import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";
import {
  ACTIVE_STATUSES,
  TERMINAL_STATUSES,
  AgentToolCallIdempotencyConflictError,
  AgentToolCallCompletionConflictError,
  AgentToolCallLeaseLostError,
  AgentToolCallScopeMismatchError,
  AgentWriteSlotBusyError,
  AgentToolCallWorkloadConflictError,
  AgentToolCallSecretPersistenceError,
  AgentToolCallDataCorruptionError,
  assertSecretFree,
  assertAgentAuditSecretFree,
  serializePayload,
  assertAgentAuditPersistable,
  resultDigest,
  requiredText,
  optionalText,
  positiveInteger,
  sha256Digest,
  boundedLimit,
  assertStatus,
  workloadIdentity,
  forUpdate,
  terminalEnvelope,
  toToolCall,
  sameCreateRequest,
} from "./agent-tool-call-record.js";
import type {
  AgentToolCallStatus,
  AgentToolCallTerminalStatus,
  AgentToolCallRow,
  AgentToolCall,
  AgentToolCallLedgerContext,
  AgentToolCallLedgerGuard,
  CreateAgentToolCallInput,
  CreateAgentToolCallResult,
  ClaimAgentToolCallInput,
  CompleteAgentToolCallInput,
  CompleteAgentToolCallResult,
  ListAgentToolCallsOptions,
  NormalizedCreateAgentToolCall,
  AgentTaskStateRow,
} from "./agent-tool-call-record.js";
export { AgentToolCallIdempotencyConflictError, AgentToolCallCompletionConflictError, AgentToolCallLeaseLostError, AgentToolCallScopeMismatchError, AgentWriteSlotBusyError, AgentToolCallWorkloadConflictError, AgentToolCallSecretPersistenceError, AgentToolCallDataCorruptionError, assertAgentAuditSecretFree, assertAgentAuditPersistable } from "./agent-tool-call-record.js";
export type { AgentToolCallStatus, AgentToolCallTerminalStatus, AgentToolCall, AgentToolCallLedgerContext, AgentToolCallLedgerGuard, CreateAgentToolCallInput, CreateAgentToolCallResult, ClaimAgentToolCallInput, CompleteAgentToolCallInput, CompleteAgentToolCallResult, ListAgentToolCallsOptions } from "./agent-tool-call-record.js";

/** Scoped idempotency, leased execution and immutable audit receipts for both repair flows. */
export class AgentToolCallRepository {
  constructor(protected readonly db: IDatabase, protected readonly taskType: "repair" | "session_recovery") {}

  async createToolCall(input: CreateAgentToolCallInput): Promise<CreateAgentToolCallResult> {
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
        if (!task || task.task_type !== this.taskType || !["pending", "running"].includes(task.status)) {
          throw new AgentToolCallWorkloadConflictError(normalized.taskId, "task_state");
        }
        const workload = workloadIdentity(task.config_json);
        if (workload.stepId !== normalized.stepId || workload.executionStepId !== normalized.stepId) {
          throw new AgentToolCallWorkloadConflictError(normalized.taskId, "current_step");
        }
        if (workload.executionId !== normalized.executionId) {
          throw new AgentToolCallWorkloadConflictError(normalized.taskId, "execution");
        }
        if (workload.authorizationScopeDigest !== normalized.authorizationScopeDigest) {
          throw new AgentToolCallWorkloadConflictError(normalized.taskId, "authorization_scope");
        }
        if (input.toolCallLedgerGuard != null) {
          input.toolCallLedgerGuard(
            await this.toolCallLedger(tx, normalized.taskId, normalized.stepId),
            { runtimeTargetVersion: workload.runtimeTargetVersion, taskConfig: JSON.parse(task.config_json), taskStatus: task.status },
          );
        }
        if (normalized.isWrite) {
          const active = (await tx.query<{ call_id: string }>(
            `SELECT call_id FROM ce_repair_tool_calls
              WHERE task_id = ? AND is_write = 1 AND status IN ('pending', 'executing')
              ORDER BY id ASC LIMIT 1${forUpdate(tx)}`,
            [normalized.taskId],
          ))[0];
          if (active) throw new AgentWriteSlotBusyError(normalized.taskId, active.call_id);
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

  async findToolCall(callId: string): Promise<AgentToolCall | null> {
    const row = await this.findRawByCallId(requiredText(callId, "callId", 64));
    return row ? toToolCall(row) : null;
  }

  async findToolCallByClientRequestId(stepId: string, clientRequestId: string): Promise<AgentToolCall | null> {
    const row = await this.findRawByClientRequestId(
      requiredText(stepId, "stepId", 64),
      requiredText(clientRequestId, "clientRequestId", 128),
    );
    return row ? toToolCall(row) : null;
  }

  async listToolCalls(taskId: string, options: ListAgentToolCallsOptions = {}): Promise<AgentToolCall[]> {
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
    const rows = await this.db.query<AgentToolCallRow>(
      `SELECT * FROM ce_repair_tool_calls WHERE ${clauses.join(" AND ")} ORDER BY id ASC LIMIT ?`,
      params,
    );
    return rows.map(toToolCall);
  }

  async listPendingToolCalls(taskId: string, limit?: number): Promise<AgentToolCall[]> {
    return this.listToolCalls(taskId, { statuses: ["pending"], limit });
  }

  async listActiveToolCalls(taskId: string, limit?: number): Promise<AgentToolCall[]> {
    return this.listToolCalls(taskId, { statuses: [...ACTIVE_STATUSES], limit });
  }

  /**
   * Claims a pending call or takes over an expired executing lease in one CAS.
   * Retrying with the same active owner is idempotent.
   */
  async claimToolCall(input: ClaimAgentToolCallInput): Promise<AgentToolCall | null> {
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

  async renewLease(input: ClaimAgentToolCallInput): Promise<AgentToolCall | null> {
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
  async completeToolCall(input: CompleteAgentToolCallInput): Promise<CompleteAgentToolCallResult | null> {
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
      if (!task || task.task_type !== this.taskType) return null;
      const current = await this.findRawByCallId(callId, tx);
      if (!current) return null;
      if (current.execution_id !== executionId
        || current.authorization_scope_digest !== authorizationScopeDigest) {
        throw new AgentToolCallScopeMismatchError(callId);
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
      if (TERMINAL_STATUSES.includes(row.status as AgentToolCallTerminalStatus)) {
        if (row.status === input.status && row.result_digest === digest) {
          return { call: toToolCall(row), outcome: "duplicate" };
        }
        throw new AgentToolCallCompletionConflictError(callId);
      }
      throw new AgentToolCallLeaseLostError(callId);
    });
  }

  async cancelPendingToolCall(
    callId: string,
    executionId: string,
    authorizationScopeDigest: string,
    result: unknown = null,
    now?: number,
  ): Promise<CompleteAgentToolCallResult | null> {
    return this.completeToolCall({
      callId, executionId, authorizationScopeDigest, status: "canceled", result, now,
    });
  }

  private async findRawByCallId(callId: string, db: IDatabase = this.db): Promise<AgentToolCallRow | null> {
    return (await db.query<AgentToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE call_id = ? LIMIT 1",
      [callId],
    ))[0] ?? null;
  }

  private async findRawByClientRequestId(
    stepId: string,
    clientRequestId: string,
    db: IDatabase = this.db,
  ): Promise<AgentToolCallRow | null> {
    return (await db.query<AgentToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE step_id = ? AND client_request_id = ? LIMIT 1",
      [stepId, clientRequestId],
    ))[0] ?? null;
  }

  private resolveCreateRetry(
    row: AgentToolCallRow,
    normalized: NormalizedCreateAgentToolCall,
  ): CreateAgentToolCallResult {
    if (!sameCreateRequest(row, normalized)) {
      throw new AgentToolCallIdempotencyConflictError(normalized.stepId, normalized.clientRequestId);
    }
    return { call: toToolCall(row), created: false };
  }

  private async insertToolCall(
    db: IDatabase,
    normalized: NormalizedCreateAgentToolCall,
  ): Promise<CreateAgentToolCallResult> {
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

  protected async lockTask(db: IDatabase, taskId: string): Promise<AgentTaskStateRow | null> {
    return (await db.query<AgentTaskStateRow>(
      `SELECT task_id, task_type, status, config_json FROM ce_tasks WHERE task_id = ?${forUpdate(db)}`,
      [taskId],
    ))[0] ?? null;
  }

  protected async toolCallLedger(
    db: IDatabase,
    taskId: string,
    stepId: string,
  ): Promise<AgentToolCall[]> {
    const rows = await db.query<AgentToolCallRow>(
      "SELECT * FROM ce_repair_tool_calls WHERE task_id = ? AND step_id = ? ORDER BY id ASC",
      [taskId, stepId],
    );
    return rows.map(toToolCall);
  }
}
