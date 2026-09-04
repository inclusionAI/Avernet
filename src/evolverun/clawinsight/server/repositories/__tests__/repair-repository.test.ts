import { createHash } from "node:crypto";
import Database from "better-sqlite3";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { runMigrations, SqliteDatabase } from "../../db.js";
import { mysqlDialect } from "../../db/dialect.js";
import { migrations } from "../../schema.js";
import { redactPersistableText } from "../../services/repair/redaction.js";
import {
  RepairRepository,
  RepairToolCallCompletionConflictError,
  RepairToolCallIdempotencyConflictError,
  RepairToolCallLeaseLostError,
  RepairToolCallSecretPersistenceError,
  RepairToolCallWorkloadConflictError,
  RepairWriteSlotBusyError,
  type CreateRepairToolCallInput,
  type RepairToolCallTerminalStatus,
} from "../repair-repository.js";

const SCOPE_DIGEST = "a".repeat(64);

function digest(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

let db: SqliteDatabase;
let repo: RepairRepository;

beforeAll(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new RepairRepository(db);
});

afterAll(async () => {
  await db.close();
});

function config(taskId: string, stepId: string, executionId = `exec-${taskId}`) {
  return {
    schemaVersion: "ce-repair/v1",
    taskId,
    authorizationScopeDigest: SCOPE_DIGEST,
    runtimeTarget: { version: 7 },
    current: { stepId, phase: "repair_plan" },
    execution: { stepId, executionId, ticketDigest: "b".repeat(64) },
  };
}

async function createTask(taskId: string, stepId = `step-${taskId}`): Promise<void> {
  await repo.createTaskWithStep({
    task: {
      taskId,
      userId: "user-1",
      botId: "bot-1",
      taskName: `Repair ${taskId}`,
      remark: "gateway cannot start",
      config: config(taskId, stepId),
      createdBy: "user-1",
    },
    step: {
      stepId,
      stepType: "repair_plan",
      stepNo: 1,
      command: "repair_plan",
    },
  });
}

function toolInput(
  taskId: string,
  stepId: string,
  clientRequestId: string,
  overrides: Partial<CreateRepairToolCallInput> = {},
): CreateRepairToolCallInput {
  return {
    callId: `call-${taskId}-${clientRequestId}`,
    taskId,
    stepId,
    executionId: `exec-${taskId}`,
    authorizationScopeDigest: SCOPE_DIGEST,
    clientRequestId,
    toolName: "ocb_context",
    operation: "get_bot",
    request: { botId: "bot-1" },
    ...overrides,
  };
}

function toolRequestEnvelope(runtimeTargetVersion: number, payload: unknown) {
  return {
    schemaVersion: "repair-tool-request/v1",
    runtimeTargetVersion,
    payload,
  };
}

