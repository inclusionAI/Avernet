/**
 * NodeExecutionApiRepository — HTTP client implementation of INodeExecutionRepository.
 *
 * Calls evolvetrace's /api/internal/node-executions endpoints.
 */
import type { ApiClient } from "../api-client.js";
import type {
  INodeExecutionRepository,
  NodeExecutionRow,
  NodeExecutionInsert,
  NodeExecutionCompletion,
  FindNodeExecutionsOptions,
} from "../repositories/types.js";

// ── Repository ──

export class NodeExecutionApiRepository implements INodeExecutionRepository {
  private maxIoBytes: number;

  constructor(private api: ApiClient, maxIoSizeKb: number = 10) {
    this.maxIoBytes = maxIoSizeKb * 1024;
  }

  private normalizeRow(row: any): NodeExecutionRow {
    return {
      id: row.id ?? 0,
      flow_id: row.flow_id ?? "",
      workflow_id: row.workflow_id ?? "",
      node_id: row.node_id ?? "",
      executor_type: row.executor_type ?? null,
      status: row.status ?? "",
      attempt: row.attempt ?? 0,
      input_json: row.input_json ?? null,
      output_json: row.output_json ?? null,
      error_text: row.error_text ?? null,
      duration_ms: row.duration_ms ?? null,
      token_usage_json: row.token_usage_json ?? null,
      node_title: row.node_title ?? null,
      progress_message: row.progress_message ?? null,
      session_key: row.session_key ?? null,
      session_id: row.session_id ?? null,
      triggered_by: row.triggered_by ?? null,
      branch_id: row.branch_id ?? null,
      embedded_session_key: row.embedded_session_key ?? null,
      system_context_json: row.system_context_json ?? null,
      resolved_prompt: row.resolved_prompt ?? null,
      version: row.version ?? 0,
      started_at: row.started_at ?? 0,
      completed_at: row.completed_at ?? null,
      gmt_create: row.gmt_create ?? 0,
      gmt_modified: row.gmt_modified ?? null,
    };
  }

  private truncateJson(json: string | null | undefined, maxBytes: number): string | null {
    if (json === null || json === undefined) return null;
    if (Buffer.byteLength(json, "utf8") <= maxBytes) return json;
    // Simple truncation: cut at maxBytes and close JSON
    try {
      const parsed = JSON.parse(json);
      const str = JSON.stringify(parsed);
      if (Buffer.byteLength(str, "utf8") <= maxBytes) return str;
      return str.substring(0, maxBytes) + " [truncated]";
    } catch {
      return json.substring(0, maxBytes) + " [truncated]";
    }
  }

  private truncateError(text: string | null | undefined): string | null {
    if (!text) return null;
    const MAX = 4000;
    return text.length > MAX ? text.substring(0, MAX - 14) + "... [truncated]" : text;
  }

