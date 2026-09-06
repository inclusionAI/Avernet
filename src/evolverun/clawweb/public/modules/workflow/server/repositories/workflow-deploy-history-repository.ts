/**
 * Repository for workflow_deploy_history table — centralized deploy history.
 * MySQL-only table (migration v45, mysqlOnly: true).
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type WorkflowDeployHistoryRow = {
  id: number;
  pack_id: string;
  workflow_id: string;
  deploy_number: number;
  version: number;
  tag_name: string | null;
  action: string;
  from_deploy_number: number | null;
  spec_json: string;
  note: string | null;
  bot_id: string | null;
  owner_id: string | null;
  /** 1 = default active version for this workflow; 0 = inactive. */
  is_active: number;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type InsertDeployHistoryInput = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName?: string | null;
  action: "deploy" | "rollback" | "pull" | "migration" | "edit";
  fromDeployNumber?: number;
  specJson: string;
  note?: string;
  botId?: string;
  ownerId?: string;
  /** If true, mark this record as the active default version on insert. Default false. */
  isActive?: boolean;
};

export class WorkflowDeployHistoryRepository {
  constructor(private db: IDatabase) {}

  async insert(input: InsertDeployHistoryInput): Promise<void> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO workflow_deploy_history (pack_id, workflow_id, deploy_number, version, tag_name, action, from_deploy_number, spec_json, note, bot_id, owner_id, is_active, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.packId, input.workflowId, input.deployNumber, input.version,
        input.tagName, input.action, input.fromDeployNumber ?? null,
        input.specJson, input.note ?? null, input.botId ?? null, input.ownerId ?? null,
        input.isActive ? 1 : 0, now, now,
      ],
    );
  }

  async listHistory(workflowId: string, limit: number): Promise<Omit<WorkflowDeployHistoryRow, "spec_json">[]> {
    return this.db.query<Omit<WorkflowDeployHistoryRow, "spec_json">>(
      `SELECT id, pack_id, workflow_id, deploy_number, version, tag_name, action, from_deploy_number, note, bot_id, owner_id, is_active, gmt_create, gmt_modified
       FROM workflow_deploy_history WHERE workflow_id = ? ORDER BY deploy_number DESC LIMIT ?`,
      [workflowId, limit],
    );
  }

  async getLatestVersion(workflowId: string): Promise<number> {
    const rows = await this.db.query<{ max_version: number | null }>(
      `SELECT MAX(version) as max_version FROM workflow_deploy_history WHERE workflow_id = ?`,
      [workflowId],
    );
    return rows[0]?.max_version ?? 0;
  }

  /** Get MAX(deploy_number) for a specific pack+workflow. Returns 0 if no records. */
  async getMaxDeployNumber(packId: string, workflowId: string): Promise<number> {
    const rows = await this.db.query<{ max_deploy_number: number | null }>(
      `SELECT MAX(deploy_number) as max_deploy_number FROM workflow_deploy_history WHERE pack_id = ? AND workflow_id = ?`,
      [packId, workflowId],
    );
    return rows[0]?.max_deploy_number ?? 0;
  }

  async findByVersion(workflowId: string, version: number): Promise<Pick<WorkflowDeployHistoryRow, "deploy_number" | "tag_name" | "action" | "spec_json" | "note" | "gmt_create"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "deploy_number" | "tag_name" | "action" | "spec_json" | "note" | "gmt_create">>(
      `SELECT deploy_number, tag_name, action, spec_json, note, gmt_create
       FROM workflow_deploy_history WHERE workflow_id = ? AND version = ?
       ORDER BY deploy_number DESC LIMIT 1`,
      [workflowId, version],
    );
    return rows[0] ?? null;
  }

  async getLatestDeploy(packId: string, workflowId: string): Promise<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "spec_json"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "spec_json">>(
      `SELECT deploy_number, version, tag_name, spec_json FROM workflow_deploy_history WHERE pack_id = ? AND workflow_id = ?
       ORDER BY version DESC LIMIT 1`,
      [packId, workflowId],
    );
    return rows[0] ?? null;
  }

  /** Find record by deploy_number + workflow_id (unique per UK). */
  async findByDeployNumber(packId: string, workflowId: string, deployNumber: number): Promise<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create">>(
      `SELECT deploy_number, version, tag_name, action, spec_json, note, from_deploy_number, gmt_create
       FROM workflow_deploy_history WHERE pack_id = ? AND workflow_id = ? AND deploy_number = ?`,
      [packId, workflowId, deployNumber],
    );
    return rows[0] ?? null;
  }

  /** Find a record by workflow_id + deploy_number only. deploy_number is unique within a single
   *  workflow (it is MAX(deploy_number)+1 per workflow), so pack_id is not needed for lookup. */
  async findByWorkflowAndDeployNumber(workflowId: string, deployNumber: number): Promise<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create">>(
      `SELECT deploy_number, version, tag_name, action, spec_json, note, from_deploy_number, gmt_create
       FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?`,
      [workflowId, deployNumber],
    );
    return rows[0] ?? null;
  }

  /** Find by version, filtered to deploy/edit actions only (for rollback content lookup). */
  async findByVersionDeployOrEdit(workflowId: string, version: number): Promise<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "note" | "from_deploy_number" | "gmt_create">>(
      `SELECT deploy_number, version, tag_name, action, spec_json, note, from_deploy_number, gmt_create
       FROM workflow_deploy_history WHERE workflow_id = ? AND version = ? AND action IN ('deploy', 'edit')
       ORDER BY deploy_number DESC LIMIT 1`,
      [workflowId, version],
    );
    return rows[0] ?? null;
  }

  /** Find the active (is_active=1) deploy record for a workflow. */
  async findActiveByWorkflowId(workflowId: string): Promise<Pick<WorkflowDeployHistoryRow, "pack_id" | "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "bot_id" | "owner_id" | "gmt_create" | "gmt_modified"> | null> {
    const rows = await this.db.query<Pick<WorkflowDeployHistoryRow, "pack_id" | "deploy_number" | "version" | "tag_name" | "action" | "spec_json" | "bot_id" | "owner_id" | "gmt_create" | "gmt_modified">>(
      `SELECT pack_id, deploy_number, version, tag_name, action, spec_json, bot_id, owner_id, gmt_create, gmt_modified
       FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1
       ORDER BY deploy_number DESC LIMIT 1`,
      [workflowId],
    );
    return rows[0] ?? null;
  }

  /** Set a specific version as the active (default) version for a workflow.
   *  Uses row-level locking on MySQL/ZDAS to prevent concurrent activation races. */
  async setActive(workflowId: string, version: number): Promise<boolean> {
    return this.db.transaction(async (tx) => {
      // Row-level lock for MySQL/ZDAS; SQLite serializes writes automatically.
      const lockClause = tx.dbType === "mysql" || tx.dbType === "zdas" ? "FOR UPDATE" : "";

      // Verify target version exists (and lock matching rows)
      const targetRows = await tx.query<{ count: number }>(
        `SELECT COUNT(*) as count FROM workflow_deploy_history WHERE workflow_id = ? AND version = ? ${lockClause}`,
        [workflowId, version],
      );
      if (!targetRows[0] || targetRows[0].count === 0) {
        return false;
      }

      // Clear existing active flag, then set target (latest deploy of the version)
      await tx.exec(
        `UPDATE workflow_deploy_history SET is_active = 0 WHERE workflow_id = ? AND is_active = 1`,
        [workflowId],
      );
      await tx.exec(
        `UPDATE workflow_deploy_history SET is_active = 1 WHERE workflow_id = ? AND version = ? ORDER BY deploy_number DESC LIMIT 1`,
        [workflowId, version],
      );
      return true;
    });
  }
}