describe("Repair schema", () => {
  it("creates v87 ce_repair_tool_calls without raw credential columns", async () => {
    const columns = await db.query<{ name: string }>("PRAGMA table_info(ce_repair_tool_calls)");
    const names = columns.map((column) => column.name);
    expect(names).toEqual([
      "id", "call_id", "task_id", "step_id", "execution_id",
      "authorization_scope_digest", "client_request_id", "tool_name", "operation",
      "action_id", "deadline_at", "request_json", "is_write", "status", "lease_owner",
      "lease_expires_at", "result_json", "result_digest", "gmt_create", "gmt_modified",
    ]);
    const indexes = await db.query<{ name: string; origin: string }>(
      "PRAGMA index_list(ce_repair_tool_calls)",
    );
    expect(indexes).toHaveLength(2);
    expect(indexes.every((index) => index.origin === "u")).toBe(true);
    const table = (await db.query<{ sql: string }>(
      "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ce_repair_tool_calls'",
    ))[0];
    expect(table.sql).not.toContain("CHECK");
    const migration = await db.query<{ version: number }>(
      "SELECT version FROM schema_version WHERE version = 87",
    );
    expect(migration).toHaveLength(1);
  });

  it("adapts the minimal v87 table to MySQL without a v88 index migration", () => {
    const tableMigration = migrations.find((migration) => migration.version === 87);
    const indexMigration = migrations.find((migration) => migration.version === 88);
    expect(tableMigration).toBeDefined();
    expect(tableMigration!.sql).toHaveLength(1);
    expect(indexMigration).toBeUndefined();
    const ddl = mysqlDialect.renderDdl(tableMigration!.sql[0]);
    expect(ddl).toContain("id BIGINT PRIMARY KEY AUTO_INCREMENT");
    expect(ddl).toContain("lease_expires_at BIGINT");
    expect(ddl).toContain("gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP");
    expect(ddl).toContain("gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP");
    expect(ddl).not.toContain("unixepoch()");
    expect(ddl).not.toContain("CHECK");
    expect(ddl.match(/UNIQUE \(/gu)).toHaveLength(2);
  });
});

describe("Repair CE task/step transactions", () => {
  it("creates the Repair task and first Step atomically and rejects persisted secrets", async () => {
    await createTask("task-atomic", "step-atomic");
    const tasks = await db.query<{ task_type: string; status: string }>(
      "SELECT task_type, status FROM ce_tasks WHERE task_id = ?",
      ["task-atomic"],
    );
    const steps = await db.query<{ step_type: string; status: string }>(
      "SELECT step_type, status FROM ce_steps WHERE step_id = ?",
      ["step-atomic"],
    );
    expect(tasks[0]).toEqual({ task_type: "repair", status: "pending" });
    expect(steps[0]).toEqual({ step_type: "repair_plan", status: "created" });

    await expect(repo.createTaskWithStep({
      task: {
        taskId: "task-secret",
        userId: "user-1",
        botId: "bot-1",
        taskName: "secret",
        config: { current: { stepId: "step-secret" }, modelApiKey: "sk-secret-value-123" },
        createdBy: "user-1",
      },
      step: {
        stepId: "step-secret", stepType: "repair_plan", stepNo: 1, command: "repair_plan",
      },
    })).rejects.toBeInstanceOf(RepairToolCallSecretPersistenceError);
    expect(await db.query("SELECT id FROM ce_tasks WHERE task_id = 'task-secret'")).toHaveLength(0);

    await expect(repo.createTaskWithStep({
      task: {
        taskId: "task-llm-secret",
        userId: "user-1",
        botId: "bot-1",
        taskName: "secret",
        config: {
          current: { stepId: "step-llm-secret" },
          openclawUsesCustomApiKey: true,
          llmApiKey: "one-execution-secret",
        },
        createdBy: "user-1",
      },
      step: {
        stepId: "step-llm-secret", stepType: "repair_plan", stepNo: 1, command: "repair_plan",
      },
    })).rejects.toBeInstanceOf(RepairToolCallSecretPersistenceError);
    expect(await db.query("SELECT id FROM ce_tasks WHERE task_id = 'task-llm-secret'")).toHaveLength(0);
  });

  it("rolls back the Task if Step insertion fails", async () => {
    await expect(repo.createTaskWithStep({
      task: {
        taskId: "task-rollback",
        userId: "user-1",
        botId: "bot-1",
        taskName: "rollback",
        config: config("task-rollback", "step-atomic"),
        createdBy: "user-1",
      },
      step: {
        stepId: "step-atomic", stepType: "repair_plan", stepNo: 1, command: "repair_plan",
      },
    })).rejects.toThrow();
    expect(await db.query("SELECT id FROM ce_tasks WHERE task_id = 'task-rollback'")).toHaveLength(0);
  });

  it("atomically transitions Step+Task and can bind the next Step to the same job", async () => {
    const taskId = "task-transition";
    const planStepId = "step-transition-plan";
    const applyStepId = "step-transition-apply";
    await createTask(taskId, planStepId);
    await db.exec(
      "UPDATE ce_steps SET status = 'dispatched', bot_run_id = 'job-shared' WHERE step_id = ?",
      [planStepId],
    );

    const waitingConfig = config(taskId, planStepId);
    const first = await repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: planStepId,
      previousStep: {
        stepId: planStepId,
        expectedStatuses: ["dispatched"],
        status: "succeeded",
        output: { artifactDigest: digest("plan") },
        summary: "plan ready",
      },
      nextTaskStatus: "waiting_approval",
      nextConfig: waitingConfig,
    });
    expect(first).toEqual({ outcome: "transitioned" });

    const applyConfig = {
      ...waitingConfig,
      current: { stepId: applyStepId, phase: "repair_apply" },
    };
    const second = await repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["waiting_approval"],
      expectedCurrentStepId: planStepId,
      nextTaskStatus: "running",
      nextConfig: applyConfig,
      nextStep: {
        stepId: applyStepId,
        stepType: "repair_apply",
        stepNo: 2,
        command: "repair_apply",
      },
      reuseJobId: "job-shared",
    });
    expect(second).toEqual({ outcome: "transitioned" });
    const next = (await db.query<{ status: string; bot_run_id: string }>(
      "SELECT status, bot_run_id FROM ce_steps WHERE step_id = ?",
      [applyStepId],
    ))[0];
    expect(next).toEqual({ status: "dispatched", bot_run_id: "job-shared" });
    const task = (await db.query<{ status: string; config_json: string }>(
      "SELECT status, config_json FROM ce_tasks WHERE task_id = ?",
      [taskId],
    ))[0];
    expect(task.status).toBe("running");
    expect(JSON.parse(task.config_json).current.stepId).toBe(applyStepId);
  });

  it("does not transition while any tool call is active", async () => {
    const taskId = "task-active-transition";
    const stepId = "step-active-transition";
    await createTask(taskId, stepId);
    const reportCall = await repo.createToolCall(toolInput(taskId, stepId, "active"));
    const result = await repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      nextTaskStatus: "waiting_approval",
      nextConfig: config(taskId, stepId),
    });
    expect(result).toEqual({ outcome: "conflict", reason: "active_tool_calls" });
    const task = (await db.query<{ status: string }>(
      "SELECT status FROM ce_tasks WHERE task_id = ?", [taskId],
    ))[0];
    expect(task.status).toBe("pending");

    const ignored = await repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      nextTaskStatus: "waiting_approval",
      nextConfig: config(taskId, stepId),
      ignoreActiveToolCallId: reportCall.call.callId,
    });
    expect(ignored).toEqual({ outcome: "transitioned" });

    const secondTaskId = "task-active-transition-two";
    const secondStepId = "step-active-transition-two";
    await createTask(secondTaskId, secondStepId);
    const firstActive = await repo.createToolCall(toolInput(secondTaskId, secondStepId, "first"));
    await repo.createToolCall(toolInput(secondTaskId, secondStepId, "second"));
    const stillBlocked = await repo.transitionStep({
      taskId: secondTaskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: secondStepId,
      nextTaskStatus: "waiting_approval",
      nextConfig: config(secondTaskId, secondStepId),
      ignoreActiveToolCallId: firstActive.call.callId,
    });
    expect(stillBlocked).toEqual({ outcome: "conflict", reason: "active_tool_calls" });
  });

  it("uses the observed config digest to prevent a heartbeat from overwriting a concurrent decision", async () => {
    const taskId = "task-config-cas";
    const stepId = "step-config-cas";
    await createTask(taskId, stepId);
    const initial = (await db.query<{ config_json: string }>(
      "SELECT config_json FROM ce_tasks WHERE task_id = ?", [taskId],
    ))[0].config_json;
    const next = { ...JSON.parse(initial), marker: "decision" };
    expect(await repo.compareAndSetTaskConfig({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      expectedTaskConfigDigest: digest(initial),
      nextConfig: next,
    })).toBe(true);
    expect(await repo.compareAndSetTaskConfig({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      expectedTaskConfigDigest: digest(initial),
      nextConfig: { ...JSON.parse(initial), marker: "stale-heartbeat" },
    })).toBe(false);
    const stored = (await db.query<{ config_json: string }>(
      "SELECT config_json FROM ce_tasks WHERE task_id = ?", [taskId],
    ))[0].config_json;
    expect(JSON.parse(stored).marker).toBe("decision");
    expect(await repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      expectedTaskConfigDigest: digest(initial),
      nextTaskStatus: "running",
      nextConfig: next,
    })).toEqual({ outcome: "conflict", reason: "task_config" });
  });
});

