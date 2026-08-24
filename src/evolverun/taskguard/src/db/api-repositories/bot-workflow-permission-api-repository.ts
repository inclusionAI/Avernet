/**
 * BotWorkflowPermissionApiRepository — HTTP client for bot workflow permission checks.
 *
 * Calls evolvetrace's /api/internal/bot-workflow-permissions/check endpoint.
 */
import type { ApiClient } from "../api-client.js";
import type { BotPermissionCheckResult, IBotWorkflowPermissionRepository } from "../repositories/types.js";

export class BotWorkflowPermissionApiRepository implements IBotWorkflowPermissionRepository {
  constructor(private api: ApiClient) {}

  async checkExecutePermission(
    botId: string,
    botOwnerId: string,
    workflowId: string,
  ): Promise<BotPermissionCheckResult> {
    try {
      const resp = await this.api.post<{ allowed: boolean; hasRecords: boolean }>(
        "/api/internal/bot-workflow-permissions/check",
        { botId, botOwnerId, workflowId, permission: "execute" },
      );
      if (!resp.ok || !resp.data) return { allowed: true, hasRecords: false };
      return {
        allowed: resp.data.allowed ?? true,
        hasRecords: resp.data.hasRecords ?? false,
      };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[BotPermApi] checkExecutePermission failed: ${msg}`);
      // On API failure, fallback to allowing execution
      return { allowed: true, hasRecords: false };
    }
  }
}