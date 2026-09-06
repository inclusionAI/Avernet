import { describe, expect, it } from "vitest";
import type { ExecResult, IDatabase } from "@avernet/clawweb-shared/server/db";
import { mysqlDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { RepairRepository } from "../repair-repository.js";

const SCOPE_DIGEST = "a".repeat(64);

class ZdasRecordingDatabase implements IDatabase {
  readonly dbType = "zdas" as const;
  readonly dialect = mysqlDialect;
  readonly queries: string[] = [];
  readonly task = {
    task_id: "task-zdas-lock",
    task_type: "repair",
    status: "pending",
    config_json: JSON.stringify({
      current: { stepId: "step-zdas-lock" },
      execution: { stepId: "step-zdas-lock", executionId: "exec-zdas-lock" },
      authorizationScopeDigest: SCOPE_DIGEST,
    }),
  };
  readonly step = {
    step_id: "step-zdas-lock",
    task_id: "task-zdas-lock",
    status: "created",
    bot_run_id: null,
  };
  call: Record<string, unknown> | null = null;

  async query<T>(sql: string, params: unknown[] = []): Promise<T[]> {
    this.queries.push(sql);
    if (sql.includes("FROM ce_tasks")) return [this.task] as T[];
    if (sql.includes("FROM ce_steps")) return [this.step] as T[];
    if (sql.includes("SELECT id FROM ce_repair_tool_calls")) return [];
    if (sql.includes("WHERE step_id = ? AND client_request_id = ?")) {
      return this.call
        && this.call.step_id === params[0]
        && this.call.client_request_id === params[1]
        ? [this.call] as T[]
        : [];
    }
    if (sql.includes("WHERE call_id = ?")) {
      return this.call?.call_id === params[0] ? [this.call] as T[] : [];
    }
    if (sql.includes("WHERE task_id = ? AND step_id = ?")) {
      return this.call?.task_id === params[0] && this.call.step_id === params[1]
        ? [this.call] as T[]
        : [];
    }
    return [];
  }

  async exec(sql: string, params: unknown[] = []): Promise<ExecResult> {
    if (sql.includes("INSERT INTO ce_repair_tool_calls")) {
      this.call = {
        id: 1,
        call_id: params[0],
        task_id: params[1],
        step_id: params[2],
        execution_id: params[3],
        authorization_scope_digest: params[4],
        client_request_id: params[5],
        tool_name: params[6],
        operation: params[7],
        action_id: params[8],
        deadline_at: params[9],
        request_json: params[10],
        is_write: params[11],
        status: "pending",
        lease_owner: null,
        lease_expires_at: null,
        result_json: null,
        result_digest: null,
        gmt_create: params[12],
        gmt_modified: params[13],
      };
      return { affectedRows: 1, insertId: 1 };
    }
    if (sql.includes("UPDATE ce_repair_tool_calls")) {
      if (!this.call || this.call.status !== "pending") return { affectedRows: 0 };
      this.call.status = params[0];
      this.call.result_json = params[1];
      this.call.result_digest = params[2];
      this.call.lease_owner = null;
      this.call.lease_expires_at = null;
      this.call.gmt_modified = params[3];
      return { affectedRows: 1 };
    }
    if (sql.includes("UPDATE ce_steps")) {
      this.step.status = String(params[0]);
      return { affectedRows: 1 };
    }
    if (sql.includes("UPDATE ce_tasks")) {
      this.task.status = String(params[0]);
      this.task.config_json = String(params[1]);
      return { affectedRows: 1 };
    }
    return { affectedRows: 0 };
  }

  async transaction<T>(fn: (db: IDatabase) => Promise<T>): Promise<T> {
    return fn(this);
  }

  async close(): Promise<void> {}
}

describe("RepairRepository task mutex SQL", () => {
  it("uses FOR UPDATE for ZDAS create, completion, and transition predicates", async () => {
    const db = new ZdasRecordingDatabase();
    const repo = new RepairRepository(db);
    const created = await repo.createToolCall({
      callId: "call-zdas-lock",
      taskId: db.task.task_id,
      stepId: db.step.step_id,
      executionId: "exec-zdas-lock",
      authorizationScopeDigest: SCOPE_DIGEST,
      clientRequestId: "request-zdas-lock",
      toolName: "baas_read",
      operation: "fs_list",
      request: { path: "/home/admin" },
    });
    await repo.cancelPendingToolCall(
      created.call.callId,
      "exec-zdas-lock",
      SCOPE_DIGEST,
      { reason: "test" },
    );
    await expect(repo.transitionStep({
      taskId: db.task.task_id,
      expectedTaskStatuses: ["pending"],
      expectedCurrentStepId: db.step.step_id,
      previousStep: {
        stepId: db.step.step_id,
        expectedStatuses: ["created"],
        status: "succeeded",
      },
      nextTaskStatus: "waiting_approval",
      nextConfig: JSON.parse(db.task.config_json),
    })).resolves.toEqual({ outcome: "transitioned" });

    const taskLocks = db.queries.filter((sql) => sql.includes("FROM ce_tasks"));
    expect(taskLocks).toHaveLength(3);
    expect(taskLocks.every((sql) => sql.trimEnd().endsWith("FOR UPDATE"))).toBe(true);
    expect(db.queries.find((sql) => sql.includes("SELECT id FROM ce_repair_tool_calls")))
      .toMatch(/FOR UPDATE$/u);
    expect(db.queries.find((sql) => sql.includes("FROM ce_steps")))
      .toMatch(/FOR UPDATE$/u);
  });
});