describe("Repair tool-call ledger", () => {
  it("runs create and transition ledger guards before mutation", async () => {
    const taskId = "task-ledger-guard";
    const stepId = "step-ledger-guard";
    await createTask(taskId, stepId);
    const rejected = new Error("semantic conclusion required");
    await expect(repo.createToolCall(toolInput(taskId, stepId, "guarded-create", {
      toolCallLedgerGuard: (calls, context) => {
        expect(calls).toEqual([]);
        expect(context).toEqual({ runtimeTargetVersion: 7 });
        throw rejected;
      },
    }))).rejects.toBe(rejected);
    await expect(repo.listToolCalls(taskId)).resolves.toHaveLength(0);

    const source = await repo.createToolCall(toolInput(taskId, stepId, "source"));
    await expect(repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      nextTaskStatus: "waiting_approval",
      nextConfig: config(taskId, stepId),
      ignoreActiveToolCallId: source.call.callId,
      toolCallLedgerGuard: (calls, context) => {
        expect(calls.map((call) => call.callId)).toEqual([source.call.callId]);
        expect(context).toEqual({ runtimeTargetVersion: 7 });
        throw rejected;
      },
    })).rejects.toBe(rejected);
    await expect(db.query<{ status: string }>(
      "SELECT status FROM ce_tasks WHERE task_id = ?", [taskId],
    )).resolves.toEqual([{ status: "pending" }]);
  });

  it("rejects a stale new workload call but preserves an existing idempotent retry", async () => {
    const taskId = "task-stale-tool-call";
    const stepId = "step-stale-tool-call";
    await createTask(taskId, stepId);
    const input = toolInput(taskId, stepId, "stable-request");
    const created = await repo.createToolCall(input);
    await expect(repo.transitionStep({
      taskId,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: stepId,
      nextTaskStatus: "waiting_approval",
      nextConfig: config(taskId, stepId),
      ignoreActiveToolCallId: created.call.callId,
    })).resolves.toEqual({ outcome: "transitioned" });

    await expect(repo.createToolCall({ ...input, callId: "ignored-retry-after-transition" }))
      .resolves.toMatchObject({ created: false, call: { callId: created.call.callId } });
    await expect(repo.createToolCall(toolInput(taskId, stepId, "new-after-transition")))
      .rejects.toBeInstanceOf(RepairToolCallWorkloadConflictError);
  });

  it("is idempotent by Step + stable request, ignores deadline refresh, and rejects conflicts", async () => {
    const taskId = "task-idempotent";
    const stepId = "step-idempotent";
    await createTask(taskId, stepId);
    const input = toolInput(taskId, stepId, "request-1", {
      actionId: "action-1",
      deadlineAt: 2_000,
    });
    const created = await repo.createToolCall(input);
    expect(created.created).toBe(true);
    expect(created.call).toMatchObject({
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
      actionId: "action-1",
      deadlineAt: 2_000,
      isWrite: false,
      status: "pending",
    });
    const duplicate = await repo.createToolCall({
      ...input,
      callId: "ignored-retry-call-id",
      deadlineAt: 3_000,
    });
    expect(duplicate.created).toBe(false);
    expect(duplicate.call.callId).toBe(created.call.callId);
    expect(duplicate.call.deadlineAt).toBe(2_000);

    await expect(repo.createToolCall({
      ...input,
      callId: "conflicting-call-id",
      request: { botId: "different-bot" },
    })).rejects.toBeInstanceOf(RepairToolCallIdempotencyConflictError);
  });

  it("ignores runtime target version changes only for the v1 request envelope", async () => {
    const taskId = "task-request-envelope";
    const stepId = "step-request-envelope";
    await createTask(taskId, stepId);
    const payload = { operation: "get_bot", params: { botId: "bot-1" } };
    const input = toolInput(taskId, stepId, "request-envelope", {
      request: toolRequestEnvelope(1, payload),
    });
    const created = await repo.createToolCall(input);
    const duplicate = await repo.createToolCall({
      ...input,
      callId: "ignored-envelope-retry-call-id",
      request: toolRequestEnvelope(2, payload),
    });
    expect(duplicate).toMatchObject({
      created: false,
      call: {
        callId: created.call.callId,
        request: toolRequestEnvelope(1, payload),
      },
    });

    await expect(repo.createToolCall({
      ...input,
      callId: "conflicting-envelope-retry-call-id",
      request: toolRequestEnvelope(2, {
        operation: "get_bot",
        params: { botId: "different-bot" },
      }),
    })).rejects.toBeInstanceOf(RepairToolCallIdempotencyConflictError);
  });

  it("accepts a v1 envelope retry for a legacy row only when its payload is unchanged", async () => {
    const taskId = "task-request-envelope-legacy";
    const stepId = "step-request-envelope-legacy";
    await createTask(taskId, stepId);
    const legacyPayload = { operation: "get_bot", params: { botId: "bot-1" } };
    const input = toolInput(taskId, stepId, "request-envelope-legacy", {
      request: legacyPayload,
    });
    const created = await repo.createToolCall(input);
    const duplicate = await repo.createToolCall({
      ...input,
      callId: "ignored-legacy-envelope-retry-call-id",
      request: toolRequestEnvelope(2, legacyPayload),
    });
    expect(duplicate).toMatchObject({
      created: false,
      call: {
        callId: created.call.callId,
        request: legacyPayload,
      },
    });
  });

  it("queries source and semantic-conclusion records independently and by exact idempotency keys", async () => {
    const taskId = "task-audit-record-kinds";
    const stepId = "step-audit-record-kinds";
    await createTask(taskId, stepId);
    const source = await repo.createToolCall(toolInput(taskId, stepId, "source", {
      toolName: "baas_read",
      operation: "fs_read",
    }));
    const conclusion = await repo.createToolCall(toolInput(taskId, stepId, `conclusion:${source.call.callId}`, {
      callId: "call-audit-conclusion",
      toolName: "repair_control",
      operation: "record_conclusion",
    }));
    await repo.createToolCall(toolInput(taskId, stepId, "unrelated-source", {
      toolName: "antlogs",
      operation: "search",
    }));

    await expect(repo.listToolCalls(taskId, { recordKind: "source" }))
      .resolves.toHaveLength(2);
    await expect(repo.listToolCalls(taskId, { recordKind: "conclusion" }))
      .resolves.toEqual([conclusion.call]);
    await expect(repo.listToolCalls(taskId, {
      recordKind: "conclusion",
      clientRequestIds: [`conclusion:${source.call.callId}`],
    })).resolves.toEqual([conclusion.call]);
    await expect(repo.listToolCalls(taskId, {
      recordKind: "conclusion",
      clientRequestIds: ["conclusion:missing"],
    })).resolves.toEqual([]);
  });

  it("filters Apply evidence calls by exact call ids and write intent", async () => {
    const taskId = "task-apply-evidence-filter";
    const stepId = "step-apply-evidence-filter";
    await createTask(taskId, stepId);
    const read = await repo.createToolCall(toolInput(taskId, stepId, "read-call", {
      callId: "call-read-evidence",
      toolName: "baas_read",
    }));
    const write = await repo.createToolCall(toolInput(taskId, stepId, "write-call", {
      callId: "call-write-evidence",
      toolName: "baas_write",
      actionId: "restart-gateway",
      isWrite: true,
    }));

    await expect(repo.listToolCalls(taskId, {
      stepId,
      callIds: [read.call.callId, write.call.callId],
      isWrite: true,
      limit: 2,
    })).resolves.toEqual([write.call]);
    await expect(repo.listToolCalls(taskId, {
      stepId,
      callIds: ["missing-call"],
      isWrite: true,
    })).resolves.toEqual([]);
  });

  it.each([
    { headers: { Cookie: "SSO=secret" } },
    { modelApiKey: "sk-secret-value-123" },
    { llmApiKey: "one-execution-secret" },
    { authCode: "one-time-code" },
    { authorizationCode: "one-time-code" },
    { cfuseAuthCode: "one-time-code" },
    { execution_ticket: "opaque-secret" },
    { authorization: "Bearer opaque-secret" },
    { path: "/ready?X-Amz-Signature=signed-query-secret" },
  ])("rejects authentication material instead of persisting it: %j", async (request) => {
    const suffix = digest(JSON.stringify(request)).slice(0, 8);
    const taskId = `task-secret-${suffix}`;
    const stepId = `step-secret-${suffix}`;
    await createTask(taskId, stepId);
    await expect(repo.createToolCall(toolInput(taskId, stepId, suffix, { request })))
      .rejects.toBeInstanceOf(RepairToolCallSecretPersistenceError);
    expect(await repo.listToolCalls(taskId)).toHaveLength(0);
  });

  it("rejects an AuthCode in a terminal result and leaves the claimed call uncompleted", async () => {
    const taskId = "task-result-auth-code";
    const stepId = "step-result-auth-code";
    await createTask(taskId, stepId);
    const created = await repo.createToolCall(toolInput(taskId, stepId, "result-auth-code"));
    const scope = {
      callId: created.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });
    await expect(repo.completeToolCall({
      ...scope,
      leaseOwner: "executor",
      status: "succeeded",
      result: { authCode: "must-never-persist" },
      now: 110,
    })).rejects.toBeInstanceOf(RepairToolCallSecretPersistenceError);
    expect(await repo.findToolCall(created.call.callId)).toMatchObject({
      status: "executing",
      result: null,
    });
  });

  it("persists a batch after only its credential-bearing entry was safely redacted", async () => {
    const taskId = "task-result-entry-redaction";
    const stepId = "step-result-entry-redaction";
    await createTask(taskId, stepId);
    const created = await repo.createToolCall(toolInput(taskId, stepId, "result-entry-redaction"));
    const scope = {
      callId: created.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });

    const completed = await repo.completeToolCall({
      ...scope,
      leaseOwner: "executor",
      status: "succeeded",
      result: {
        entries: [
          { message: "safe-before" },
          { message: "[REDACTED_SECRET_TEXT]" },
          { message: "safe-after" },
        ],
      },
      now: 110,
    });

    expect(completed?.call).toMatchObject({
      status: "succeeded",
      result: {
        entries: [
          { message: "safe-before" },
          { message: "[REDACTED_SECRET_TEXT]" },
          { message: "safe-after" },
        ],
      },
    });
  });

  it("persists a canonical redacted error without retaining its authentication label", async () => {
    const taskId = "task-redacted-terminal-error";
    const stepId = "step-redacted-terminal-error";
    await createTask(taskId, stepId);
    const created = await repo.createToolCall(toolInput(taskId, stepId, "redacted-terminal-error"));
    const scope = {
      callId: created.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });
    const completed = await repo.completeToolCall({
      ...scope,
      leaseOwner: "executor",
      status: "failed",
      errorCode: "repair_ocb_failed",
      errorMessage: redactPersistableText("upstream Cookie: SSO=terminal-secret"),
      now: 110,
    });
    expect(completed?.call).toMatchObject({
      status: "failed",
      // The redactor replaces only the secret assignment (Cookie: SSO=...) with
      // [REDACTED_SECRET_TEXT], preserving the surrounding diagnostic prefix
      // ("upstream ") rather than discarding the entire entry.
      errorMessage: "upstream [REDACTED_SECRET_TEXT]",
    });
  });

  it("claims by scoped CAS, renews, takes over only after expiry, and finalizes idempotently", async () => {
    const taskId = "task-lease";
    const stepId = "step-lease";
    await createTask(taskId, stepId);
    const created = await repo.createToolCall(toolInput(taskId, stepId, "lease", { deadlineAt: 1_000 }));
    const base = {
      callId: created.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    expect(await repo.claimToolCall({
      ...base, authorizationScopeDigest: "c".repeat(64), leaseOwner: "browser-a",
      now: 100, leaseExpiresAt: 200,
    })).toBeNull();
    const first = await repo.claimToolCall({ ...base, leaseOwner: "browser-a", now: 100, leaseExpiresAt: 200 });
    expect(first).toMatchObject({ status: "executing", leaseOwner: "browser-a" });
    const retry = await repo.claimToolCall({ ...base, leaseOwner: "browser-a", now: 110, leaseExpiresAt: 200 });
    expect(retry).toMatchObject({ status: "executing", leaseOwner: "browser-a" });
    expect(await repo.claimToolCall({
      ...base, leaseOwner: "browser-b", now: 150, leaseExpiresAt: 220,
    })).toBeNull();
    const renewed = await repo.renewLease({
      ...base, leaseOwner: "browser-a", now: 150, leaseExpiresAt: 250,
    });
    expect(renewed?.leaseExpiresAt).toBe(250);
    expect(await repo.claimToolCall({
      ...base, leaseOwner: "browser-b", now: 201, leaseExpiresAt: 300,
    })).toBeNull();
    const taken = await repo.claimToolCall({
      ...base, leaseOwner: "browser-b", now: 251, leaseExpiresAt: 350,
    });
    expect(taken).toMatchObject({ leaseOwner: "browser-b" });

    await expect(repo.completeToolCall({
      ...base, leaseOwner: "browser-a", status: "succeeded", result: { ok: true }, now: 260,
    })).rejects.toBeInstanceOf(RepairToolCallLeaseLostError);
    const completed = await repo.completeToolCall({
      ...base, leaseOwner: "browser-b", status: "succeeded", result: { ok: true }, now: 260,
    });
    expect(completed).toMatchObject({ outcome: "completed", call: { status: "succeeded" } });
    const duplicate = await repo.completeToolCall({
      ...base, leaseOwner: "browser-b", status: "succeeded", result: { ok: true }, now: 261,
    });
    expect(duplicate?.outcome).toBe("duplicate");
    await expect(repo.completeToolCall({
      ...base, leaseOwner: "browser-b", status: "failed", result: { ok: true }, now: 262,
    })).rejects.toBeInstanceOf(RepairToolCallCompletionConflictError);
    expect(await repo.listActiveToolCalls(taskId)).toHaveLength(0);
  });

  it("stores one terminal envelope and hashes result, error, trace, and status together", async () => {
    const taskId = "task-terminal-envelope";
    const stepId = "step-terminal-envelope";
    await createTask(taskId, stepId);
    const created = await repo.createToolCall(toolInput(taskId, stepId, "terminal-envelope"));
    const scope = {
      callId: created.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });
    const completed = await repo.completeToolCall({
      ...scope,
      leaseOwner: "executor",
      status: "failed",
      result: { artifactRef: "oss://repair/evidence.json" },
      errorCode: "repair_ocb_failed",
      errorMessage: "downstream failed",
      downstreamTraceId: "trace-1",
      now: 110,
    });
    expect(completed).toMatchObject({
      outcome: "completed",
      call: {
        result: { artifactRef: "oss://repair/evidence.json" },
        errorCode: "repair_ocb_failed",
        errorMessage: "downstream failed",
        downstreamTraceId: "trace-1",
      },
    });
    const stored = (await db.query<{ result_json: string; result_digest: string }>(
      "SELECT result_json, result_digest FROM ce_repair_tool_calls WHERE call_id = ?",
      [created.call.callId],
    ))[0];
    expect(JSON.parse(stored.result_json)).toEqual({
      downstreamTraceId: "trace-1",
      error: { code: "repair_ocb_failed", message: "downstream failed" },
      result: { artifactRef: "oss://repair/evidence.json" },
      status: "failed",
    });
    expect(stored.result_digest).toBe(digest(stored.result_json));
    await expect(repo.completeToolCall({
      ...scope,
      leaseOwner: "executor",
      status: "failed",
      result: { artifactRef: "oss://repair/evidence.json" },
      errorCode: "different_error",
      errorMessage: "downstream failed",
      downstreamTraceId: "trace-1",
      now: 111,
    })).rejects.toBeInstanceOf(RepairToolCallCompletionConflictError);
  });

  it.each<RepairToolCallTerminalStatus>(["succeeded", "failed", "unknown", "canceled"])(
    "records %s as an immutable terminal status",
    async (status) => {
      const taskId = `task-terminal-${status}`;
      const stepId = `step-terminal-${status}`;
      await createTask(taskId, stepId);
      const created = await repo.createToolCall(toolInput(taskId, stepId, status));
      const scope = {
        callId: created.call.callId,
        executionId: `exec-${taskId}`,
        authorizationScopeDigest: SCOPE_DIGEST,
      };
      await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });
      const result = await repo.completeToolCall({
        ...scope, leaseOwner: "executor", status, result: { status }, now: 110,
      });
      expect(result).toMatchObject({ outcome: "completed", call: { status } });
    },
  );

  it("cancels a pending call idempotently and refuses a claim after its deadline", async () => {
    const taskId = "task-cancel-deadline";
    const stepId = "step-cancel-deadline";
    await createTask(taskId, stepId);
    const canceled = await repo.createToolCall(toolInput(taskId, stepId, "cancel"));
    const first = await repo.cancelPendingToolCall(
      canceled.call.callId, `exec-${taskId}`, SCOPE_DIGEST, { reason: "task ended" }, 100,
    );
    expect(first?.outcome).toBe("completed");
    const second = await repo.cancelPendingToolCall(
      canceled.call.callId, `exec-${taskId}`, SCOPE_DIGEST, { reason: "task ended" }, 101,
    );
    expect(second?.outcome).toBe("duplicate");

    const expired = await repo.createToolCall(toolInput(taskId, stepId, "expired", { deadlineAt: 150 }));
    expect(await repo.claimToolCall({
      callId: expired.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
      leaseOwner: "late-browser",
      now: 150,
      leaseExpiresAt: 200,
    })).toBeNull();
  });

  it("enforces one active write per Task while allowing reads", async () => {
    const taskId = "task-write-slot";
    const stepId = "step-write-slot";
    await createTask(taskId, stepId);
    const firstInput = toolInput(taskId, stepId, "write-1", { isWrite: true });
    const first = await repo.createToolCall(firstInput);
    expect(first.call.isWrite).toBe(true);
    await expect(repo.createToolCall(toolInput(taskId, stepId, "write-2", { isWrite: true })))
      .rejects.toBeInstanceOf(RepairWriteSlotBusyError);
    await expect(repo.createToolCall(toolInput(taskId, stepId, "read-1"))).resolves.toMatchObject({
      created: true,
    });

    const scope = {
      callId: first.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await repo.claimToolCall({ ...scope, leaseOwner: "executor", now: 100, leaseExpiresAt: 200 });
    await repo.completeToolCall({
      ...scope, leaseOwner: "executor", status: "succeeded", result: { ok: true }, now: 110,
    });
    await expect(repo.createToolCall(toolInput(taskId, stepId, "write-2", { isWrite: true })))
      .resolves.toMatchObject({ created: true, call: { isWrite: true } });
    await expect(repo.createToolCall({ ...firstInput, callId: "write-1-retry" }))
      .resolves.toMatchObject({ created: false, call: { callId: first.call.callId } });
  });

  it("never takes over an expired executing write automatically", async () => {
    const taskId = "task-write-no-replay";
    const stepId = "step-write-no-replay";
    await createTask(taskId, stepId);
    const write = await repo.createToolCall(toolInput(taskId, stepId, "write", { isWrite: true }));
    const scope = {
      callId: write.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
    };
    await expect(repo.claimToolCall({
      ...scope, leaseOwner: "browser-a", now: 100, leaseExpiresAt: 200,
    })).resolves.toMatchObject({ status: "executing", leaseOwner: "browser-a" });
    await expect(repo.claimToolCall({
      ...scope, leaseOwner: "browser-b", now: 201, leaseExpiresAt: 300,
    })).resolves.toBeNull();
    await expect(repo.completeToolCall({
      ...scope,
      leaseOwner: "browser-a",
      status: "unknown",
      result: { reason: "execution_lease_expired" },
      now: 201,
    })).resolves.toMatchObject({ call: { status: "unknown" } });
  });

  it("lists pending and active calls in stable Task order", async () => {
    const taskId = "task-list";
    const stepId = "step-list";
    await createTask(taskId, stepId);
    const first = await repo.createToolCall(toolInput(taskId, stepId, "one"));
    await repo.createToolCall(toolInput(taskId, stepId, "two"));
    await repo.claimToolCall({
      callId: first.call.callId,
      executionId: `exec-${taskId}`,
      authorizationScopeDigest: SCOPE_DIGEST,
      leaseOwner: "executor",
      now: 100,
      leaseExpiresAt: 200,
    });
    expect((await repo.listPendingToolCalls(taskId)).map((call) => call.clientRequestId)).toEqual(["two"]);
    expect((await repo.listActiveToolCalls(taskId)).map((call) => call.clientRequestId)).toEqual(["one", "two"]);
  });
});
