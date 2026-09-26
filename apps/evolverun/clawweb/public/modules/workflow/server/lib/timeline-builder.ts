/**
 * Build a unified timeline from run archive raw data.
 * Shared between ClawMind plugin (inspect/bot analysis) and clawweb API.
 */

export type TimelineEventType =
  | "WORKFLOW_START"
  | "WORKFLOW_FINISH"
  | "WORKFLOW_REOPENED"
  | "WORKFLOW_BLOCKED"
  | "WORKFLOW_REPAIRED"
  | "NODE_START"
  | "NODE_END"
  | "NODE_WAITING"
  | "NODE_SKIPPED"
  | "NODE_READY"
  | "LOOP_STARTED"
  | "LOOP_COMPLETED"
  | "LOOP_FAILED"
  | "LOOP_ITERATION_STARTED"
  | "LOOP_ITERATION_COMPLETED"
  | "BUDGET_EXHAUSTED"
  | "ACTION_STARTED"
  | "ACTION_FAILED"
  | "ACTION_SUCCEEDED"
  | "VALIDATION_FAILED"
  | "TOOL_CALL"
  | "TOOL_RESULT"
  | "ASSISTANT_TEXT"
  | "PROGRESS"
  | "ERROR"
  | "LOG"
  | "UNKNOWN";

export type UnifiedTimelineEvent = {
  id: string;
  eventType: TimelineEventType;
  displayType: string;
  timestamp: number | null;
  relativeMs: number | null;
  nodeId: string | null;
  attempt: number | null;
  title: string;
  detail: string | null;
  payload: Record<string, unknown> | null;
  severity: "info" | "warning" | "error" | "success";
  source: "flow_event" | "node_step_trace" | "execution_step_log" | "run_log" | "langfuse" | "synthetic";
  traceId?: string | null;
  observationId?: string | null;
};

export type RunTimelineInput = {
  flowId: string;
  flow?: Record<string, unknown> | null;
  flowEvents?: Array<Record<string, unknown>>;
  stepTraces?: Array<Record<string, unknown>>;
  stepLogs?: Array<Record<string, unknown>>;
  runLogs?: Array<Record<string, unknown>>;
};

export type RunTimeline = {
  ok: boolean;
  flowId: string;
  startedAt: number | null;
  finishedAt: number | null;
  durationMs: number | null;
  events: UnifiedTimelineEvent[];
  summary: {
    total: number;
    errors: number;
    warnings: number;
    toolCalls: number;
    assistantTurns: number;
    nodesStarted: number;
    nodesFinished: number;
    failedNodes: string[];
    skippedNodes: string[];
    flowEventCount: number;
    stepTraceCount: number;
    executionStepLogCount: number;
    runLogCount: number;
  };
};

const FLOW_EVENT_TYPE_MAP: Record<string, TimelineEventType> = {
  workflow_started: "WORKFLOW_START",
  workflow_preflight: "WORKFLOW_START",
  workflow_finished: "WORKFLOW_FINISH",
  workflow_reopened: "WORKFLOW_REOPENED",
  workflow_blocked: "WORKFLOW_BLOCKED",
  workflow_repaired: "WORKFLOW_REPAIRED",
  node_started: "NODE_START",
  node_ready: "NODE_READY",
  node_waiting: "NODE_WAITING",
  node_succeeded: "NODE_END",
  node_failed: "NODE_END",
  node_skipped: "NODE_SKIPPED",
  node_materialized: "NODE_START",
  node_validation_result: "NODE_END",
  node_output_contract_failed: "NODE_END",
  node_manual_retry: "NODE_START",
  loop_started: "LOOP_STARTED",
  loop_completed: "LOOP_COMPLETED",
  loop_failed: "LOOP_FAILED",
  loop_iteration_started: "LOOP_ITERATION_STARTED",
  loop_iteration_completed: "LOOP_ITERATION_COMPLETED",
  budget_exhausted: "BUDGET_EXHAUSTED",
  action_started: "ACTION_STARTED",
  action_failed: "ACTION_FAILED",
  action_succeeded: "ACTION_SUCCEEDED",
  validation_failed: "VALIDATION_FAILED",
  collaboration_result_received: "ACTION_SUCCEEDED",
  collaboration_result_rejected: "ACTION_FAILED",
  flow_imported: "UNKNOWN",
  flow_hidden: "UNKNOWN",
  flow_control_resumed: "WORKFLOW_START",
  flow_control_queued: "WORKFLOW_BLOCKED",
  done: "WORKFLOW_FINISH",
};

