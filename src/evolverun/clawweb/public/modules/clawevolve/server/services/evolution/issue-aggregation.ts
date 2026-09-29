import { digestCanonicalJson, type WorkflowEvolutionDiagnosis } from './contracts.js';

export type IssueAnalysis = { analysisId: string; flowId: string | null; flowIds?: string[]; completedAtMs: number; diagnoses: WorkflowEvolutionDiagnosis[] };
export type IssueSource = WorkflowEvolutionDiagnosis & { sourceId: string; analysisId: string; flowId: string; completedAtMs: number };
export type IssueGroup = { workflowId: string; signature: string; inputDigest: string; flowIds: string[]; sources: IssueSource[] };
export const ISSUE_AGGREGATION_INPUT_V1 = 'workflow-issue-summary-input/v1' as const;
export const ISSUE_AGGREGATION_INPUT_V2 = 'workflow-issue-summary-input/v2' as const;
export type IssueAggregationInputVersion = typeof ISSUE_AGGREGATION_INPUT_V1 | typeof ISSUE_AGGREGATION_INPUT_V2;
export type AggregationModelSource =
  | (IssueSource & { detailLevel: 'full'; proposalRef?: string })
  | { sourceId: string; detailLevel: 'compact'; representativeSourceId?: string; proposalRef?: string; reasoningExcerpt?: string };
export type IssueAggregationModelInput = Omit<IssueGroup, 'sources'> & {
  sources: AggregationModelSource[];
  inputSummary: { totalSources: number; fullSources: number; compactSources: number };
};
export type IssueSummary = {
  summary: string;
  causes: Array<{ title: string; conclusion: string; certainty: 'supported' | 'hypothesis' | 'unknown'; sourceIds: string[] }>;
  unknowns: string[];
};

const FULL_SOURCE_LIMIT = 24;
const COMPACT_REASONING_CHARS = 240;
function proposalRef(source: IssueSource): string | undefined {
  const proposal = source.proposal;
  return proposal?.operations?.length ? digestCanonicalJson(proposal.operations) : undefined;
}
function diversityKey(source: IssueSource): string {
  return digestCanonicalJson([proposalRef(source) ?? null, source.nodeId, source.failureMode,
    source.reasoning.replace(/\s+/gu, ' ').trim().slice(0, COMPACT_REASONING_CHARS)]);
}

/** Preserve every source identity while spending detailed context on recent, diverse diagnoses. */
export function buildAggregationModelInput(group: IssueGroup): IssueAggregationModelInput {
  const recent = [...group.sources].sort((a, b) => b.completedAtMs - a.completedAtMs || a.sourceId.localeCompare(b.sourceId));
  const selected = new Set<string>();
  const seen = new Set<string>();
  for (const source of recent) {
    const key = diversityKey(source);
    if (seen.has(key)) continue;
    seen.add(key); selected.add(source.sourceId);
    if (selected.size >= FULL_SOURCE_LIMIT) break;
  }
  for (const source of recent) {
    if (selected.size >= FULL_SOURCE_LIMIT) break;
    selected.add(source.sourceId);
  }
  const representatives = new Map(recent.filter(source => selected.has(source.sourceId)).map(source => [diversityKey(source), source.sourceId]));
  const sources: AggregationModelSource[] = group.sources.map(source => {
    const reference = proposalRef(source);
    if (selected.has(source.sourceId)) return { ...source, detailLevel: 'full', ...(reference ? { proposalRef: reference } : {}) };
    const representativeSourceId = representatives.get(diversityKey(source));
    return { sourceId: source.sourceId, detailLevel: 'compact',
      ...(representativeSourceId ? { representativeSourceId } : {
        reasoningExcerpt: source.reasoning.replace(/\s+/gu, ' ').trim().slice(0, COMPACT_REASONING_CHARS),
        ...(reference ? { proposalRef: reference } : {}),
      }) };
  });
  const fullSources = sources.filter(source => source.detailLevel === 'full').length;
  return { workflowId: group.workflowId, signature: group.signature, inputDigest: group.inputDigest,
    flowIds: group.flowIds, sources,
    inputSummary: { totalSources: sources.length, fullSources, compactSources: sources.length - fullSources } };
}

/** Select entire latest successful analyses before grouping; an empty result replaces older findings. */
export function buildIssueGroups(workflowId: string, analyses: IssueAnalysis[]): IssueGroup[] {
  const latest = new Map<string, IssueAnalysis>();
  const ordered = [...analyses].sort((a, b) => b.completedAtMs - a.completedAtMs || b.analysisId.localeCompare(a.analysisId));
  for (const analysis of ordered) {
    const flowIds = new Set([...(analysis.flowIds ?? []), ...(analysis.flowId ? [analysis.flowId] : []), ...analysis.diagnoses.flatMap(d => d.flowIds)]);
    for (const flowId of flowIds) if (!latest.has(flowId)) latest.set(flowId, analysis);
  }
  const groups = new Map<string, IssueSource[]>();
  for (const [flowId, analysis] of latest) {
    for (const diagnosis of analysis.diagnoses.filter(d => d.flowIds.includes(flowId))) {
      const source: IssueSource = { ...diagnosis, flowIds: [flowId], flowId, analysisId: analysis.analysisId,
        completedAtMs: analysis.completedAtMs,
        sourceId: digestCanonicalJson([analysis.analysisId, diagnosis.diagnosisId, flowId]),
      };
      const sources = groups.get(diagnosis.failureSignature) ?? [];
      sources.push(source);
      groups.set(diagnosis.failureSignature, sources);
    }
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([signature, sources]) => {
    sources.sort((a, b) => a.sourceId.localeCompare(b.sourceId));
    return { workflowId, signature, sources, flowIds: [...new Set(sources.map(s => s.flowId))].sort(),
      inputDigest: digestCanonicalJson({ workflowId, signature, sources }),
    };
  });
}

export function validateIssueSummary(raw: unknown, group: IssueGroup): IssueSummary {
  const text = (value: unknown, max = 8000): string => {
    if (typeof value !== 'string' || !value.trim() || value.length > max) throw new Error('invalid aggregation text');
    return value.trim();
  };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('invalid aggregation');
  const value = raw as Record<string, unknown>;
  if (!Array.isArray(value.causes) || !value.causes.length || value.causes.length > 100) throw new Error('invalid causes');
  const allowed = new Set(group.sources.map(s => s.sourceId));
  const covered = new Set<string>();
  const causes = value.causes.map((entry): IssueSummary['causes'][number] => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error('invalid cause');
    const c = entry as Record<string, unknown>;
    if (!['supported', 'hypothesis', 'unknown'].includes(String(c.certainty))) throw new Error('invalid certainty');
    if (!Array.isArray(c.sourceIds) || !c.sourceIds.length || c.sourceIds.length > allowed.size) throw new Error('invalid sourceIds');
    const sourceIds = [...new Set(c.sourceIds.map(id => text(id, 64)))];
    for (const id of sourceIds) {
      if (!allowed.has(id)) throw new Error('unknown source reference');
      covered.add(id);
    }
    return { title: text(c.title, 200), conclusion: text(c.conclusion), certainty: c.certainty as IssueSummary['causes'][number]['certainty'], sourceIds };
  });
  if (covered.size !== allowed.size) throw new Error('aggregation omitted source diagnoses');
  if (!Array.isArray(value.unknowns) || value.unknowns.length > 100) throw new Error('invalid unknowns');
  return { summary: text(value.summary), causes, unknowns: value.unknowns.map(v => text(v)) };
}
