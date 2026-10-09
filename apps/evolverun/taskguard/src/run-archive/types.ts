/**
 * Type definitions for the run-archive module.
 */
import type { RunLogRow, RunLogInsert } from "../db/repositories/types.js";

// Re-export for convenience
export type { RunLogRow, RunLogInsert };

// ── Langfuse row types (from aw_langfuse_traces / aw_langfuse_observation) ──

export type LangfuseTraceRow = {
  trace_id: string;
  name: string | null;
  session_id: string | null;
  real_session_id: string | null;
  gmt_trace: number | null;
  input: string | null;
  output: string | null;
  metadata: string | null;
  latency: number | null;
  total_cost: number | null;
  user_id: string | null;
};

export type LangfuseObservationRow = {
  observation_id: string;
  trace_id: string;
  parent_observation_id: string | null;
  type: string | null;
  name: string | null;
  start_time: number | null;
  end_time: number | null;
  input: string | null;
  output: string | null;
  model: string | null;
  status_message: string | null;
  usage_input_tokens: number | null;
  usage_output_tokens: number | null;
  usage_total_tokens: number | null;
  latency: number | null;
};

// ── Failure summary ──

export type FailedNodeInfo = {
  nodeId: string;
  nodeTitle: string | null;
  executorType: string | null;
  error: string | null;
  attempt: number;
  embeddedSessionKey: string | null;
  relatedErrorLogs: RunLogRow[];
};

export type ErrorTimelineEntry = {
  timestamp: string;
  event: string;
  detail: string;
};

export type FailureSummary = {
  failedNodeCount: number;
  failedNodes: FailedNodeInfo[];
  rootCauseHints: string[];
  errorTimeline: ErrorTimelineEntry[];
};

// ── Run archive ──

export type RunArchive = {
  archive: {
    flowId: string;
    archiveId: string;
    archiveVersion: string;
    createdAt: string;
    status: "completed" | "partial";
    errors: string[];
  };
  flowRun: Record<string, unknown> | null;
  nodeExecutions: Record<string, unknown>[];
  flowEvents: Record<string, unknown>[];
  nodeStepTraces: Record<string, unknown>[];
  executionStepLogs: Record<string, unknown>[];
  runLogs: RunLogRow[];
  langfuseTraces: LangfuseTraceRow[];
  langfuseObservations: LangfuseObservationRow[];
  failureSummary: FailureSummary;
};
