import { canonicalJson, digestCanonicalJson } from './contracts.js';
import type { IssueGroup, IssueSource } from './issue-aggregation.js';

export type RepairCandidate = { id: string; summary: string; proposal: NonNullable<IssueSource['proposal']>; sources: IssueSource[] };
export type RepairSelection = { schemaVersion: 'issue-group-repair/v1'; workflowId: string; signature: string; inputDigest: string; candidates: RepairCandidate[]; excludedIds: string[] };
export type RepairOutcome = { id: string; status: 'applied' | 'not_needed' | 'unresolved'; reason: string };

export function repairCandidates(group: IssueGroup): RepairCandidate[] {
  const unique = new Map<string, RepairCandidate>();
  for (const source of group.sources) {
    if (!source.proposal?.summary?.trim()) continue;
    const id = digestCanonicalJson(source.proposal);
    const candidate = unique.get(id) ?? { id, summary: source.proposal.summary, proposal: source.proposal, sources: [] };
    candidate.sources.push(source);
    unique.set(id, candidate);
  }
  return [...unique.values()];
}

export function selectRepairCandidates(group: IssueGroup, digest: unknown, ids: unknown): RepairSelection {
  if (digest !== group.inputDigest) throw new Error('group_changed');
  if (!Array.isArray(ids) || !ids.length || ids.length > 20 || ids.some(id => typeof id !== 'string') || new Set(ids).size !== ids.length) throw new Error('invalid_selection');
  const all = repairCandidates(group);
  const candidates = all.filter(candidate => ids.includes(candidate.id));
  if (candidates.length !== ids.length) throw new Error('unknown_candidate');
  const selection: RepairSelection = { schemaVersion: 'issue-group-repair/v1', workflowId: group.workflowId, signature: group.signature, inputDigest: group.inputDigest,
    candidates, excludedIds: all.filter(candidate => !ids.includes(candidate.id)).map(candidate => candidate.id) };
  if (Buffer.byteLength(canonicalJson(selection), 'utf8') > 180_000) throw new Error('selection_too_large');
  return selection;
}

export function validateRepairOutcomes(raw: unknown, selection: Pick<RepairSelection, 'candidates'>): RepairOutcome[] {
  if (!Array.isArray(raw) || raw.length !== selection.candidates.length) throw new Error('incomplete_repair_outcomes');
  const remaining = new Set(selection.candidates.map(candidate => candidate.id));
  return raw.map(item => {
    if (!item || typeof item !== 'object' || !remaining.delete(item.id)
      || !['applied', 'not_needed', 'unresolved'].includes(item.status)
      || typeof item.reason !== 'string' || !item.reason.trim() || item.reason.length > 4000) throw new Error('invalid_repair_outcome');
    return { id: item.id, status: item.status, reason: item.reason.trim() };
  });
}
