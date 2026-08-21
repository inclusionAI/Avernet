/**
 * FlowRunApiRepository — HTTP client implementation of IFlowRunRepository.
 *
 * Calls evolvetrace's /api/internal/runs endpoints.
 * When ApiClient has no privateKeyB64, requests are sent unsigned.
 */
import type { ApiClient } from "../api-client.js";
import { FlowRunRepository, type FlowRunRow, type FlowRunInsert, type FlowRunCompletion, type FindFlowRunsOptions } from "../repositories/flow-run-repository.js";
import type { IFlowRunRepository } from "../repositories/types.js";

// ── API Request/Response types ──

type InternalRunBody = {
  flow_id?: string;
  workflow_id?: string;
  workflow_title?: string;
  status?: string;
  triggered_by?: string;
  params_json?: string;
  input_json?: string;
  node_count?: number;
  identity_key?: string;
  started_at?: number;
  credentials_json?: string;
  origin_session_key?: string;
  origin_session_id?: string;
  origin_bot_id?: string;
  user_id?: string;
  plugin_version?: string;
  engine?: string;
};

// ── Repository ──

export class FlowRunApiRepository implements IFlowRunRepository {
  constructor(private api: ApiClient) {}

  private normalizeRow(row: any): FlowRunRow {
    return {
      id: row.id ?? 0,
      flow_id: row.flow_id ?? "",
      workflow_id: row.workflow_id ?? "",
      workflow_title: row.workflow_title ?? null,
      status: row.status ?? "",
      params_json: row.params_json ?? null,
      input_json: row.input_json ?? null,
      result_json: row.result_json ?? null,
      node_count: row.node_count ?? 0,
      succeeded_count: row.succeeded_count ?? 0,
      failed_count: row.failed_count ?? 0,
      total_duration_ms: row.total_duration_ms ?? null,
      total_token_usage: row.total_token_usage ?? null,
      triggered_by: row.triggered_by ?? null,
      identity_key: row.identity_key ?? null,
      current_phase: row.current_phase ?? null,
      started_at: row.started_at ?? 0,
      completed_at: row.completed_at ?? null,
      credentials_json: row.credentials_json ?? null,
      origin_session_key: row.origin_session_key ?? null,
      origin_session_id: row.origin_session_id ?? null,
      origin_bot_id: row.origin_bot_id ?? null,
      user_id: row.user_id ?? null,
      plugin_version: row.plugin_version ?? null,
      engine: row.engine ?? null,
      gmt_create: row.gmt_create ?? 0,
      gmt_modified: row.gmt_modified ?? null,
    };
  }