function safeText(value: unknown, max = 500): string {
  if (value === null || value === undefined) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function asNumber(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Normalize timestamps to seconds (Unix epoch).
 * The sources use mixed units:
 *   - flow_events.time: seconds
 *   - node_step_traces.gmt_create: seconds
 *   - run_logs.timestamp: often milliseconds
 *   - flow_runs.started_at: mixed
 * Values > 1e12 are treated as milliseconds and converted to seconds.
 */
function normalizeTimestamp(value: unknown): number | null {
  if (value === null || value === undefined) return null;

  // 1. Numeric: prefer obvious milliseconds (> 1e12) or unix seconds.
  if (typeof value === "number" || (typeof value === "string" && /^[+-]?\d+(\.\d+)?$/.test(value))) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return null;
    if (n > 1e12) return Math.floor(n / 1000);
    return Math.floor(n);
  }

  // 2. ISO / locale string: "2026-08-26 11:37:42" or "2026-08-26T11:37:42Z"
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const ms = Date.parse(trimmed.replace(/ /, "T"));
    if (!Number.isNaN(ms) && ms > 0) return Math.floor(ms / 1000);
  }

  return null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function severityFromStatus(status: string): UnifiedTimelineEvent["severity"] {
  const s = String(status).toLowerCase();
  if (s.includes("fail") || s.includes("error") || s.includes("rejected")) return "error";
  if (s.includes("skip")) return "warning";
  if (s.includes("succeed") || s.includes("finish") || s.includes("completed")) return "success";
  return "info";
}

function normalizeFlowEvent(raw: Record<string, unknown>, baseTime: number | null): UnifiedTimelineEvent {
  const eventType = FLOW_EVENT_TYPE_MAP[String(raw.event_type ?? "")] ?? "UNKNOWN";
  const nodeId = asString(raw.node_id);
  const timestamp = normalizeTimestamp(raw.time);
  const attempt = asNumber(raw.attempt);
  const status = String(raw.event_type ?? "").replace("node_", "");
  const title = nodeId ? `${nodeId} ${status}` : String(raw.event_type ?? "event");
  const errorText = safeText(raw.error_text ?? "");
  const dataJson = raw.data_json ? safeText(raw.data_json, 300) : null;

  let severity: UnifiedTimelineEvent["severity"] = "info";
  if (eventType === "NODE_END") severity = severityFromStatus(String(raw.event_type ?? ""));
  if (eventType === "ERROR" || eventType === "BUDGET_EXHAUSTED" || eventType === "VALIDATION_FAILED") severity = "error";
  if (eventType === "NODE_WAITING") severity = "warning";

  return {
    id: `fe-${String(raw.id ?? raw.event_id ?? Math.random())}`,
    eventType,
    displayType: String(raw.event_type ?? "UNKNOWN"),
    timestamp,
    relativeMs: timestamp && baseTime ? (timestamp - baseTime) * 1000 : null,
    nodeId,
    attempt,
    title,
    detail: errorText || dataJson,
    payload: { raw: { ...raw } },
    severity,
    source: "flow_event",
  };
}

function normalizeStepTrace(raw: Record<string, unknown>, baseTime: number | null): UnifiedTimelineEvent {
  const stepType = String(raw.step_type ?? "");
  let eventType: TimelineEventType = "UNKNOWN";
  if (stepType === "tool_call") eventType = "TOOL_CALL";
  else if (stepType === "tool_result") eventType = "TOOL_RESULT";
  else if (stepType === "assistant_text") eventType = "ASSISTANT_TEXT";
  else if (stepType === "progress") eventType = "PROGRESS";

  const nodeId = asString(raw.node_id);
  const toolName = asString(raw.tool_name) ?? "tool";
  const attempt = asNumber(raw.attempt);
  const timestamp = normalizeTimestamp(raw.gmt_create);

  let title = stepType;
  if (eventType === "TOOL_CALL") title = `调用 ${toolName}`;
  if (eventType === "TOOL_RESULT") title = `${toolName} 返回`;
  if (eventType === "ASSISTANT_TEXT") title = "Assistant";
  if (eventType === "PROGRESS") title = "Progress";

  const detail = safeText(raw.tool_output_text ?? raw.text_content ?? raw.tool_input_json ?? "");
  // Payload stores non-duplicate metadata only, keeping detail the primary readable field
  const payload: Record<string, unknown> = {
    step_type: raw.step_type,
    tool_name: raw.tool_name,
    tool_use_id: raw.tool_use_id,
    is_error: raw.is_error,
    attempt: raw.attempt,
    step_seq: raw.step_seq,
    trace_id: raw.trace_id,
    observation_id: raw.observation_id,
    model: raw.model,
    latency_ms: raw.latency_ms,
    prompt_tokens: raw.prompt_tokens,
    completion_tokens: raw.completion_tokens,
  };
  return {
    id: `nst-${String(raw.id ?? `${raw.node_id}-${raw.step_seq}`)}`,
    eventType,
    displayType: stepType,
    timestamp,
    relativeMs: timestamp && baseTime ? (timestamp - baseTime) * 1000 : null,
    nodeId,
    attempt,
    title,
    detail,
    payload,
    severity: raw.is_error ? "error" : "info",
    source: "node_step_trace",
    traceId: asString(raw.trace_id),
    observationId: asString(raw.observation_id),
  };
}

function normalizeExecutionStepLog(raw: Record<string, unknown>, baseTime: number | null): UnifiedTimelineEvent {
  const nodeId = asString(raw.node_id);
  const timestamp = normalizeTimestamp(raw.timestamp);
  const title = String(raw.step_type ?? "step");
  const detail = safeText(raw.output_summary ?? raw.input_summary ?? raw.llm_evaluation ?? "");
  const payload = {
    step_type: raw.step_type,
    node_id: raw.node_id,
    error_flag: raw.error_flag,
    latency_ms: raw.latency_ms,
  };
  return {
    id: `esl-${String(raw.id ?? Math.random())}`,
    eventType: "UNKNOWN",
    displayType: title,
    timestamp,
    relativeMs: timestamp && baseTime ? (timestamp - baseTime) * 1000 : null,
    nodeId,
    attempt: null,
    title,
    detail,
    payload,
    severity: raw.error_flag ? "error" : "info",
    source: "execution_step_log",
  };
}

function normalizeRunLog(raw: Record<string, unknown>, baseTime: number | null): UnifiedTimelineEvent {
  const level = String(raw.level ?? "info").toLowerCase();
  const nodeId = asString(raw.node_id);
  const timestamp = normalizeTimestamp(raw.timestamp);
  const title = `[${raw.source ?? "-"}] ${level}`;
  const source = String(raw.source ?? "");
  const payload = {
    level,
    source,
    logger: raw.logger,
  };
  return {
    id: `rl-${String(raw.id ?? raw.seq ?? Math.random())}`,
    eventType: "LOG",
    displayType: level,
    timestamp,
    relativeMs: timestamp && baseTime ? (timestamp - baseTime) * 1000 : null,
    nodeId,
    attempt: null,
    title,
    detail: safeText(raw.message ?? ""),
    payload,
    severity: level === "error" ? "error" : level === "warn" || level === "warning" ? "warning" : "info",
    source: "run_log",
  };
}

export function buildRunTimeline(input: RunTimelineInput): RunTimeline {
  const events: UnifiedTimelineEvent[] = [];

  // Determine start time from flow record or first flow event
  let startedAt: number | null = normalizeTimestamp(input.flow?.started_at ?? input.flow?.gmt_create ?? null);
  if (!startedAt) {
    const firstFe = (input.flowEvents ?? []).find((e) => String(e.event_type ?? "").includes("started"));
    startedAt = firstFe ? normalizeTimestamp(firstFe.time) : null;
  }

  for (const e of input.flowEvents ?? []) {
    events.push(normalizeFlowEvent(e, startedAt));
  }
  for (const t of input.stepTraces ?? []) {
    events.push(normalizeStepTrace(t, startedAt));
  }
  for (const l of input.stepLogs ?? []) {
    events.push(normalizeExecutionStepLog(l, startedAt));
  }
  for (const l of input.runLogs ?? []) {
    events.push(normalizeRunLog(l, startedAt));
  }

  events.sort((a, b) => {
    const ta = a.timestamp ?? 0;
    const tb = b.timestamp ?? 0;
    if (ta !== tb) return ta - tb;
    // stable: flow_event before step_trace before run_log
    const order = { flow_event: 0, execution_step_log: 1, node_step_trace: 2, run_log: 3, langfuse: 4, synthetic: 5 };
    return (order[a.source] ?? 99) - (order[b.source] ?? 99);
  });

  const finishedAt = events.length > 0 ? events[events.length - 1].timestamp : null;
  const durationMs = startedAt && finishedAt ? (finishedAt - startedAt) * 1000 : null;

  const failedNodes = new Set<string>();
  const skippedNodes = new Set<string>();
  let toolCalls = 0;
  let assistantTurns = 0;
  let nodesStarted = 0;
  let nodesFinished = 0;
  let errors = 0;
  let flowEventCount = 0;
  let stepTraceCount = 0;
  let executionStepLogCount = 0;
  let runLogCount = 0;

  for (const e of events) {
    if (e.source === "flow_event") flowEventCount++;
    if (e.source === "node_step_trace") stepTraceCount++;
    if (e.source === "execution_step_log") executionStepLogCount++;
    if (e.source === "run_log") runLogCount++;
    if (e.eventType === "TOOL_CALL") toolCalls++;
    if (e.eventType === "ASSISTANT_TEXT") assistantTurns++;
    if (e.eventType === "NODE_START") nodesStarted++;
    if (e.eventType === "NODE_END") {
      nodesFinished++;
      if (e.severity === "error" && e.nodeId) failedNodes.add(e.nodeId);
    }
    if (e.eventType === "NODE_SKIPPED" && e.nodeId) skippedNodes.add(e.nodeId);
    if (e.severity === "error") errors++;
  }

  return {
    ok: true,
    flowId: input.flowId,
    startedAt,
    finishedAt,
    durationMs,
    events,
    summary: {
      total: events.length,
      errors,
      warnings: events.filter((e) => e.severity === "warning").length,
      toolCalls,
      assistantTurns,
      nodesStarted,
      nodesFinished,
      failedNodes: [...failedNodes],
      skippedNodes: [...skippedNodes],
      flowEventCount,
      stepTraceCount,
      executionStepLogCount,
      runLogCount,
    },
  };
}
