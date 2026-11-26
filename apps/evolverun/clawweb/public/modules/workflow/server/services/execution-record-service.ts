import type {
  ExecutionRecord,
  ExecutionRecordFlowRun,
  ExecutionRecordNodeExecution,
} from "../../shared/execution-record.js";
import type { ExecutionRecordSnapshot } from "../repositories/flow-run-repository.js";
import { readFanoutMap } from "./fanout-state-reader.js";
import type { NodeExecutionRepository } from "../repositories/node-execution-repository.js";

/** Structural payload guard only: never consult actor-dependent workflow/run view permissions. */
export function isFanoutExecutionRecord(run: Pick<ExecutionRecordSnapshot, "flow_id" | "state_json">): boolean {
  // Validate root/map shape and embedded flow identity using the same persisted selection.
  const map = readFanoutMap(run);
  // A nonempty map is denied even if its group entries are malformed; do not read child payloads.
  if (Object.keys(map).length > 0) return true;
  const state: unknown = run.state_json ? JSON.parse(run.state_json) : null;
  return state !== null && typeof state === "object" && Object.hasOwn(state, "fanoutOrigin");
}

/** Read nodes only after the caller has checked identity and structural payload guards. Errors propagate. */
export async function readExecutionRecord(
  run: ExecutionRecordFlowRun,
  nodeExecRepo: Pick<NodeExecutionRepository, "findExecutionRecordsByFlowIdStrict">,
  includeNodeOutput = false,
): Promise<ExecutionRecord> {
  const rows = await nodeExecRepo.findExecutionRecordsByFlowIdStrict(run.flow_id, includeNodeOutput);
  // Explicit projections prevent future repository columns from expanding this contract.
  const flowRun: ExecutionRecordFlowRun = {
    id: run.id,
    flow_id: run.flow_id,
    workflow_id: run.workflow_id,
    workflow_title: run.workflow_title,
    status: run.status,
    params_json: run.params_json,
    input_json: run.input_json,
    result_json: run.result_json,
    node_count: run.node_count,
    succeeded_count: run.succeeded_count,
    failed_count: run.failed_count,
    total_duration_ms: run.total_duration_ms,
    total_token_usage: run.total_token_usage,
    triggered_by: run.triggered_by,
    identity_key: run.identity_key,
    current_phase: run.current_phase,
    started_at: run.started_at,
    completed_at: run.completed_at,
    credentials_json: null,
    origin_session_key: run.origin_session_key,
    origin_session_id: run.origin_session_id,
    origin_bot_id: run.origin_bot_id,
    user_id: run.user_id,
    plugin_version: run.plugin_version,
    gmt_create: run.gmt_create,
    gmt_modified: run.gmt_modified,
  };
  const nodes = rows.map((row): ExecutionRecordNodeExecution => ({
    id: row.id,
    flow_id: row.flow_id,
    workflow_id: row.workflow_id,
    node_id: row.node_id,
    executor_type: row.executor_type,
    status: row.status,
    attempt: row.attempt,
    input_json: row.input_json,
    error_text: row.error_text,
    duration_ms: row.duration_ms,
    token_usage_json: row.token_usage_json,
    node_title: row.node_title,
    triggered_by: row.triggered_by,
    branch_id: row.branch_id,
    progress_message: row.progress_message,
    session_key: row.session_key,
    session_id: row.session_id,
    system_context_json: row.system_context_json,
    embedded_session_key: row.embedded_session_key,
    started_at: row.started_at,
    completed_at: row.completed_at,
    gmt_create: row.gmt_create,
    gmt_modified: row.gmt_modified,
    ...(includeNodeOutput ? { output_json: row.output_json } : {}),
  }));
  return {
    workflow_id: run.workflow_id,
    flow_id: run.flow_id,
    status: run.status,
    ext_info: { flow_runs: flowRun, node_executions: nodes },
  };
}
