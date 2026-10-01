import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, boundedRepairJson, canonicalRepairJson, digestRepairJson, MAX_FROZEN_BYTES,
  validateRepairItem, type RepairItem, type RepairItemState } from '../contracts/repair-batch.js';
import type { RepairSourcePort } from '../contracts/repair-workbench.js';

type RecordValue = Record<string, unknown>;
type DiagnosisSource = {
  diagnosisId: string; flowId: string; analysisId: string; failureSignature: string;
  failureMode: string; nodeId: string | null; reasoning: string; evidenceEventIds: string[];
  proposal?: RecordValue; sourceId: string; completedAtMs: number;
};
type GroupSource = { workflowId: string; signature: string; inputDigest: string; sources: DiagnosisSource[] };
export type RepairSuggestionSource = {
  id: string | number; workflow_id: string; failure_signature: string; status: string;
  fix_spec: string | null; proposal_json?: string | null; node_id?: string | null;
  source_diagnosis_ids?: string | null; impact_run_ids?: string | null;
  gmt_modified?: string | number | null;
};
export type RepairEvidenceSource = {
  event_id: string; workflow_id: string; flow_id: string; event_type: string; payload_json: string;
  node_id?: string | null; payload_digest?: string; occurred_at_ms?: number;
};
/** Composition roots adapt the legacy analysis/suggestion read APIs. The Workflow module
 * consumes these reads without constructing or extending the old evolution control plane. */
export interface RepairSourceReaders {
  groups(db: IDatabase, workflowId: string, options?: { sinceMs?: number }): Promise<GroupSource[]>;
  suggestions(db: IDatabase, workflowId: string): Promise<RepairSuggestionSource[]>;
  evidence(db: IDatabase, workflowId: string, eventIds: string[]): Promise<RepairEvidenceSource[]>;
}
export const REPAIR_ACTIVE_LOOKBACK_DAYS = 30;
const ACTIVE_LOOKBACK_MS = REPAIR_ACTIVE_LOOKBACK_DAYS * 24 * 60 * 60 * 1000;
type SourceItem = Awaited<ReturnType<RepairSourcePort['load']>>[number];
function parsed(raw: string, label: string): unknown {
  try { return JSON.parse(raw); } catch { throw new RepairBatchError('INVALID_INPUT', `Invalid stored ${label}`); }
}
function ids(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const value = parsed(raw, 'source ids');
  if (!Array.isArray(value) || value.some(id => typeof id !== 'string')) throw new RepairBatchError('INVALID_INPUT', 'Invalid stored source ids');
  return [...new Set(value)].sort();
}
function proposal(value: unknown, workflowId: string): RecordValue | null {
  if (value == null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) throw new RepairBatchError('INVALID_INPUT', 'Invalid stored proposal');
  const record = value as RecordValue;
  if (record.workflowId != null && record.workflowId !== workflowId) throw new RepairBatchError('CONTENT_MISMATCH', 'Proposal belongs to another workflow');
  return record;
}
export function structuredRepairProposalIdentity(value: Record<string, unknown> | null): unknown | null {
  if (value && Array.isArray(value.operations) && value.operations.length) return {
    schemaVersion: value.schemaVersion ?? null, workflowId: value.workflowId ?? null, operations: value.operations,
  };
  return null;
}
function proposalIdentity(value: RecordValue | null, instruction: string, textSourceId?: string): unknown {
  const structured = structuredRepairProposalIdentity(value);
  if (structured) return structured;
  return value ? { proposal: value, instruction } : { instruction, textSourceId };
}
function legacyState(status: string): RepairItemState {
  if (['applying', 'running', 'dispatching', 'dispatched'].includes(status)) return 'processing';
  if (['applied', 'applied_unverified'].includes(status)) return 'awaiting_verification';
  if (status === 'verified') return 'verified';
  if (status === 'ineffective') return 'ineffective';
  if (['pending', 'adopted', 'failed'].includes(status)) return 'pending';
  // Archived and unknown legacy states remain read-only; do not silently re-open them.
  return 'no_action';
}
function timestampMs(value: string | number | null | undefined): number | null {
  if (value == null || value === '') return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : null;
}
function lifecycleVisible(status: string): boolean {
  return ['applying', 'running', 'dispatching', 'dispatched', 'applied', 'applied_unverified'].includes(status);
}
const legacyPriority: Partial<Record<RepairItemState, number>> = {
  pending: 0, no_action: 1, verified: 2, ineffective: 3, awaiting_verification: 4, processing: 5,
};

