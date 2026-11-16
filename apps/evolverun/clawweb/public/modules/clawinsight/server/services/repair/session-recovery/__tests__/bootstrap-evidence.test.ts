import { describe, expect, it } from "vitest";
import type { AgentToolCall } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { bootstrapEvidence, trajectoryIndexReads } from "../bootstrap-evidence.js";

function call(index: number, patch: Partial<AgentToolCall> = {}): AgentToolCall {
  return { id: index, callId: `call-${index}`, taskId: "task", stepId: "step", executionId: "execution",
    authorizationScopeDigest: "scope", clientRequestId: `request-${index}`, toolName: "trajectory_read",
    operation: "trajectory_read", isWrite: false, status: "succeeded",
    request: { clientRequestId: `request-${index}`, requestDigest: `digest-${index}`, purpose: "observe", offset: 0 },
    result: { source: "trajectory", coverage: "uploaded_records_only", read_scope: "traces",
      traces: [{ traceId: "trace-1", name: "original task".repeat(200), startTimeMs: 1000,
        event_summary: { available: true, count: 2, latest: { observationId: "event-1", status: "running" } } }],
      offset: 0, next_offset: null, recorded_at_ms: 1000 + index * 60000 }, ...patch } as AgentToolCall;
}
function evidence(calls: AgentToolCall[]) {
  return calls.map(call => ({ evidence_id: `evidence-${call.callId}`, tool_call_id: call.callId,
    source: call.toolName, location: `tool-call:${call.callId}`, excerpt: JSON.stringify(call.result).slice(0, 2000) }));
}
function project(calls: AgentToolCall[]) { return bootstrapEvidence(evidence(calls), trajectoryIndexReads(calls)); }

it("expands repeated index content once while retaining every identity, read time and original receipt", () => {
  const calls = Array.from({ length: 60 }, (_, index) => call(index));
  const original = structuredClone(calls), saved = evidence(calls), before = structuredClone(saved);
  const projected = bootstrapEvidence(saved, trajectoryIndexReads(calls));
  expect(projected).toHaveLength(60);
  expect(projected.map(({ evidence_id, tool_call_id, source, location }) => ({ evidence_id, tool_call_id, source, location })))
    .toEqual(saved.map(({ excerpt: _, ...identity }) => identity));
  expect(projected[0].excerpt).toBe(saved[0].excerpt.slice(0, 1500));
  expect(projected).toEqual(expect.arrayContaining(calls.map(call => expect.objectContaining({
    tool_call_id: call.callId, recorded_at_ms: (call.result as any).recorded_at_ms,
  }))));
  for (let i = 1; i < 60; i++) {
    expect(projected[i].excerpt).toContain(`same_content_as=${saved[0].evidence_id};`);
    expect(projected[i].excerpt).toContain(`recorded_at_ms=${(calls[i].result as any).recorded_at_ms};`);
    expect(projected[i].excerpt).toContain(`tool_call_get(toolCallId=${calls[i].callId})`);
  }
  expect(JSON.stringify(projected).length).toBeLessThan(JSON.stringify(bootstrapEvidence(saved)).length / 3);
  expect(saved).toEqual(before); expect(calls).toEqual(original);
});

describe("different evidence is never replaced by a reference", () => {
  const differences = [
    { coverage: "partial" }, { next_offset: 20 }, { offset: 20 }, { dataSource: "fallback" },
    { delivery_confirmed: true }, { unavailable_reason: "upstream_unavailable" },
    { observed_at_ms: 123 }, { read_at_ms: 456 }, { additional_future_field: "new evidence" },
    { traces: [{ ...(call(1).result as any).traces[0], startTimeMs: 2000 }] },
    { traces: [{ ...(call(1).result as any).traces[0], event_summary: { available: true, count: 3 } }] },
  ];
  it.each(differences)("compares complete results, including changes beyond the displayed prefix: %j", difference => {
    const first = call(1), second = call(2, { result: { ...(call(2).result as object), ...difference } });
    expect(project([first, second])[1].excerpt).toBe(evidence([second])[0].excerpt.slice(0, 1500));
  });
  it.each([{ offset: 20 }, { limit: 1 }, { scope: "different" }, { purpose: "recheck a different condition" }])(
    "retains different request scopes even when result pages are equal: %j", difference => {
      const first = call(1), second = call(2, { request: { ...(call(2).request as object), ...difference } });
      expect(project([first, second])[1].excerpt).not.toContain("same_content_as");
    });
  it.each([{ authorizationScopeDigest: "changed-target" }, { executionId: "next-ais" }])(
    "does not equate reads from different execution contexts: %j", patch => {
      expect(project([call(1), call(2, patch)])[1].excerpt).not.toContain("same_content_as");
    });
  it("does not merge across a write that can change the effective delivery boundary", () => {
    const calls = [call(1), call(2, { toolName: "retry_message", isWrite: true }), call(3)];
    expect(project(calls).every(item => !item.excerpt.includes("same_content_as"))).toBe(true);
  });
});

it.each([
  { toolName: "retry_message", isWrite: true }, { toolName: "apply_action", isWrite: true },
  { toolName: "reconcile_write" }, { toolName: "session_read" }, { status: "failed" },
  { status: "unknown" }, { status: "canceled" }, { status: "executing" },
  { request: { trace_id: "trace-1" } }, { request: { field: "input" } },
  { request: { observation_id: "event-1" } }, { result: { read_scope: "content", content: "body" } },
  { result: { read_scope: "observations", observations: [] } },
  { result: { read_scope: "traces", traces: [] } },
] as Partial<AgentToolCall>[])("leaves non-index, unsuccessful and incomplete legacy receipts untouched: %j", patch => {
  const calls = [call(1, patch), call(2, patch)];
  expect(project(calls)).toEqual(bootstrapEvidence(evidence(calls)));
});

it("keeps legacy evidence unchanged when no complete audit is available", () => {
  const saved = evidence([call(1), call(2)]);
  expect(bootstrapEvidence(saved)).toEqual(saved.map(item => ({ ...item, excerpt: item.excerpt.slice(0, 1500) })));
});

it.each([{}, { delivery_confirmed: true, delivery_key: "current-delivery" }])(
  "preserves complete short JSON for the existing AIS closeout fact filter: %j", facts => {
    const calls = Array.from({ length: 500 }, (_, index) => call(index, {
      result: { source: "trajectory", read_scope: "traces", coverage: "uploaded_records_only", traces: [],
        offset: 0, next_offset: null, recorded_at_ms: 1000 + index * 60000, ...facts },
    }));
    expect(project(calls)).toEqual(bootstrapEvidence(evidence(calls)));
    expect(project(calls).every(item => JSON.parse(item.excerpt).recorded_at_ms > 0)).toBe(true);
  });

it("keeps plain-text references within the existing 400-character closeout budget", () => {
  const calls = [call(1), call(2)];
  const saved = evidence(calls);
  saved[0].evidence_id = "evidence-" + "x".repeat(400);
  expect(bootstrapEvidence(saved, trajectoryIndexReads(calls))[1].excerpt).toBe(saved[1].excerpt.slice(0, 1500));
});

it("never references an audit item absent from the supplied evidence", () => {
  const calls = [call(1), call(2), call(3)];
  const projected = bootstrapEvidence(evidence(calls).slice(1), trajectoryIndexReads(calls));
  expect(projected[0].excerpt).not.toContain("same_content_as");
  expect(projected[1].excerpt).toContain(`same_content_as=${projected[0].evidence_id};`);
});
