/**
 * FacadeBindingApiRepository — HTTP client implementation of IFacadeBindingRepository.
 *
 * Calls evolvetrace's /api/internal/facades endpoints.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IFacadeBindingRepository,
  FacadeBindingRow,
  FacadeBindingInsert,
} from "../repositories/types.js";

export class FacadeBindingApiRepository implements IFacadeBindingRepository {
  constructor(private api: ApiClient) {}

  private normalizeRow(row: any): FacadeBindingRow {
    return {
      id: row.id ?? 0,
      command: row.command ?? "",
      workflow_id: row.workflow_id ?? "",
      pack_id: row.pack_id ?? null,
      remark: row.remark ?? null,
      gmt_create: row.gmt_create ?? 0,
      gmt_modified: row.gmt_modified ?? 0,
    };
  }

  async findByCommand(command: string): Promise<FacadeBindingRow | null> {
    try {
      // The API doesn't have a GET /:command endpoint, so list all and filter.
      // In practice, we'll fetch the list and find by command.
      const resp = await this.api.get<{ success: boolean; data: any[] }>("/api/internal/facades");
      if (!resp.ok) return null;
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      const found = rows.find((r: any) => r.command === command);
      return found ? this.normalizeRow(found) : null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] findByCommand failed: ${msg}`);
      return null;
    }
  }

  async findByWorkflowId(workflowId: string): Promise<FacadeBindingRow[]> {
    try {
      const resp = await this.api.get<{ success: boolean; data: any[] }>("/api/internal/facades");
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows
        .filter((r: any) => r.workflow_id === workflowId)
        .map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] findByWorkflowId failed: ${msg}`);
      return [];
    }
  }

  async listAll(_botId?: string, _botOwnerId?: string): Promise<FacadeBindingRow[]> {
    try {
      const resp = await this.api.get<{ success: boolean; data: any[] }>("/api/internal/facades");
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      // botId/botOwnerId filtering is handled server-side (facade bindings don't have
      // bot_id/bot_owner_id columns — permission filtering happens at the workflow level).
      return rows.map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] listAll failed: ${msg}`);
      return [];
    }
  }

  async upsert(insert: FacadeBindingInsert): Promise<FacadeBindingRow> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        "/api/internal/facades",
        {
          command: insert.command,
          workflowId: insert.workflow_id,
          packId: insert.pack_id ?? undefined,
          remark: insert.remark ?? undefined,
        },
      );
      if (!resp.ok || !resp.data) throw new Error("upsert failed");
      return this.normalizeRow(resp.data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] upsert failed: ${msg}`);
      throw error;
    }
  }

  async deleteByCommand(command: string): Promise<boolean> {
    try {
      const resp = await this.api.delete<{ success: boolean; data: any }>(
        `/api/internal/facades/${encodeURIComponent(command)}`,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] deleteByCommand failed: ${msg}`);
      return false;
    }
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    try {
      const resp = await this.api.delete<{ deleted: number }>(
        `/api/internal/facades/by-workflow/${encodeURIComponent(workflowId)}`,
      );
      if (!resp.ok || !resp.data) return 0;
      return resp.data.deleted ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FacadeBindingApi] deleteByWorkflowId failed: ${msg}`);
      return 0;
    }
  }
}