export function createRepairSourcePort(readers: RepairSourceReaders): RepairSourcePort {
  return { async load(db, workflowId, mode = 'full', scope) {
    const sinceMs = scope?.includeHistorical ? undefined : Date.now() - ACTIVE_LOOKBACK_MS;
    const groups = await readers.groups(db, workflowId, { sinceMs });
    const suggestions = (await readers.suggestions(db, workflowId)).filter(row => {
      if (scope?.includeHistorical || lifecycleVisible(row.status)) return true;
      const modifiedAt = timestampMs(row.gmt_modified);
      return modifiedAt === null || modifiedAt >= sinceMs!;
    });
    if (groups.some(group => group.workflowId !== workflowId) || suggestions.some(row => row.workflow_id !== workflowId)) {
      throw new RepairBatchError('CONTENT_MISMATCH', 'Source belongs to another workflow');
    }
    const diagnosisContext = (source: DiagnosisSource): RecordValue => ({
      analysisId: source.analysisId, diagnosisId: source.diagnosisId, flowId: source.flowId,
      nodeId: source.nodeId, failureMode: source.failureMode, reasoning: source.reasoning, completedAtMs: source.completedAtMs,
      evidenceEventIds: source.evidenceEventIds, evidenceCount: source.evidenceEventIds.length,
    });
    const items = new Map<string, SourceItem>();
    const get = (signature: string, value: RecordValue | null, instruction: string, textSourceId?: string): SourceItem => {
      const groupKey = digestRepairJson([workflowId, signature]);
      const proposalKey = digestRepairJson(proposalIdentity(value, instruction, textSourceId));
      const itemId = digestRepairJson([workflowId, groupKey, proposalKey, 'initial']);
      let row = items.get(itemId);
      if (!row) {
        row = { episodeKey: 'initial', initialState: 'pending', item: { itemId, groupKey, proposalKey,
          contentRevision: 1, previousItemId: null, proposal: value, instruction, sources: [],
          context: { signature, diagnoses: [], suggestions: [] } } };
        items.set(itemId, row);
      }
      return row;
    };
    for (const group of groups) for (const source of [...group.sources]
      .sort((a, b) => b.completedAtMs - a.completedAtMs || a.sourceId.localeCompare(b.sourceId))) {
      const value = proposal(source.proposal, workflowId);
      // Diagnoses without actionable suggestions remain in the existing diagnostic view.
      if (!value || typeof value.summary !== 'string' || !value.summary.trim()) continue;
      const row = get(group.signature, value, value.summary.trim());
      row.item.sources.push({ kind: 'diagnosis_candidate', signature: group.signature, inputDigest: group.inputDigest,
        candidateId: digestRepairJson(value), analysisId: source.analysisId, diagnosisId: source.diagnosisId, flowId: source.flowId });
      (row.item.context!.diagnoses as RecordValue[]).push(diagnosisContext(source));
    }
    for (const suggestion of [...suggestions].sort((a, b) => (timestampMs(b.gmt_modified) ?? 0) - (timestampMs(a.gmt_modified) ?? 0)
      || String(a.id).localeCompare(String(b.id)))) {
      const value = proposal(suggestion.proposal_json ? parsed(suggestion.proposal_json, 'suggestion proposal') : null, workflowId);
      const instruction = suggestion.fix_spec?.trim() || (typeof value?.summary === 'string' ? value.summary.trim() : '');
      if (!value && !instruction) continue;
      const row = get(suggestion.failure_signature, value, instruction, String(suggestion.id));
      row.item.sources.push({ kind: 'suggestion', suggestionId: String(suggestion.id),
        proposalDigest: value ? digestRepairJson(value) : null, instructionDigest: digestRepairJson(instruction) });
      const diagnosisIds = ids(suggestion.source_diagnosis_ids);
      const runIds = ids(suggestion.impact_run_ids);
      const related = groups.filter(group => group.signature === suggestion.failure_signature).flatMap(group => group.sources)
        .filter(source => diagnosisIds.length ? diagnosisIds.includes(source.diagnosisId) : runIds.includes(source.flowId));
      (row.item.context!.diagnoses as RecordValue[]).push(...related.map(diagnosisContext));
      (row.item.context!.suggestions as RecordValue[]).push({ suggestionId: String(suggestion.id), status: suggestion.status,
        diagnosisIds, runIds, nodeId: suggestion.node_id ?? null,
        missingDiagnosisIds: diagnosisIds.filter(id => !related.some(source => source.diagnosisId === id)) });
      // Legacy work in progress is visible but is not silently re-enqueued as a new pending item.
      const state = legacyState(suggestion.status);
      if ((legacyPriority[state] ?? 0) > (legacyPriority[row.initialState ?? 'pending'] ?? 0)) row.initialState = state;
    }
    if (mode === 'full') {
      const selectedIds = scope?.itemIds ? new Set(scope.itemIds) : null;
      const selected = [...items.values()].filter(row => !selectedIds || selectedIds.has(row.item.itemId));
      const eventIds = [...new Set(selected.flatMap(row => (row.item.context!.diagnoses as RecordValue[])
        .flatMap(diagnosis => diagnosis.evidenceEventIds as string[])))].sort();
      const evidence = new Map<string, RepairEvidenceSource>();
      // Avoid database parameter limits; never silently discard citations after the requested item set.
      for (let offset = 0; offset < eventIds.length; offset += 200) {
        for (const row of await readers.evidence(db, workflowId, eventIds.slice(offset, offset + 200))) {
          if (row.workflow_id === workflowId) evidence.set(row.event_id, row);
        }
      }
      for (const row of selected) {
        row.item.context!.diagnoses = (row.item.context!.diagnoses as RecordValue[]).map(diagnosis => {
          const { evidenceEventIds, evidenceCount: _evidenceCount, ...summary } = diagnosis;
          return { ...summary, evidence: (evidenceEventIds as string[]).map(eventId => {
            const entry = evidence.get(eventId);
            if (!entry || entry.flow_id !== diagnosis.flowId) return { eventId, missing: true };
            return { eventId, flowId: entry.flow_id, nodeId: entry.node_id ?? null, eventType: entry.event_type,
              payloadDigest: entry.payload_digest ?? null, occurredAtMs: entry.occurred_at_ms ?? null,
              payload: parsed(entry.payload_json, 'evidence payload') };
          }) };
        });
      }
    }
    for (const row of items.values()) {
      row.item.sources = [...new Map(row.item.sources.map(source => [canonicalRepairJson(source), source])).values()]
        .sort((a, b) => canonicalRepairJson(a).localeCompare(canonicalRepairJson(b)));
      for (const key of ['diagnoses', 'suggestions']) {
        row.item.context![key] = [...new Map((row.item.context![key] as RecordValue[]).map(value => [canonicalRepairJson(value), value])).values()]
          .sort((a, b) => canonicalRepairJson(a).localeCompare(canonicalRepairJson(b)));
      }
      validateRepairItem(row.item);
      boundedRepairJson(row.item, MAX_FROZEN_BYTES);
    }
    return [...items.values()].sort((a, b) => a.item.itemId.localeCompare(b.item.itemId));
  } };
}
