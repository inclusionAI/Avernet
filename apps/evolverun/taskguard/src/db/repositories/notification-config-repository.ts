/**
 * Repository for workflow_notification_configs table — per-workflow DingTalk failure notification settings.
 * Read-only in ClawMind; writes are managed by clawweb frontend.
 */
import type { IDatabase } from "../types.js";
import type { INotificationConfigRepository, NotificationConfigRow } from "./types.js";

export class NotificationConfigRepository implements INotificationConfigRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<NotificationConfigRow | null> {
    const rows = await this.db.query<NotificationConfigRow>(
      `SELECT id, workflow_id, robot_code, app_secret, on_failure_users, on_failure_groups,
              on_failure_message_title, on_failure_message_include_run_link, gmt_create, gmt_modified
       FROM workflow_notification_configs WHERE workflow_id = ?`,
      [workflowId],
    );
    return rows[0] ?? null;
  }
}

// Re-export the row type from types.ts for convenience
export type { NotificationConfigRow } from "./types.js";