/**
 * HTTP Callback Notification Types
 *
 * Type definitions for the HTTP callback notification system that
 * pushes workflow lifecycle events to external subsystems via HTTP POST.
 *
 * Callbacks are fire-and-forget (best-effort delivery) — they never
 * block workflow execution.
 */

// Import canonical types from types.ts (the project's type hub) for local use
import type { NotifyEvent, HttpCallbackNotification } from "../types.js";
// Re-export for consumers that import from this module
export type { NotifyEvent, HttpCallbackNotification } from "../types.js";

/** HTTP callback notification configuration (per-workflow). */
export type HttpCallbackConfig = {
  /** Unique identifier for this config entry. */
  id: string;
  /** Workflow ID this config belongs to. */
  workflowId: string;
  /** Human-readable name for this callback config (e.g., "监控平台通知"). */
  name: string;
  /** Target URL to receive the HTTP POST callback. Must use HTTPS. */
  url: string;
  /** HMAC-SHA256 signing secret for payload integrity verification. Optional — when omitted, no signature headers are sent. */
  secret?: string;
  /** Whether this callback is active. */
  enabled: boolean;
  /** Which lifecycle events should trigger this callback. */
  notifyOn: NotifyEvent[];
  /** HTTP request timeout in milliseconds. Default: 5000. */
  timeoutMs: number;
  /** Maximum retry attempts for 5xx / network errors. Default: 2. */
  maxRetries: number;
  /** Delay between retry attempts in milliseconds. Default: 1000. */
  retryDelayMs: number;
  /** Whether ext_info should include node output_json (can be large). Default: false. */
  includeNodeOutput: boolean;
  /** Creation timestamp (epoch seconds). */
  createdAt?: number;
  /** Last modification timestamp (epoch seconds). */
  updatedAt?: number;
};

/** Payload sent to the callback URL via HTTP POST. */
export type HttpCallbackPayload = {
  /** Workflow ID (e.g., "risk-review-pipeline"). */
  workflow_id: string;
  /** Flow execution instance ID. */
  flow_id: string;
  /** Current notification status/event type. */
  status: string;
  /** Extended information containing flow_runs and node_executions snapshots. */
  ext_info: ExtInfo;
};

/** Extended information attached to every callback payload. */
export type ExtInfo = {
  /** Snapshot of the flow_runs record for this execution. */
  flow_runs: FlowRunSnapshot | null;
  /** Snapshots of all node_executions records for this flow. */
  node_executions: NodeExecutionSnapshot[];
};

/** Flow run snapshot included in ext_info — mirrors all flow_runs table columns. */
export type FlowRunSnapshot = {
  id: number;
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  params_json: string | null;
  input_json: string | null;
  result_json: string | null;
  node_count: number;
  succeeded_count: number;
  failed_count: number;
  total_duration_ms: number | null;
  total_token_usage: number | null;
  triggered_by: string | null;
  identity_key: string | null;
  current_phase: string | null;
  started_at: number;
  completed_at: number | null;
  credentials_json: string | null;
  origin_session_key: string | null;
  origin_session_id: string | null;
  origin_bot_id: string | null;
  user_id: string | null;
  plugin_version: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

/** Node execution snapshot included in ext_info — mirrors all node_executions table columns. */
export type NodeExecutionSnapshot = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  executor_type: string | null;
  status: string;
  attempt: number;
  input_json: string | null;
  /** Included only when includeNodeOutput=true. */
  output_json?: string | null;
  error_text: string | null;
  duration_ms: number | null;
  token_usage_json: string | null;
  node_title: string | null;
  triggered_by: string | null;
  branch_id: string | null;
  progress_message: string | null;
  session_key: string | null;
  session_id: string | null;
  system_context_json: string | null;
  embedded_session_key: string | null;
  started_at: number;
  completed_at: number | null;
  gmt_create: number;
  gmt_modified: number | null;
};

/** DB row for the http_callback_configs table. */
export type HttpCallbackConfigRow = {
  id: number;
  config_id: string;
  workflow_id: string;
  name: string;
  url: string;
  secret: string | null;
  enabled: number; // 0 | 1
  notify_on: string; // JSON array string
  timeout_ms: number;
  max_retries: number;
  retry_delay_ms: number;
  include_node_output: number; // 0 | 1
  gmt_create: number;
  gmt_modified: number;
};

/** Insert type for http_callback_configs. */
export type HttpCallbackConfigInsert = {
  configId: string;
  workflowId: string;
  name: string;
  url: string;
  secret?: string;
  enabled?: number;
  notifyOn: string; // JSON array string
  timeoutMs?: number;
  maxRetries?: number;
  retryDelayMs?: number;
  includeNodeOutput?: number;
};


/** Repository interface for http_callback_configs persistence. */
export interface IHttpCallbackConfigRepository {
  /** Get all callback configs for a workflow. */
  findByWorkflowId(workflowId: string): Promise<HttpCallbackConfigRow[]>;
  /** Get a single config by its config_id. */
  findByConfigId(configId: string): Promise<HttpCallbackConfigRow | null>;
  /** Get all distinct workflow IDs that have callback configs in the DB. */
  findAllWorkflowIds(): Promise<string[]>;
  /** Create a new callback config. Returns the inserted row's numeric ID. */
  insert(config: HttpCallbackConfigInsert): Promise<number>;
  /** Update a callback config. Returns true if a row was updated. */
  update(configId: string, config: Partial<HttpCallbackConfigInsert>): Promise<boolean>;
  /** Delete a callback config by config_id. Returns true if a row was deleted. */
  deleteByConfigId(configId: string): Promise<boolean>;
  /** Delete all callback configs for a workflow. Returns deleted count. */
  deleteByWorkflowId(workflowId: string): Promise<number>;
}


/** Result of an HTTP callback dispatch attempt. */
export type HttpCallbackDispatchResult = {
  /** Whether the HTTP request was sent successfully. */
  sent: boolean;
  /** HTTP response code (if received). */
  responseCode: number | null;
  /** Error message (if failed). */
  error: string | null;
};

// ── HTTP Callback Audit Log Types ──────────────────────────────

/** Row shape for http_callback_logs table. */
export type HttpCallbackLogRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  config_id: string;
  config_name: string | null;
  callback_url: string;
  notify_event: string;
  node_id: string | null;
  attempt: number;
  max_attempts: number;
  request_body: string | null;
  request_headers: string | null;
  response_status_code: number | null;
  response_body: string | null;
  duration_ms: number | null;
  status: string;
  error_message: string | null;
  gmt_create: number;
  gmt_modified: number;
};

/** Insert parameters for a new http_callback_logs row. */
export type HttpCallbackLogInsert = {
  flowId: string;
  workflowId: string;
  configId: string;
  configName: string | null;
  callbackUrl: string;
  notifyEvent: string;
  nodeId: string | null;
  attempt: number;
  maxAttempts: number;
  requestBody: string | null;
  requestHeaders: string | null;
  responseStatusCode: number | null;
  responseBody: string | null;
  durationMs: number | null;
  status: string;
  errorMessage: string | null;
};

/** Repository interface for http_callback_logs persistence. */
export interface IHttpCallbackLogRepository {
  insert(log: HttpCallbackLogInsert): Promise<number>;
  findByFlowId(flowId: string, limit?: number): Promise<HttpCallbackLogRow[]>;
  findByWorkflowId(workflowId: string, limit?: number): Promise<HttpCallbackLogRow[]>;
  findByStatus(status: string, limit?: number): Promise<HttpCallbackLogRow[]>;
  deleteOlderThan(timestamp: number): Promise<number>;
}
