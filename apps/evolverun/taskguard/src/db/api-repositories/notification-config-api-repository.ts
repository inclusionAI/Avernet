/**
 * NotificationConfigApiRepository — HTTP client implementation of INotificationConfigRepository.
 *
 * Calls evolvetrace's /api/workflows/:workflowId/notification-config endpoint.
 *
 * Note: The evolvetrace server's WorkflowNotificationConfigRepository is currently a stub
 * (returns null for findByWorkflowId). When the server implements real persistence, these
 * HTTP calls will transparently start returning real data.
 */
import type { ApiClient } from "../api-client.js";
import type {
  INotificationConfigRepository,
  NotificationConfigRow,
} from "../repositories/types.js";

export class NotificationConfigApiRepository implements INotificationConfigRepository {
  constructor(private api: ApiClient) {}

  private normalizeRow(workflowId: string, row: any): NotificationConfigRow {
    return {
      id: row.id ?? 0,
      workflow_id: row.workflowId ?? workflowId,
      robot_code: row.robotCode ?? "",
      app_secret: row.appSecret ?? "",
      on_failure_users: typeof row.onFailureUsers === "string"
        ? row.onFailureUsers
        : JSON.stringify(row.onFailureUsers ?? []),
      on_failure_groups: typeof row.onFailureGroups === "string"
        ? row.onFailureGroups
        : JSON.stringify(row.onFailureGroups ?? []),
      on_failure_message_title: row.onFailureMessageTitle ?? null,
      on_failure_message_include_run_link: row.onFailureMessageIncludeRunLink ? 1 : 0,
      gmt_create: row.gmt_create ?? 0,
      gmt_modified: row.gmt_modified ?? 0,
    };
  }

  async findByWorkflowId(workflowId: string): Promise<NotificationConfigRow | null> {
    try {
      const resp = await this.api.get<any>(
        `/api/workflows/${encodeURIComponent(workflowId)}/notification-config`,
      );
      if (!resp.ok || !resp.data) return null;
      return this.normalizeRow(workflowId, resp.data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NotificationConfigApi] findByWorkflowId failed: ${msg}`);
      return null;
    }
  }
}