import type { IDatabase } from "../db.js";

export type EvolveTaskSourceRow = {
  id: number;
  task_id: string;
  source_type: string;
  source_id: string;
  source_schema_version: string;
  adapter_version: string | null;
  source_ref_json: string;
  source_digest: string | null;
  status: string;
  error_code: string | null;
  error_message: string | null;
  resolved_at: number | string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export class EvolveTaskSourceRepository {
  constructor(private readonly db: IDatabase) {}

  async createFrozen(input: {
    taskId: string;
    sourceType: string;
    sourceId: string;
    sourceSchemaVersion: string;
    adapterVersion: string | null;
    sourceRef: Record<string, unknown>;
  }): Promise<EvolveTaskSourceRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_task_sources
       (task_id, source_type, source_id, source_schema_version, adapter_version,
        source_ref_json, status, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, 'frozen', ?, ?)`,
      [input.taskId, input.sourceType, input.sourceId, input.sourceSchemaVersion,
        input.adapterVersion, JSON.stringify(input.sourceRef), now, now],
    );
    const row = await this.findByTaskId(input.taskId);
    if (!row) throw new Error("创建 Evolve Task Source 失败");
    return row;
  }

  async findByTaskId(taskId: string): Promise<EvolveTaskSourceRow | null> {
    return (await this.db.query<EvolveTaskSourceRow>(
      "SELECT * FROM ce_task_sources WHERE task_id = ? LIMIT 1",
      [taskId],
    ))[0] ?? null;
  }

  async markResolving(taskId: string): Promise<void> {
    await this.db.exec(
      `UPDATE ce_task_sources
          SET status = 'resolving', error_code = NULL, error_message = NULL, gmt_modified = ?
        WHERE task_id = ?`,
      [this.db.dialect.now(), taskId],
    );
  }

  async markReady(taskId: string, input: {
    digest: string;
    sourceSchemaVersion: string;
    adapterVersion: string | null;
  }): Promise<void> {
    const now = this.db.dialect.now();
    const resolvedAt = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE ce_task_sources
          SET status = 'ready', source_digest = ?, source_schema_version = ?, adapter_version = ?,
              error_code = NULL, error_message = NULL, resolved_at = ?, gmt_modified = ?
        WHERE task_id = ?`,
      [input.digest, input.sourceSchemaVersion, input.adapterVersion, resolvedAt, now, taskId],
    );
  }

  async markFailed(taskId: string, code: string, message: string): Promise<void> {
    await this.db.exec(
      `UPDATE ce_task_sources
          SET status = 'failed', error_code = ?, error_message = ?, gmt_modified = ?
        WHERE task_id = ?`,
      [code, message.slice(0, 4000), this.db.dialect.now(), taskId],
    );
  }
}