  async insert(run: FlowRunInsert): Promise<boolean> {
    try {
      const body: InternalRunBody = {
        flow_id: run.flowId,
        workflow_id: run.workflowId,
        workflow_title: run.workflowTitle ?? undefined,
        status: run.status,
        triggered_by: run.triggeredBy ?? undefined,
        params_json: run.paramsJson ?? undefined,
        input_json: run.inputJson ?? undefined,
        node_count: run.nodeCount ?? 0,
        identity_key: run.identityKey ?? undefined,
        started_at: run.startedAt ?? Math.floor(Date.now() / 1000),
        credentials_json: run.credentialsJson ?? undefined,
        origin_session_key: run.originSessionKey ?? undefined,
        origin_session_id: run.originSessionId ?? undefined,
        origin_bot_id: run.originBotId ?? undefined,
        user_id: run.userId ?? undefined,
        plugin_version: run.pluginVersion ?? undefined,
        engine: run.engine ?? undefined,
      };
      const resp = await this.api.post<{ success: boolean; data: any }>("/api/internal/runs", body);
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] insert failed: ${msg}`);
      return false;
    }
  }

  async incrementNodeCount(flowId: string, field: "succeeded_count" | "failed_count"): Promise<boolean> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/increment-node`,
        { field },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] incrementNodeCount failed: ${msg}`);
      return false;
    }
  }

  async updateCompletion(flowId: string, completion: FlowRunCompletion): Promise<boolean> {
    try {
      const body: Record<string, unknown> = {
        status: completion.status,
        total_duration_ms: completion.totalDurationMs ?? null,
        total_token_usage: completion.totalTokenUsage ?? null,
        completed_at: completion.completedAt,
      };
      if (completion.resultJson !== undefined) body.result_json = completion.resultJson;
      if (completion.inputJson !== undefined) body.input_json = completion.inputJson;
      if (completion.succeededCount !== undefined) body.succeeded_count = completion.succeededCount;
      if (completion.failedCount !== undefined) body.failed_count = completion.failedCount;
      if (completion.currentPhase !== undefined) body.phase = completion.currentPhase;

      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/completion`,
        body,
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] updateCompletion failed: ${msg}`);
      return false;
    }
  }

  async updateStatus(flowId: string, status: string): Promise<boolean> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/status`,
        { status },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] updateStatus failed: ${msg}`);
      return false;
    }
  }

  async updateCurrentPhase(flowId: string, currentPhase: string): Promise<boolean> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/phase`,
        { phase: currentPhase },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] updateCurrentPhase failed: ${msg}`);
      return false;
    }
  }

  async updateNodeCount(flowId: string, nodeCount: number): Promise<boolean> {
    try {
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/node-count`,
        { node_count: nodeCount },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] updateNodeCount failed: ${msg}`);
      return false;
    }
  }

  async updateResultJson(flowId: string, nodeId: string, result: Record<string, unknown>): Promise<boolean> {
    try {
      const resultJson = JSON.stringify({ nodeId, ...result });
      const resp = await this.api.put<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}/result-json`,
        { result_json: resultJson },
      );
      return resp.ok;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] updateResultJson failed: ${msg}`);
      return false;
    }
  }

  async findByFlowId(flowId: string): Promise<FlowRunRow | null> {
    try {
      const resp = await this.api.get<{ success: boolean; data: any }>(
        `/api/internal/runs/${encodeURIComponent(flowId)}`,
      );
      if (!resp.ok || !resp.data) return null;
      return this.normalizeRow(resp.data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] findByFlowId failed: ${msg}`);
      return null;
    }
  }

  async findStaleRunning(cutoffEpochSecs: number, limit = 100): Promise<FlowRunRow[]> {
    try {
      const resp = await this.api.get<{ success: boolean; data: any[]; total?: number; limit?: number; offset?: number }>(
        "/api/internal/runs",
        { status: "running", limit: String(limit) },
      );
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      // Filter by cutoff: only include runs where started_at < cutoffEpochSecs
      return rows
        .filter((r: any) => r.started_at < cutoffEpochSecs)
        .slice(0, limit)
        .map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] findStaleRunning failed: ${msg}`);
      return [];
    }
  }

  async findRuns(options: FindFlowRunsOptions = {}): Promise<FlowRunRow[]> {
    try {
      const query: Record<string, string> = {};
      if (options.workflowId) query.workflowId = options.workflowId;
      if (options.status) query.status = options.status;
      if (options.identityKey) query.identityKey = options.identityKey;
      if (options.currentPhase) query.currentPhase = options.currentPhase;
      query.limit = String(options.limit ?? 20);
      query.offset = String(options.offset ?? 0);

      const resp = await this.api.get<{ success: boolean; data: any[]; total?: number; limit?: number; offset?: number }>(
        "/api/internal/runs",
        query,
      );
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows.map((r: any) => this.normalizeRow(r));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] findRuns failed: ${msg}`);
      return [];
    }
  }

  async findRunningByOrigin(botId: string, engine: string, limit = 50): Promise<Pick<FlowRunRow, "flow_id" | "status" | "started_at">[]> {
    try {
      const resp = await this.api.get<{ success: boolean; data: any[]; total?: number }>(
        "/api/internal/runs",
        { status: "running", limit: String(limit) },
      );
      if (!resp.ok) return [];
      const rows = Array.isArray(resp.data) ? resp.data : (resp.data?.data ?? []);
      return rows
        .filter((r: any) => r.origin_bot_id === botId && r.engine === engine)
        .slice(0, limit)
        .map((r: any) => ({
          flow_id: r.flow_id,
          status: r.status,
          started_at: r.started_at,
        }));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[FlowRunApi] findRunningByOrigin failed: ${msg}`);
      return [];
    }
  }
}