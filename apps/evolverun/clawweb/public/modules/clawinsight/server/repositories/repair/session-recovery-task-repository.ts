import { createHash, randomUUID } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { RecoveryStoredTask, RecoveryTaskStore } from "../../services/repair/session-recovery/task-contracts.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";

export type { RecoveryStoredTask } from "../../services/repair/session-recovery/task-contracts.js";

/** Business data lives in the existing Task extension JSON, alongside append-only task events. */
export class SessionRecoveryTaskRepository implements RecoveryTaskStore {
  constructor(private readonly db: IDatabase, private readonly tasks: EvolveRepository) {}

  async create(value: RecoveryStoredTask, stepId: string): Promise<void> {
    const config = value;
    await this.tasks.createTaskWithStep({
      task: { taskId: config.taskId, taskType: "session_recovery", userId: config.request.target.entity_id,
        botId: config.request.target.bot_id, taskName: "会话自愈", remark: this.eventRemark(config.request.event_id), createdBy: "session-recovery-service", configJson: JSON.stringify(value) },
      step: { stepId, stepType: "session_recovery", stepNo: 1, command: "session_recovery" },
    });
  }

  async listForMaintenance(limit: number, afterTaskId = ""): Promise<RecoveryStoredTask[]> {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) throw new Error("Invalid maintenance limit");
    const rows = await this.db.query<{ config_json: string }>(
      "SELECT config_json FROM ce_tasks WHERE task_type = 'session_recovery' AND task_id > ? AND (status IN ('running','waiting_owner','waiting_effect') OR config_json NOT LIKE ?) ORDER BY task_id ASC LIMIT ?",
      [afterTaskId, '%"pending_results":[]%', limit]);
    return rows.map(row => JSON.parse(row.config_json) as RecoveryStoredTask);
  }

  async find(taskId: string): Promise<RecoveryStoredTask | null> {
    const row = await this.tasks.findTask(taskId);
    if (!row || row.task_type !== "session_recovery") return null;
    return JSON.parse(row.config_json) as RecoveryStoredTask;
  }

  private eventRemark(eventId: string): string {
    return `session-recovery-event:${createHash("sha256").update(eventId).digest("hex")}`;
  }
  async listForEvent(eventId: string, ownerId: string): Promise<RecoveryStoredTask[]> {
    const rows = await this.db.query<{ config_json: string }>(
      "SELECT config_json FROM ce_tasks WHERE task_type = 'session_recovery' AND remark = ? AND user_id = ? ORDER BY id DESC LIMIT 100",
      [this.eventRemark(eventId), ownerId]);
    return rows.map(row => JSON.parse(row.config_json) as RecoveryStoredTask);
  }
  async update(taskId: string, expectedVersion: number, change: (current: RecoveryStoredTask) => RecoveryStoredTask): Promise<RecoveryStoredTask | null> {
    return this.db.transaction(async tx => {
      const rows = await tx.query<{ config_json: string }>(
        `SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'session_recovery'${tx.dbType === "sqlite" ? "" : " FOR UPDATE"}`,
        [taskId],
      );
      if (!rows.length) return null;
      const current = JSON.parse(rows[0].config_json) as RecoveryStoredTask;
      if (current.version !== expectedVersion) return null;
      const updated = change(current);
      if (updated.taskId !== taskId || updated.version !== expectedVersion + 1) throw new Error("Invalid recovery task transition");
      if (containsSecret(JSON.stringify(updated))) throw new Error("Recovery task state must not contain credentials");
      if (updated.timeline.length < current.timeline.length
        || current.timeline.some((event, index) => JSON.stringify(event) !== JSON.stringify(updated.timeline[index]))) {
        throw new Error("Recovery timeline facts are immutable");
      }
      const serialized = JSON.stringify(updated);
      const outcome = await tx.exec("UPDATE ce_tasks SET config_json = ?, status = ?, gmt_modified = ? WHERE task_id = ? AND config_json = ?",
        [serialized, updated.status, nowForDb(tx.dbType), taskId, rows[0].config_json]);
      return outcome.affectedRows === 1 ? updated : null;
    });
  }
  async acknowledgeResult(taskId: string, resultVersion: number): Promise<void> {
    await this.db.transaction(async tx => {
      const row = (await tx.query<{ config_json: string }>(
        `SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'session_recovery'${tx.dbType === "sqlite" ? "" : " FOR UPDATE"}`, [taskId],
      ))[0];
      if (!row) throw new Error("Recovery task no longer exists");
      const value = JSON.parse(row.config_json) as RecoveryStoredTask;
      if (!value.pending_results.some(result => result.result_version === resultVersion)) return;
      value.timeline.push({ event_id: randomUUID(), at_ms: Date.now(), kind: "result", actor: "service",
        summary: "诊断服务已接收任务结果", payload: { result_version: resultVersion } });
      value.pending_results = value.pending_results.filter(result => result.result_version !== resultVersion);
      const result = await tx.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ? AND config_json = ?",
        [JSON.stringify(value), taskId, row.config_json]);
      if (result.affectedRows !== 1) throw new Error("Recovery callback acknowledgment conflicted");
    });
  }

}
