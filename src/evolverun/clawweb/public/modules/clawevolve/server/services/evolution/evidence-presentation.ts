import type { WorkflowRunEvidenceRow } from "../../repositories/workflow-evolution-repository.js";

export type EvolutionEvidenceSummary = {
  eventId: string;
  flowId: string;
  nodeId: string | null;
  eventType: string;
  producer: string;
  occurredAtMs: number;
  summary: string;
  missing?: boolean;
};

const SUMMARY_KEYS = ["message", "error", "reason", "status", "expected", "actual", "command", "phase"] as const;

function safeObject(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function safeText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value); } catch { return "[unavailable]"; }
}

export function summarizeEvidenceRow(row: WorkflowRunEvidenceRow): EvolutionEvidenceSummary {
  let payload: Record<string, unknown> = {};
  try { payload = safeObject(JSON.parse(row.payload_json)); } catch { /* use event type fallback */ }
  const summary = SUMMARY_KEYS
    .filter((key) => payload[key] != null)
    .map((key) => `${key}: ${safeText(payload[key])}`)
    .join(" · ")
    .slice(0, 500);
  return {
    eventId: row.event_id,
    flowId: row.flow_id,
    nodeId: row.node_id,
    eventType: row.event_type,
    producer: row.producer,
    occurredAtMs: row.occurred_at_ms,
    summary: summary || row.event_type,
  };
}

export function presentEvidence(
  eventIds: string[],
  rowsById: ReadonlyMap<string, WorkflowRunEvidenceRow>,
): EvolutionEvidenceSummary[] {
  return eventIds.map((eventId) => {
    const row = rowsById.get(eventId);
    return row
      ? summarizeEvidenceRow(row)
      : {
        eventId,
        flowId: "",
        nodeId: null,
        eventType: "missing",
        producer: "unknown",
        occurredAtMs: 0,
        summary: "引用证据不可用",
        missing: true,
      };
  });
}
