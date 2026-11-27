/** Verified clawweb read protocols only. Validation and Domain mapping belong to Service. */
import { backendRequest } from '../httpClient';

export interface OverviewIdentityParams {
  user_id?: string;
}
export interface OverviewWindowParams extends OverviewIdentityParams {
  days?: number;
}
export interface OverviewRunsParams extends OverviewIdentityParams {
  workflowId: string;
  limit?: number;
  offset?: number;
  status?: string;
  statuses?: string;
  query?: string;
  from?: number;
  to?: number;
}
export interface OverviewRunDto {
  flow_id: string;
  workflow_id: string;
  status: string;
  workflow_title?: unknown;
  node_count?: unknown;
  succeeded_count?: unknown;
  failed_count?: unknown;
  started_at?: unknown;
  completed_at?: unknown;
  total_duration_ms?: unknown;
  total_token_usage?: unknown;
  user_id?: unknown;
  triggered_by?: unknown;
  origin_bot_id?: unknown;
  plugin_version?: unknown;
  workflow_version?: unknown;
  engine?: unknown;
  evolution_analysis_status?: unknown;
}
export interface OverviewRunsDto {
  runs: OverviewRunDto[];
  total: number;
  statusCounts?: Record<string, number>;
}
export interface OverviewNodeDto {
  flow_id: string;
  node_id: string;
  status: string;
  attempt?: unknown;
  node_title?: unknown;
  executor_type?: unknown;
  triggered_by?: unknown;
  phase?: unknown;
  started_at?: unknown;
  completed_at?: unknown;
  duration_ms?: unknown;
  input_json?: unknown;
  output_json?: unknown;
  error_text?: unknown;
  token_usage_json?: unknown;
  system_context_json?: unknown;
  resolved_prompt?: unknown;
  progress_message?: unknown;
  session_key?: unknown;
  embedded_session_key?: unknown;
  session_id?: unknown;
}
/** Existing detail columns and default-off, server-authorized runtime projection only. */
export interface OverviewDetailedRunDto extends OverviewRunDto {
  input_json?: unknown;
  params_json?: unknown;
  parent_flow_id?: unknown;
  root_flow_id?: unknown;
  device_id?: unknown;
  bot_type?: unknown;
  stage?: unknown;
}
export interface OverviewRunDetailDto {
  run: OverviewDetailedRunDto;
  nodes: OverviewNodeDto[];
}
export interface OverviewHealthDto {
  overallScore?: unknown;
  p95DurationMs?: unknown;
  successRate?: unknown;
  nodeFailureRate?: unknown;
  retryRate?: unknown;
  totalTokens?: unknown;
  bottleneckNode?: unknown;
  fragileNode?: unknown;
  recommendation?: unknown;
}
export interface OverviewTrendDto {
  date: string;
  totalRuns: number;
  succeededRuns: number;
  failedRuns: number;
  successRate: number;
}
export interface OverviewController {
  runs: (params: OverviewRunsParams, signal?: AbortSignal) => Promise<OverviewRunsDto>;
  run: (id: string, params: OverviewIdentityParams, signal?: AbortSignal) => Promise<OverviewRunDetailDto>;
  nodes: (id: string, params: OverviewIdentityParams, signal?: AbortSignal) => Promise<OverviewNodeDto[]>;
  workflow: (id: string, params: OverviewIdentityParams, signal?: AbortSignal) => Promise<unknown>;
  health: (id: string, params: OverviewWindowParams, signal?: AbortSignal) => Promise<{ data: OverviewHealthDto }>;
  trend: (id: string, params: OverviewWindowParams, signal?: AbortSignal) => Promise<{ data: OverviewTrendDto[] }>;
  nodeStats: (id: string, params: OverviewWindowParams, signal?: AbortSignal) => Promise<unknown>;
  archive: (id: string, params: OverviewIdentityParams, signal?: AbortSignal) => Promise<unknown>;
  timeline: (id: string, params: OverviewIdentityParams, signal?: AbortSignal) => Promise<unknown>;
}
const BASE = '/openapi/v1/harnessflow/api';
const workflowPath = (id: string) => `${BASE}/workflows/${encodeURIComponent(id)}`;
const runPath = (id: string) => `${BASE}/runs/${encodeURIComponent(id)}`;
const archivePath = (id: string) => `${BASE}/run-archives/${encodeURIComponent(id)}`;
function get<T>(url: string, params: object, signal?: AbortSignal): Promise<T> {
  // Explicitly disable global identity injection even while context is being cleared.
  return backendRequest<T>(url, { method: 'GET', params: { ...params }, signal, injectUserId: false });
}
export const overviewController: OverviewController = {
  runs: (params, signal) => get(`${BASE}/runs`, params, signal),
  run: (id, params, signal) => get(runPath(id), params, signal),
  nodes: (id, params, signal) => get(`${runPath(id)}/nodes`, { ...params, full: true }, signal),
  workflow: (id, params, signal) => get(workflowPath(id), params, signal),
  health: (id, params, signal) => get(`${workflowPath(id)}/health`, params, signal),
  trend: (id, params, signal) => get(`${workflowPath(id)}/success-trend`, params, signal),
  nodeStats: (id, params, signal) => get(`${workflowPath(id)}/node-stats`, params, signal),
  archive: (id, params, signal) => get(archivePath(id), params, signal),
  timeline: (id, params, signal) => get(`${archivePath(id)}/timeline`, params, signal),
};
