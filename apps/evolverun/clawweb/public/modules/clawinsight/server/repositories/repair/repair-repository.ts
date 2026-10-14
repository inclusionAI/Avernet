import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";
import { AgentToolCallRepository } from "./agent-tool-call-repository.js";
import {
  AgentToolCallIdempotencyConflictError,
  AgentToolCallCompletionConflictError,
  AgentToolCallLeaseLostError,
  AgentToolCallScopeMismatchError,
  AgentWriteSlotBusyError,
  AgentToolCallWorkloadConflictError,
  AgentToolCallSecretPersistenceError,
  AgentToolCallDataCorruptionError,
  assertAgentAuditSecretFree,
  serializePayload,
  assertAgentAuditPersistable,
  resultDigest,
  requiredText,
  optionalText,
  optionalLongText,
  positiveInteger,
  sha256Digest,
  stateList,
  currentStepId,
  workloadIdentity,
  forUpdate,
} from "./agent-tool-call-record.js";
import type {
  AgentToolCallStatus,
  AgentToolCallTerminalStatus,
  AgentToolCall,
  AgentToolCallLedgerContext,
  AgentToolCallLedgerGuard,
  CreateAgentToolCallInput,
  CreateAgentToolCallResult,
  ClaimAgentToolCallInput,
  CompleteAgentToolCallInput,
  CompleteAgentToolCallResult,
  ListAgentToolCallsOptions,
} from "./agent-tool-call-record.js";
import {
  AgentToolCallIdempotencyConflictError as RepairToolCallIdempotencyConflictError,
  AgentToolCallCompletionConflictError as RepairToolCallCompletionConflictError,
  AgentToolCallLeaseLostError as RepairToolCallLeaseLostError,
  assertAgentAuditPersistable as assertRepairAuditPersistable,
  AgentToolCallWorkloadConflictError as RepairToolCallWorkloadConflictError,
  AgentToolCallScopeMismatchError as RepairToolCallScopeMismatchError,
  assertAgentAuditSecretFree as assertRepairAuditSecretFree,
  AgentWriteSlotBusyError as RepairWriteSlotBusyError,
  AgentToolCallDataCorruptionError as RepairToolCallDataCorruptionError,
  AgentToolCallSecretPersistenceError as RepairToolCallSecretPersistenceError,
} from "./agent-tool-call-record.js";
import type {
  CompleteAgentToolCallResult as CompleteRepairToolCallResult,
  CreateAgentToolCallResult as CreateRepairToolCallResult,
  ListAgentToolCallsOptions as ListRepairToolCallsOptions,
  CreateAgentToolCallInput as CreateRepairToolCallInput,
  AgentToolCallLedgerGuard as RepairToolCallLedgerGuard,
  AgentToolCallStatus as RepairToolCallStatus,
  AgentToolCall as RepairToolCall,
  ClaimAgentToolCallInput as ClaimRepairToolCallInput,
  AgentToolCallTerminalStatus as RepairToolCallTerminalStatus,
  AgentToolCallLedgerContext as RepairToolCallLedgerContext,
  CompleteAgentToolCallInput as CompleteRepairToolCallInput,
} from "./agent-tool-call-record.js";
export { AgentToolCallIdempotencyConflictError as RepairToolCallIdempotencyConflictError, AgentToolCallCompletionConflictError as RepairToolCallCompletionConflictError, AgentToolCallLeaseLostError as RepairToolCallLeaseLostError, assertAgentAuditPersistable as assertRepairAuditPersistable, AgentToolCallWorkloadConflictError as RepairToolCallWorkloadConflictError, AgentToolCallScopeMismatchError as RepairToolCallScopeMismatchError, assertAgentAuditSecretFree as assertRepairAuditSecretFree, AgentWriteSlotBusyError as RepairWriteSlotBusyError, AgentToolCallDataCorruptionError as RepairToolCallDataCorruptionError, AgentToolCallSecretPersistenceError as RepairToolCallSecretPersistenceError } from "./agent-tool-call-repository.js";
export type { CompleteAgentToolCallResult as CompleteRepairToolCallResult, CreateAgentToolCallResult as CreateRepairToolCallResult, ListAgentToolCallsOptions as ListRepairToolCallsOptions, CreateAgentToolCallInput as CreateRepairToolCallInput, AgentToolCallLedgerGuard as RepairToolCallLedgerGuard, AgentToolCallStatus as RepairToolCallStatus, AgentToolCall as RepairToolCall, ClaimAgentToolCallInput as ClaimRepairToolCallInput, AgentToolCallTerminalStatus as RepairToolCallTerminalStatus, AgentToolCallLedgerContext as RepairToolCallLedgerContext, CompleteAgentToolCallInput as CompleteRepairToolCallInput } from "./agent-tool-call-repository.js";

export type RepairPhase = "repair_plan" | "repair_apply";
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


/** Full Repair task and Step transitions; shared tool auditing is inherited unchanged. */
export class RepairRepository extends AgentToolCallRepository {
  constructor(db: IDatabase) { super(db, "repair"); }

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
        if (!task || task.task_type !== this.taskType || !expectedTaskStatuses.includes(task.status)) {
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
      if (!task || task.task_type !== this.taskType
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

}
