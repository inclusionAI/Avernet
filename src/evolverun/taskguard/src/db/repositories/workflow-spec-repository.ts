/**
 * Repository for workflow_specs table — read-only access for ClawFlow.
 * ClawWeb handles writes; ClawFlow only reads for DB-first workflow resolution.
 */
import type { IDatabase } from "../types.js";
import type { IWorkflowSpecRepository, WorkflowSpecRow } from "./types.js";

export type { WorkflowSpecRow };

export class WorkflowSpecRepository implements IWorkflowSpecRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<WorkflowSpecRow | null> {
    const rows = await this.db.query<WorkflowSpecRow>(
      `SELECT id, workflow_id, pack_id, spec_json, gmt_create, gmt_modified
       FROM workflow_specs WHERE workflow_id = ?`,
      [workflowId],
    );
    return rows[0] ?? null;
  }
}