  async insert(exec: NodeExecutionInsert): Promise<{ insertId: number; affectedRows: number }> {
    try {
      const body: Record<string, unknown> = {
        flow_id: exec.flowId,
        workflow_id: exec.workflowId,
        node_id: exec.nodeId,
        status: exec.status,
        attempt: exec.attempt ?? 1,
        started_at: exec.startedAt ?? Math.floor(Date.now() / 1000),
        version: exec.version ?? 1,
      };
      if (exec.executorType) body.executor_type = exec.executorType;
      if (exec.inputJson !== undefined) body.input_json = this.truncateJson(exec.inputJson, this.maxIoBytes);
      if (exec.outputJson !== undefined) body.output_json = this.truncateJson(exec.outputJson, this.maxIoBytes);
      if (exec.errorText !== undefined) body.error_text = this.truncateError(exec.errorText);
      if (exec.durationMs !== undefined) body.duration_ms = exec.durationMs;
      if (exec.tokenUsageJson !== undefined) body.token_usage_json = exec.tokenUsageJson;
      if (exec.nodeTitle !== undefined) body.node_title = exec.nodeTitle;
      if (exec.progressMessage !== undefined) body.progress_message = exec.progressMessage;
      if (exec.sessionKey !== undefined) body.session_key = exec.sessionKey;
      if (exec.sessionId !== undefined) body.session_id = exec.sessionId;
      if (exec.embeddedSessionKey !== undefined) body.embedded_session_key = exec.embeddedSessionKey?.substring(0, 512);
      if (exec.systemContextJson !== undefined) body.system_context_json = this.truncateJson(exec.systemContextJson, this.maxIoBytes);
      if (exec.resolvedPrompt !== undefined) body.resolved_prompt = exec.resolvedPrompt;

      const resp = await this.api.post<{ insertId: number }>("/api/internal/node-executions", body);
      if (!resp.ok || !resp.data) return { insertId: -1, affectedRows: 0 };
      return { insertId: resp.data.insertId ?? -1, affectedRows: 1 };
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] insert failed: ${msg}`);
      return { insertId: -1, affectedRows: 0 };
    }
  }

  async updateCompletion(id: number, completion: NodeExecutionCompletion): Promise<boolean> {
    try {
      const body: Record<string, unknown> = { status: completion.status };
      if (completion.outputJson !== undefined) body.output_json = completion.outputJson;
      if (completion.errorText !== undefined) body.error_text = completion.errorText;
      if (completion.durationMs !== undefined) body.duration_ms = completion.durationMs;
      if (completion.tokenUsageJson !== undefined) body.token_usage_json = completion.tokenUsageJson;
      if (completion.embeddedSessionKey !== undefined) body.embedded_session_key = completion.embeddedSessionKey;
      if (completion.systemContextJson !== undefined) body.system_context_json = completion.systemContextJson;
      if (completion.resolvedPrompt !== undefined) body.resolved_prompt = completion.resolvedPrompt;
      body.completed_at = completion.completedAt;
      if (completion.startedAt !== undefined) body.started_at = completion.startedAt;
      if ((completion as any).expectedVersion !== undefined) body.expected_version = (completion as any).expectedVersion;

      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/node-executions/${id}/completion`,
        body,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] updateCompletion(id) failed: ${msg}`);
      return false;
    }
  }

  async updateCompletionByFlowNode(flowId: string, nodeId: string, attempt: number, completion: NodeExecutionCompletion): Promise<boolean> {
    try {
      const body: Record<string, unknown> = { status: completion.status };
      if (completion.outputJson !== undefined) body.output_json = completion.outputJson;
      if (completion.errorText !== undefined) body.error_text = completion.errorText;
      if (completion.durationMs !== undefined) body.duration_ms = completion.durationMs;
      if (completion.tokenUsageJson !== undefined) body.token_usage_json = completion.tokenUsageJson;
      if (completion.embeddedSessionKey !== undefined) body.embedded_session_key = completion.embeddedSessionKey;
      if (completion.systemContextJson !== undefined) body.system_context_json = completion.systemContextJson;
      if (completion.resolvedPrompt !== undefined) body.resolved_prompt = completion.resolvedPrompt;
      body.completed_at = completion.completedAt;
      if ((completion as any).expectedVersion !== undefined) body.expected_version = (completion as any).expectedVersion;

      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/node-executions/${encodeURIComponent(flowId)}/${encodeURIComponent(nodeId)}/${attempt}/completion`,
        body,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] updateCompletionByFlowNode failed: ${msg}`);
      return false;
    }
  }

  async updateProgressMessage(flowId: string, nodeId: string, attempt: number, message: string): Promise<boolean> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/node-executions/${encodeURIComponent(flowId)}/${encodeURIComponent(nodeId)}/${attempt}/progress`,
        { message: message?.substring(0, 100) },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] updateProgressMessage failed: ${msg}`);
      return false;
    }
  }

  async findByFlowId(flowId: string, options: FindNodeExecutionsOptions = {}): Promise<NodeExecutionRow[]> {
    try {
      const query: Record<string, string> = { flowId };
      if (options.limit) query.limit = String(options.limit);
      if (options.offset) query.offset = String(options.offset);

      const resp = await this.api.get<{ success: boolean; data: any[]; total?: number; limit?: number; offset?: number }>(
        "/api/internal/node-executions",
        query,
      );
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows.map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async findByFlowAndNode(flowId: string, nodeId: string, limit: number = 50): Promise<NodeExecutionRow[]> {
    try {
      const allRows = await this.findByFlowId(flowId, { limit: limit * 10 });
      return allRows
        .filter((r) => r.node_id === nodeId)
        .sort((a, b) => a.attempt - b.attempt)
        .slice(0, limit);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] findByFlowAndNode failed: ${msg}`);
      return [];
    }
  }

  async findLatestByFlowId(flowId: string): Promise<NodeExecutionRow[]> {
    try {
      const allRows = await this.findByFlowId(flowId, { limit: 1000 });
      const latestByNode = new Map<string, NodeExecutionRow>();
      for (const row of allRows) {
        const key = row.node_id;
        const existing = latestByNode.get(key);
        if (!existing || row.attempt > existing.attempt) {
          latestByNode.set(key, row);
        }
      }
      return Array.from(latestByNode.values());
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] findLatestByFlowId failed: ${msg}`);
      return [];
    }
  }

  async reconcileStaleRunning(flowId: string, flowStatus: string): Promise<number> {
    try {
      const resp = await this.api.put<{ reconciled: number }>(
        `/api/internal/node-executions/${encodeURIComponent(flowId)}/reconcile-stale-running`,
        { flow_status: flowStatus },
      );
      if (!resp.ok || !resp.data) return 0;
      return resp.data.reconciled ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[NodeExecApi] reconcileStaleRunning failed: ${msg}`);
      return 0;
    }
  }
}