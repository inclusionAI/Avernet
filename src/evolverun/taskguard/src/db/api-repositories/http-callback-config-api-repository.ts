/**
 * HttpCallbackConfigApiRepository — HTTP client implementation of IHttpCallbackConfigRepository.
 *
 * Calls evolvetrace's /api/workflows/:workflowId/callback-configs endpoints.
 *
 * Note: The evolvetrace server's HttpCallbackConfigRepository is currently a stub (returns
 * []/null/false for all operations). When the server implements real persistence, these HTTP
 * calls will transparently start returning real data. Until then, results will be empty.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IHttpCallbackConfigRepository,
  HttpCallbackConfigRow,
  HttpCallbackConfigInsert,
} from "../../alerts/http-callback-types.js";

export class HttpCallbackConfigApiRepository implements IHttpCallbackConfigRepository {
  constructor(private api: ApiClient) {}

  private normalizeRow(row: any): HttpCallbackConfigRow {
    return {
      id: row.id ?? 0,
      config_id: row.config_id ?? row.configId ?? "",
      workflow_id: row.workflow_id ?? row.workflowId ?? "",
      name: row.name ?? "",
      url: row.url ?? "",
      secret: row.secret ?? null,
      enabled: row.enabled ?? 0,
      notify_on: typeof row.notify_on === "string" ? row.notify_on : JSON.stringify(row.notify_on ?? row.notifyOn ?? []),
      timeout_ms: row.timeout_ms ?? row.timeoutMs ?? 5000,
      max_retries: row.max_retries ?? row.maxRetries ?? 2,
      retry_delay_ms: row.retry_delay_ms ?? row.retryDelayMs ?? 1000,
      include_node_output: row.include_node_output ?? row.includeNodeOutput ?? 0,
      gmt_create: row.gmt_create ?? 0,
      gmt_modified: row.gmt_modified ?? 0,
    };
  }

  async findByWorkflowId(workflowId: string): Promise<HttpCallbackConfigRow[]> {
    try {
      const resp = await this.api.get<any[]>(
        `/api/workflows/${encodeURIComponent(workflowId)}/callback-configs`,
      );
      if (!resp.ok || !Array.isArray(resp.data)) return [];
      return resp.data.map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] findByWorkflowId failed: ${msg}`);
      return [];
    }
  }

  async findByConfigId(configId: string): Promise<HttpCallbackConfigRow | null> {
    try {
      // No single-config GET endpoint; list all for the workflow extracted from configId.
      // configId format: cfg:<workflowId>:<timestamp>
      const parts = configId.split(":");
      if (parts.length < 2) return null;
      const workflowId = parts[1];
      const all = await this.findByWorkflowId(workflowId);
      return all.find((r) => r.config_id === configId) ?? null;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] findByConfigId failed: ${msg}`);
      return null;
    }
  }

  async findAllWorkflowIds(): Promise<string[]> {
    // No dedicated endpoint; return empty.
    console.warn(
      "[HttpCallbackConfigApi] findAllWorkflowIds is not supported over HTTP API mode.",
    );
    return [];
  }

  async insert(config: HttpCallbackConfigInsert): Promise<number> {
    try {
      const body: Record<string, unknown> = {
        name: config.name,
        url: config.url,
        secret: config.secret ?? undefined,
        notifyOn: typeof config.notifyOn === "string" ? JSON.parse(config.notifyOn) : config.notifyOn,
        enabled: config.enabled !== 0,
        timeoutMs: config.timeoutMs,
        maxRetries: config.maxRetries,
        retryDelayMs: config.retryDelayMs,
        includeNodeOutput: config.includeNodeOutput === 1,
      };
      const resp = await this.api.post<{ id: number }>(
        `/api/workflows/${encodeURIComponent(config.workflowId)}/callback-configs`,
        body,
      );
      if (!resp.ok) return 0;
      return resp.data?.id ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] insert failed: ${msg}`);
      return 0;
    }
  }

  async update(configId: string, config: Partial<HttpCallbackConfigInsert>): Promise<boolean> {
    try {
      // Extract workflowId from configId: cfg:<workflowId>:<timestamp>
      const parts = configId.split(":");
      if (parts.length < 2) return false;
      const workflowId = parts[1];

      const body: Record<string, unknown> = {};
      if (config.name !== undefined) body.name = config.name;
      if (config.url !== undefined) body.url = config.url;
      if (config.secret !== undefined) body.secret = config.secret;
      if (config.notifyOn !== undefined) {
        body.notifyOn = typeof config.notifyOn === "string" ? JSON.parse(config.notifyOn) : config.notifyOn;
      }
      if (config.enabled !== undefined) body.enabled = config.enabled !== 0;
      if (config.timeoutMs !== undefined) body.timeoutMs = config.timeoutMs;
      if (config.maxRetries !== undefined) body.maxRetries = config.maxRetries;
      if (config.retryDelayMs !== undefined) body.retryDelayMs = config.retryDelayMs;
      if (config.includeNodeOutput !== undefined) body.includeNodeOutput = config.includeNodeOutput === 1;

      const resp = await this.api.put<{ ok: boolean }>(
        `/api/workflows/${encodeURIComponent(workflowId)}/callback-configs/${encodeURIComponent(configId)}`,
        body,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] update failed: ${msg}`);
      return false;
    }
  }

  async deleteByConfigId(configId: string): Promise<boolean> {
    try {
      const parts = configId.split(":");
      if (parts.length < 2) return false;
      const workflowId = parts[1];
      const resp = await this.api.delete<{ ok: boolean }>(
        `/api/workflows/${encodeURIComponent(workflowId)}/callback-configs/${encodeURIComponent(configId)}`,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] deleteByConfigId failed: ${msg}`);
      return false;
    }
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    // No bulk-delete endpoint; delete one by one.
    try {
      const all = await this.findByWorkflowId(workflowId);
      let count = 0;
      for (const row of all) {
        const ok = await this.deleteByConfigId(row.config_id);
        if (ok) count++;
      }
      return count;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[HttpCallbackConfigApi] deleteByWorkflowId failed: ${msg}`);
      return 0;
    }
  }
}