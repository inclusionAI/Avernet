import type { IDatabase } from "../types.js";
import type { IFacadeBindingRepository } from "./types.js";

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
  workflow_id: string;
  pack_id?: string;
  remark?: string;
};

export class FacadeBindingRepository implements IFacadeBindingRepository {
  constructor(private db: IDatabase) {}

  async findByCommand(command: string): Promise<FacadeBindingRow | null> {
    const rows = await this.db.query<FacadeBindingRow>(
      `SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified
       FROM facade_bindings WHERE command = ?`,
      [command],
    );
    return rows[0] ?? null;
  }

  async findByWorkflowId(workflowId: string): Promise<FacadeBindingRow[]> {
    return this.db.query<FacadeBindingRow>(
      `SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified
       FROM facade_bindings WHERE workflow_id = ?`,
      [workflowId],
    );
  }

  async listAll(): Promise<FacadeBindingRow[]> {
    return this.db.query<FacadeBindingRow>(
      `SELECT id, command, workflow_id, pack_id, remark, gmt_create, gmt_modified
       FROM facade_bindings ORDER BY command`,
    );
  }

  async upsert(insert: FacadeBindingInsert): Promise<FacadeBindingRow> {
    const existing = await this.findByCommand(insert.command);
    if (existing) {
      if (existing.workflow_id !== insert.workflow_id) {
        throw new Error(
          `facade command "${insert.command}" already bound to workflow "${existing.workflow_id}"`,
        );
      }
      await this.db.exec(
        `UPDATE facade_bindings SET remark = ?, pack_id = ? WHERE command = ?`,
        [insert.remark ?? null, insert.pack_id ?? null, insert.command],
      );
      return (await this.findByCommand(insert.command))!;
    }
    await this.db.exec(
      `INSERT INTO facade_bindings (command, workflow_id, pack_id, remark) VALUES (?, ?, ?, ?)`,
      [insert.command, insert.workflow_id, insert.pack_id ?? null, insert.remark ?? null],
    );
    return (await this.findByCommand(insert.command))!;
  }

  async deleteByCommand(command: string): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM facade_bindings WHERE command = ?`,
      [command],
    );
    return result.affectedRows > 0;
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    const result = await this.db.exec(
      `DELETE FROM facade_bindings WHERE workflow_id = ?`,
      [workflowId],
    );
    return result.affectedRows;
  }
}