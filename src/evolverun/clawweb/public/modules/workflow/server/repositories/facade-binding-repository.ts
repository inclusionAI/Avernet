/**
 * Repository for facade_bindings table — slash command to workflow bindings.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type FacadeBindingRow = {
  id: number;
  command: string;
  workflow_id: string;
  pack_id: string | null;
  remark: string | null;
  gmt_create: number;
  gmt_modified: number;
};

export type FacadeBindingInsert = {
  command: string;
  workflowId: string;
  packId?: string | null;
  remark?: string | null;
};

export class FacadeBindingRepository {
  constructor(private db: IDatabase) {}

  async listAll(): Promise<FacadeBindingRow[]> {
    return this.db.query<FacadeBindingRow>(
      "SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified FROM facade_bindings ORDER BY command",
    );
  }

  async findByCommand(command: string): Promise<FacadeBindingRow | null> {
    const rows = await this.db.query<FacadeBindingRow>(
      "SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified FROM facade_bindings WHERE command = ?",
      [command],
    );
    return rows[0] ?? null;
  }

  async findByWorkflowId(workflowId: string): Promise<FacadeBindingRow[]> {
    // A workflow may accumulate multiple rows when its facade command changes, because
    // `upsert` keys on `command` (the old command's row is not removed). Order by the
    // most-recently-modified row first so callers that take [0] get the current binding.
    return this.db.query<FacadeBindingRow>(
      "SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified FROM facade_bindings WHERE workflow_id = ? ORDER BY gmt_modified DESC, id DESC",
      [workflowId],
    );
  }

  async upsert(input: FacadeBindingInsert): Promise<FacadeBindingRow> {
    const now = this.db.dialect.now();
    const command = input.command;
    const workflowId = input.workflowId;
    const packId = input.packId ?? null;
    const remark = input.remark ?? null;

    const existing = await this.findByCommand(command);

    if (existing) {
      await this.db.exec(
        "UPDATE facade_bindings SET workflow_id = ?, pack_id = ?, remark = ?, gmt_modified = ? WHERE command = ?",
        [workflowId, packId, remark, now, command],
      );
      return { ...existing, workflow_id: workflowId, pack_id: packId, remark, gmt_modified: now as number };
    }

    await this.db.exec(
      "INSERT INTO facade_bindings (command, workflow_id, pack_id, remark, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?)",
      [command, workflowId, packId, remark, now, now],
    );
    const result = await this.findByCommand(command);
    return result!;
  }

  async deleteByCommand(command: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM facade_bindings WHERE command = ?",
      [command],
    );
    return result.affectedRows > 0;
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM facade_bindings WHERE workflow_id = ?",
      [workflowId],
    );
    return result.affectedRows;
  }

  async findPage(opts: { page: number; pageSize: number; search?: string }): Promise<{ rows: FacadeBindingRow[]; total: number }> {
    const offset = (opts.page - 1) * opts.pageSize;
    const limit = opts.pageSize;

    let whereClause = '';
    const params: unknown[] = [];
    if (opts.search) {
      whereClause = 'WHERE command LIKE ? OR workflow_id LIKE ? OR remark LIKE ?';
      const pattern = `%${opts.search}%`;
      params.push(pattern, pattern, pattern);
    }

    const countRows = await this.db.query<{ cnt: number }>(
      `SELECT COUNT(*) as cnt FROM facade_bindings ${whereClause}`,
      params,
    );
    const total = countRows[0]?.cnt ?? 0;

    const rows = await this.db.query<FacadeBindingRow>(
      `SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified FROM facade_bindings ${whereClause} ORDER BY gmt_modified DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );

    return { rows, total };
  }

  /** Cascade update workflow_id when a workflow is renamed */
  async updateWorkflowId(oldWorkflowId: string, newWorkflowId: string): Promise<void> {
    await this.db.exec(
      "UPDATE facade_bindings SET workflow_id = ? WHERE workflow_id = ?",
      [newWorkflowId, oldWorkflowId],
    );
  }
}