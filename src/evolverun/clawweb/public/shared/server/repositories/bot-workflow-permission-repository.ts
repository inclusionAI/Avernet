/**
 * Repository for bot_workflow_permissions table — bot-to-workflow access control.
 * Read queries do NOT filter by env (workflows are shared across environments).
 * Write operations still record env for future extensibility.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { getCurrentEnv } from "@avernet/clawweb-shared/server/env";

export type BotWorkflowPermissionRow = {
  id: number;
  bot_id: string | null;
  bot_owner_id: string;
  workflow_id: string;
  env: string;
  can_view: number;
  can_execute: number;
  can_edit: number;
  gmt_create: number;
  gmt_modified: number;
};

export type BotWorkflowPermissionUpsert = {
  bot_id: string | null;
  bot_owner_id: string;
  workflow_id: string;
  can_view: number;
  can_execute: number;
  can_edit: number;
};

const SELECT_COLUMNS = "id, bot_id, bot_owner_id, workflow_id, env, can_view, can_execute, can_edit, gmt_create, gmt_modified" as const;

export class BotWorkflowPermissionRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<BotWorkflowPermissionRow[]> {
    return this.db.query<BotWorkflowPermissionRow>(
      `SELECT ${SELECT_COLUMNS} FROM bot_workflow_permissions
       WHERE workflow_id = ? AND id IN (
         SELECT MAX(id) FROM bot_workflow_permissions
         WHERE workflow_id = ?
         GROUP BY COALESCE(bot_id, ''), bot_owner_id
       )`,
      [workflowId, workflowId],
    );
  }

  async upsert(data: BotWorkflowPermissionUpsert): Promise<BotWorkflowPermissionRow> {
    const env = getCurrentEnv();
    const now = this.db.dialect.now();

    // Normalize: empty string → null for owner-level permissions
    const normalizedBotId = (data.bot_id === "" || data.bot_id === undefined) ? null : data.bot_id;

    // Use IS NULL for null bot_id comparison (= NULL never matches in SQL)
    const botIdCondition = normalizedBotId === null ? "bot_id IS NULL" : "bot_id = ?";
    const existing = await this.db.query<BotWorkflowPermissionRow>(
      `SELECT ${SELECT_COLUMNS} FROM bot_workflow_permissions WHERE ${botIdCondition} AND bot_owner_id = ? AND workflow_id = ? AND env = ?`,
      normalizedBotId === null ? [data.bot_owner_id, data.workflow_id, env] : [normalizedBotId, data.bot_owner_id, data.workflow_id, env],
    );

    if (existing.length > 0) {
      await this.db.exec(
        `UPDATE bot_workflow_permissions SET can_view = ?, can_execute = ?, can_edit = ?, gmt_modified = ? WHERE ${botIdCondition} AND bot_owner_id = ? AND workflow_id = ? AND env = ?`,
        normalizedBotId === null
          ? [data.can_view, data.can_execute, data.can_edit, now, data.bot_owner_id, data.workflow_id, env]
          : [data.can_view, data.can_execute, data.can_edit, now, normalizedBotId, data.bot_owner_id, data.workflow_id, env],
      );
      return { ...existing[0], can_view: data.can_view, can_execute: data.can_execute, can_edit: data.can_edit, gmt_modified: now as number };
    }

    await this.db.exec(
      "INSERT INTO bot_workflow_permissions (bot_id, bot_owner_id, workflow_id, env, can_view, can_execute, can_edit, gmt_create, gmt_modified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      [normalizedBotId, data.bot_owner_id, data.workflow_id, env, data.can_view, data.can_execute, data.can_edit, now, now],
    );

    const rows = await this.db.query<BotWorkflowPermissionRow>(
      `SELECT ${SELECT_COLUMNS} FROM bot_workflow_permissions WHERE ${botIdCondition} AND bot_owner_id = ? AND workflow_id = ? AND env = ?`,
      normalizedBotId === null ? [data.bot_owner_id, data.workflow_id, env] : [normalizedBotId, data.bot_owner_id, data.workflow_id, env],
    );
    return rows[0];
  }

  async delete(botId: string | null, botOwnerId: string, workflowId: string): Promise<boolean> {
    const env = getCurrentEnv();
    const normalizedBotId = (botId === "" || botId === undefined) ? null : botId;
    const botIdCondition = normalizedBotId === null ? "bot_id IS NULL" : "bot_id = ?";
    const result = await this.db.exec(
      `DELETE FROM bot_workflow_permissions WHERE ${botIdCondition} AND bot_owner_id = ? AND workflow_id = ? AND env = ?`,
      normalizedBotId === null ? [botOwnerId, workflowId, env] : [normalizedBotId, botOwnerId, workflowId, env],
    );
    return result.affectedRows > 0;
  }

  async deleteById(id: number, workflowId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM bot_workflow_permissions WHERE id = ? AND workflow_id = ?",
      [id, workflowId],
    );
    return result.affectedRows > 0;
  }

  async checkPermission(
    botId: string,
    botOwnerId: string,
    workflowId: string,
    permission: "view" | "execute" | "edit",
  ): Promise<boolean> {
    const fieldMap = { view: "can_view", execute: "can_execute", edit: "can_edit" } as const;
    const field = fieldMap[permission];

    // Step 1: Check global wildcard permission.
    // bot_owner_id='*' (with bot_id IS NULL) grants the permission to everyone.
    const globalRows = await this.db.query<Pick<BotWorkflowPermissionRow, typeof field>>(
      `SELECT ${field} FROM bot_workflow_permissions WHERE (bot_id IS NULL OR bot_id = '') AND bot_owner_id = '*' AND workflow_id = ? AND ${field} = 1 LIMIT 1`,
      [workflowId],
    );
    if (globalRows.length > 0) return true;

    // Step 2: Check owner-level permission (bot_id IS NULL OR empty string)
    // Owner-level permission grants access to all bots under that owner
    const ownerRows = await this.db.query<Pick<BotWorkflowPermissionRow, typeof field>>(
      `SELECT ${field} FROM bot_workflow_permissions WHERE (bot_id IS NULL OR bot_id = '') AND bot_owner_id = ? AND workflow_id = ? AND ${field} = 1 LIMIT 1`,
      [botOwnerId, workflowId],
    );
    if (ownerRows.length > 0) return true;

    // Step 3: Check bot-level permission (bot_id = specific bot)
    const botRows = await this.db.query<Pick<BotWorkflowPermissionRow, typeof field>>(
      `SELECT ${field} FROM bot_workflow_permissions WHERE bot_id = ? AND bot_owner_id = ? AND workflow_id = ? AND ${field} = 1 LIMIT 1`,
      [botId, botOwnerId, workflowId],
    );
    if (botRows.length > 0) return true;

    return false;
  }

  /**
   * Check if any permission records exist for a workflow.
   * Used by the engine to decide whether to fallback to YAML allowedBots.
   */
  async hasRecordsForWorkflow(workflowId: string): Promise<boolean> {
    const countRows = await this.db.query<{ cnt: number }>(
      "SELECT COUNT(*) as cnt FROM bot_workflow_permissions WHERE workflow_id = ?",
      [workflowId],
    );
    return countRows[0].cnt > 0;
  }

  /**
   * Get view permission info for a given owner (and optional bot).
   * Two-level logic matching hasEditPermission:
   *   - Owner-level (bot_id IS NULL OR '') grants access to all bots under that owner
   *   - Bot-level (bot_id = specific) grants access only for that bot
   *
   * Returns null if no permission records exist at all (unrestricted — all workflows viewable).
   * Otherwise returns { restrictedIds, viewableIds }:
   *   - restrictedIds: workflow IDs that have permission records (under access control)
   *   - viewableIds: workflow IDs the owner/bot can view (can_view=1)
   *
   * Whitelist model: a workflow is visible ONLY if it IS in viewableIds.
   *   - In restrictedIds AND in viewableIds → has record, has can_view=1 → show
   *   - In restrictedIds but NOT in viewableIds → has record, no can_view → hide
   *   - NOT in restrictedIds → no permission record → hide (whitelist)
   */
  async getViewByIdsForOwner(ownerId: string, botId?: string): Promise<{ restrictedIds: Set<string>; viewableIds: Set<string> } | null> {
    // All workflow IDs that have permission records (under access control)
    const restrictedRows = await this.db.query<Pick<BotWorkflowPermissionRow, "workflow_id">>(
      "SELECT DISTINCT workflow_id FROM bot_workflow_permissions",
      [],
    );
    const restrictedIds = new Set(restrictedRows.map((r) => r.workflow_id));

    // When botId is not provided (Web UI / owner-level query):
    //   Query ALL viewable or editable records under this owner, regardless of bot_id value.
    //   Edit permission necessarily includes management-page visibility.
    //   Also include workflows where bot_owner_id='*' grants access to everyone.
    // When botId is provided (engine runtime / bot-specific query):
    //   Two-level check: owner-level (bot_id IS NULL OR '') first, then bot-level, union both.
    //   Also include global wildcard (bot_owner_id='*') grants.
    let viewRows: Pick<BotWorkflowPermissionRow, "workflow_id">[];
    if (botId) {
      viewRows = await this.db.query<Pick<BotWorkflowPermissionRow, "workflow_id">>(
        `SELECT DISTINCT workflow_id FROM bot_workflow_permissions
         WHERE (bot_owner_id = ? AND can_view = 1
           AND (bot_id IS NULL OR bot_id = '' OR bot_id = ?))
         OR ((bot_id IS NULL OR bot_id = '') AND bot_owner_id = '*' AND can_view = 1)`,
        [ownerId, botId],
      );
    } else {
      viewRows = await this.db.query<Pick<BotWorkflowPermissionRow, "workflow_id">>(
        `SELECT DISTINCT workflow_id FROM bot_workflow_permissions
         WHERE (bot_owner_id = ? AND (can_view = 1 OR can_edit = 1))
         OR ((bot_id IS NULL OR bot_id = '') AND bot_owner_id = '*' AND can_view = 1)`,
        [ownerId],
      );
    }
    const viewableIds = new Set(viewRows.map((r) => r.workflow_id));

    return { restrictedIds, viewableIds };
  }

  /**
   * Check if a user has edit permission on a workflow (whitelist model).
   *   1. No permission records for this workflow → deny (whitelist)
   *   2. Check global wildcard (bot_owner_id='*') → allow if can_edit = 1
   *   3. Web user check (no botId): any editable row whose bot_owner_id matches.
   *   4. Bot runtime check (botId provided): owner-level or exact bot-level row.
   *   5. Otherwise deny.
   */
  async hasEditPermission(workflowId: string, userId: string, botId?: string): Promise<boolean> {
    // Step 1: No permission records → deny (whitelist model)
    const countRows = await this.db.query<{ cnt: number }>(
      "SELECT COUNT(*) as cnt FROM bot_workflow_permissions WHERE workflow_id = ?",
      [workflowId],
    );
    if (countRows[0].cnt === 0) return false;

    // Step 2: Check global wildcard (bot_owner_id='*' grants edit to everyone)
    const globalRows = await this.db.query<Pick<BotWorkflowPermissionRow, "can_edit">>(
      `SELECT can_edit FROM bot_workflow_permissions WHERE workflow_id = ? AND (bot_id IS NULL OR bot_id = '') AND bot_owner_id = '*' AND can_edit = 1 LIMIT 1`,
      [workflowId],
    );
    if (globalRows.length > 0) return true;

    // Step 3: The management UI is user-scoped. A row written for one of the
    // user's bots still means that user owns the editable workflow permission.
    if (!botId) {
      const userRows = await this.db.query<Pick<BotWorkflowPermissionRow, "can_edit">>(
        `SELECT can_edit FROM bot_workflow_permissions WHERE workflow_id = ? AND bot_owner_id = ? AND can_edit = 1 LIMIT 1`,
        [workflowId, userId],
      );
      return userRows.length > 0;
    }

    // Step 4: A concrete bot can inherit owner-level permission.
    const ownerRows = await this.db.query<Pick<BotWorkflowPermissionRow, "can_edit">>(
      `SELECT can_edit FROM bot_workflow_permissions WHERE workflow_id = ? AND bot_owner_id = ? AND (bot_id IS NULL OR bot_id = '') AND can_edit = 1 LIMIT 1`,
      [workflowId, userId],
    );
    if (ownerRows.length > 0) return true;

    // Step 5: Or receive permission through its exact bot-level row.
    const botRows = await this.db.query<Pick<BotWorkflowPermissionRow, "can_edit">>(
      `SELECT can_edit FROM bot_workflow_permissions WHERE workflow_id = ? AND bot_owner_id = ? AND bot_id = ? AND can_edit = 1 LIMIT 1`,
      [workflowId, userId, botId],
    );
    if (botRows.length > 0) return true;

    // Step 6: Has records but no edit permission.
    return false;
  }

  /** Cascade update workflow_id in permissions when a workflow is renamed */
  async updateWorkflowId(oldWorkflowId: string, newWorkflowId: string): Promise<void> {
    const env = getCurrentEnv();
    await this.db.exec(
      "UPDATE bot_workflow_permissions SET workflow_id = ? WHERE workflow_id = ? AND env = ?",
      [newWorkflowId, oldWorkflowId, env],
    );
  }
}
