/**
 * Repository for workflow_notification_configs table — per-workflow DingTalk failure notification settings.
 */
import type { IDatabase } from "../db.js";

export type NotificationConfigRow = {
  id: number;
  workflow_id: string;
  robot_code: string;
  app_secret: string;
  on_failure_users: string; // JSON array
  on_failure_groups: string; // JSON array
  on_failure_message_title: string | null;
  on_failure_message_include_run_link: number;
  gmt_create: number;
  gmt_modified: number;
};

export type NotificationConfigUpsert = {
  robotCode: string;
  appSecret: string;
  onFailureUsers: Array<{ userId: string; name?: string }>;
  onFailureGroups: Array<{ openConversationId: string; name?: string }>;
  onFailureMessageTitle?: string | null;
  onFailureMessageIncludeRunLink: boolean;
};

const SELECT_COLUMNS = "id, workflow_id, robot_code, app_secret, on_failure_users, on_failure_groups, on_failure_message_title, on_failure_message_include_run_link, gmt_create, gmt_modified" as const;

export class WorkflowNotificationConfigRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<NotificationConfigRow | null> {
    const rows = await this.db.query<NotificationConfigRow>(
      `SELECT ${SELECT_COLUMNS} FROM workflow_notification_configs WHERE workflow_id = ?`,
      [workflowId],
    );
    return rows[0] ?? null;
  }

  async upsert(workflowId: string, data: NotificationConfigUpsert): Promise<NotificationConfigRow> {
    const now = this.db.dialect.now();
    const robotCode = data.robotCode;
    const appSecret = data.appSecret;
    const usersJson = JSON.stringify(data.onFailureUsers);
    const groupsJson = JSON.stringify(data.onFailureGroups);
    const messageTitle = data.onFailureMessageTitle ?? null;
    const includeRunLink = data.onFailureMessageIncludeRunLink ? 1 : 0;

    const existing = await this.findByWorkflowId(workflowId);

    if (existing) {
      await this.db.exec(
        `UPDATE workflow_notification_configs
         SET robot_code = ?, app_secret = ?, on_failure_users = ?, on_failure_groups = ?,
             on_failure_message_title = ?, on_failure_message_include_run_link = ?, gmt_modified = ?
         WHERE workflow_id = ?`,
        [robotCode, appSecret, usersJson, groupsJson, messageTitle, includeRunLink, now, workflowId],
      );
      return { ...existing, robot_code: robotCode, app_secret: appSecret, on_failure_users: usersJson, on_failure_groups: groupsJson, on_failure_message_title: messageTitle, on_failure_message_include_run_link: includeRunLink, gmt_modified: now as number };
    }

    await this.db.exec(
      `INSERT INTO workflow_notification_configs
         (workflow_id, robot_code, app_secret, on_failure_users, on_failure_groups, on_failure_message_title, on_failure_message_include_run_link, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [workflowId, robotCode, appSecret, usersJson, groupsJson, messageTitle, includeRunLink, now, now],
    );

    const result = await this.findByWorkflowId(workflowId);
    return result!;
  }

  async delete(workflowId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM workflow_notification_configs WHERE workflow_id = ?",
      [workflowId],
    );
    return result.affectedRows > 0;
  }

  /** Cascade update workflow_id when a workflow is renamed */
  async updateWorkflowId(oldWorkflowId: string, newWorkflowId: string): Promise<void> {
    await this.db.exec(
      "UPDATE workflow_notification_configs SET workflow_id = ? WHERE workflow_id = ?",
      [newWorkflowId, oldWorkflowId],
    );
  }
}