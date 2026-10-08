/**
 * Controller hook types for state persistence lifecycle events.
 *
 * Hooks are called at key points during node execution to record
 * metrics, alerts, and execution tracking data to the database.
 * All DB writes are best-effort (logged on failure, never throw).
 */

import type { ExecutorResult } from "../types.js";

/** Event types emitted during node execution. */
export type NodeLifecycleEvent =
  | "node_started"
  | "node_succeeded"
  | "node_failed"
  | "node_rejected"
  | "node_retry"
  | "node_progress"
  | "node_duration_ms"
  | "node_token_usage_total"
  | "node_skipped";

/** Payload for a node lifecycle event. */
export type NodeLifecyclePayload = {
  flowId: string;
  workflowId: string;
  nodeId: string;
  executorType: string;
  attempt: number;
  nodeTitle?: string;
  progressMessage?: string;
  durationMs?: number;
  usage?: ExecutorResult["usage"];
  error?: string | null;
  inputJson?: string | null;
  outputJson?: string | null;
  sessionKey?: string;
  sessionId?: string;
  /** Derived embedded session key for embedded-agent nodes — used for Langfuse trace correlation. */
  embeddedSessionKey?: string;
  /** Session file path from embedded-agent execution — used by persist step trace in Controller. */
  sessionFile?: string;
  /** Skill name from embedded-agent execution — used to persist step trace in Controller. */
  skillName?: string | null;
  /** Structured system context for debugging and analysis (trigger rules, hook outcomes, retry context, executor details). */
  systemContext?: Record<string, unknown> | null;
  /** Template-resolved prompt text for embedded-agent/subagent nodes — used for persistence and notifications. */
  resolvedPrompt?: string;
};

/** Interface for receiving node lifecycle events. */
export type NodeLifecycleHook = {
  /** Called when a node lifecycle event occurs. */
  onEvent(event: NodeLifecycleEvent, payload: NodeLifecyclePayload): void;
};