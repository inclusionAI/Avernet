import type { AgentToolCall } from "../../../repositories/repair/agent-tool-call-repository.js";
import type { RecoveryEvidence } from "./task-contracts.js";

export type TrajectoryIndexRead = {
  callId: string;
  executionId: string;
  afterWriteCallId: string;
  authorizationScopeDigest: string;
  request: Record<string, unknown>;
  result: Record<string, unknown> & { recorded_at_ms: number };
};

/** Retain references from the audit already loaded by hydration, without comparing bodies. */
export function trajectoryIndexReads(calls: readonly AgentToolCall[]): TrajectoryIndexRead[] {
  let afterWriteCallId = "";
  return calls.flatMap(call => {
    if (call.isWrite) afterWriteCallId = call.callId;
    const request = call.request as Record<string, unknown> | null;
    const result = call.result as TrajectoryIndexRead["result"] | null;
    if (call.toolName !== "trajectory_read" || call.isWrite || call.status !== "succeeded"
      || !request || !result || result.read_scope !== "traces" || !Array.isArray(result.traces)
      || request.trace_id !== undefined || request.field !== undefined || request.observation_id !== undefined
      || !Number.isSafeInteger(result.recorded_at_ms)) return [];
    return [{ callId: call.callId, executionId: call.executionId, afterWriteCallId,
      authorizationScopeDigest: call.authorizationScopeDigest, request, result }];
  });
}

/** Only the model-facing projection changes; all evidence IDs and audit receipts remain intact. */
export function bootstrapEvidence(evidence: readonly RecoveryEvidence[], reads: readonly TrajectoryIndexRead[] = []) {
  const byCall = new Map(reads.map(read => [read.callId, read]));
  const firstByContent = new Map<string, string>();
  return evidence.map(item => {
    const projected = { ...item, excerpt: item.excerpt.slice(0, 1500) };
    const read = item.source === "trajectory_read" ? byCall.get(item.tool_call_id) : undefined;
    if (!read) return projected;
    // Existing AIS closeout already reduces complete JSON receipts to a few facts.
    // Replacing those with text would enlarge closeout or hide those facts.
    try { JSON.parse(projected.excerpt); return projected; } catch { /* Truncated excerpt. */ }
    // requestDigest includes clientRequestId, so it also changes on identical polls.
    // All query parameters, coverage,
    // source, trace/event timestamps and fields beyond the displayed excerpt participate.
    const { clientRequestId: _requestId, requestDigest: _requestDigest, ...request } = read.request;
    const { recorded_at_ms, ...result } = read.result;
    const key = JSON.stringify([read.executionId, read.afterWriteCallId, read.authorizationScopeDigest, request, result]);
    const previous = firstByContent.get(key);
    if (!previous) firstByContent.set(key, item.evidence_id);
    // Plain text also survives existing AIS closeout's JSON receipt-field allowlist.
    const reference = previous ? `same_content_as=${previous}; recorded_at_ms=${recorded_at_ms}; full receipt: tool_call_get(toolCallId=${item.tool_call_id}).` : undefined;
    return { ...projected, recorded_at_ms,
      ...(reference && reference.length <= 400 && reference.length < projected.excerpt.length ? { excerpt: reference } : {}) };
  });
}
