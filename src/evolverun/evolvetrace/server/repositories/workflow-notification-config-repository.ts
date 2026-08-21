/**
 * Stub WorkflowNotificationConfigRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export type WorkflowNotificationConfigRow = {
  id: number;
  workflow_id: string;
  robot_code: string;
  app_secret: string;
  on_failure_users: string;
  on_failure_groups: string;
  on_failure_message_title: string | null;
  on_failure_message_include_run_link: number;
  gmt_create: number;
  gmt_modified: number;
};

export type NotificationConfigUpsertInput = {
  robotCode: string;
  appSecret: string;
  onFailureUsers: Array<{ userId: string; name?: string }>;
  onFailureGroups: Array<{ openConversationId: string; name?: string }>;
  onFailureMessageTitle: string | null;
  onFailureMessageIncludeRunLink: boolean;
};

export class WorkflowNotificationConfigRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(_workflowId: string): Promise<WorkflowNotificationConfigRow | null> {
    return null;
  }

  async upsert(_workflowId: string, _data: NotificationConfigUpsertInput): Promise<void> {
    // Stub: no-op
  }

  async delete(_workflowId: string): Promise<boolean> {
    return false;
  }
}
