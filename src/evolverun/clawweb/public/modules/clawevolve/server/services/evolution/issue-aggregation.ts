import { digestCanonicalJson, type WorkflowEvolutionDiagnosis } from './contracts.js';

export type IssueAnalysis = { analysisId: string; flowId: string | null; flowIds?: string[]; completedAtMs: number; diagnoses: WorkflowEvolutionDiagnosis[] };
export type IssueSource = WorkflowEvolutionDiagnosis & { sourceId: string; analysisId: string; flowId: string; completedAtMs: number };
export type IssueGroup = { workflowId: string; signature: string; inputDigest: string; flowIds: string[]; sources: IssueSource[] };
export type IssueSummary = {
  summary: string;
  causes: Array<{ title: string; conclusion: string; certainty: 'supported' | 'hypothesis' | 'unknown'; sourceIds: string[] }>;
  unknowns: string[];
};

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
