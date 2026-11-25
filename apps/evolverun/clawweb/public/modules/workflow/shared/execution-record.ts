/**
 * GET /api/runs/:flowId/execution-record — current persisted execution snapshot.
 * This is not historical callback replay: status is the stored run.status verbatim
 * (including running/waiting/blocked), not a mapped callback event name.
 * Fields follow the HTTP callback buildExtInfo allowlist; JSON text stays unparsed.
 * All node attempts are returned, ordered by started_at DESC, id DESC, with no cap.
 */
export interface ExecutionRecord {
  workflow_id: string;
  flow_id: string;
  status: string;
  ext_info: {
    /** A missing run is HTTP 404, never a successful null snapshot. */
    flow_runs: ExecutionRecordFlowRun;
    node_executions: ExecutionRecordNodeExecution[];
  };
}

export interface ExecutionRecordFlowRun {
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
  /** Always null; the stored credentials column is never selected for this API. */
  credentials_json: null;
  origin_session_key: string | null;
  origin_session_id: string | null;
  origin_bot_id: string | null;
  user_id: string | null;
  plugin_version: string | null;
  gmt_create: number;
  gmt_modified: number | null;
}

export interface ExecutionRecordNodeExecution {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  executor_type: string | null;
  status: string;
  attempt: number;
  input_json: string | null;
  /** Omitted by default/false; true returns stored string/null without truncation. */
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
}
