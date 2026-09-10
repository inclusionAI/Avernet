import { useQuery } from '@tanstack/react-query';
import { fetchJson, type EvolveRunDiagnosis } from '@avernet/clawweb-shared/web/api/client';

export type IssueGroupView = {
  workflowId: string; signature: string; inputDigest: string; flowIds: string[];
  aggregationStatus: string; aggregationId: string | null; stale: boolean;
  sources: Array<{ sourceId: string; flowId: string; flowIds: string[]; analysisId: string; diagnosisId: string;
    nodeId: string | null; failureSignature: string; failureMode: string; reasoning: string; completedAtMs: number; evidenceEventIds: string[];
    proposal?: { summary: string; operations: unknown[] } }>;
  summarySources?: IssueGroupView['sources'];
  summary: null | { summary: string; unknowns: string[]; causes: Array<{ title: string; conclusion: string;
    certainty: 'supported' | 'hypothesis' | 'unknown'; sourceIds: string[] }> };
};

export function useIssueGroups(workflowId: string) {
  return useQuery({ queryKey: ['evolve-issue-groups', workflowId],
    queryFn: () => fetchJson<{ groups: IssueGroupView[] }>(`/api/evolve/issue-groups?workflowId=${encodeURIComponent(workflowId)}`),
    enabled: !!workflowId,
    refetchInterval: query => query.state.data?.groups.some(group => group.aggregationStatus === 'queued') ? 15_000 : 60_000,
  });
}

export function groupDiagnoses(group: IssueGroupView): EvolveRunDiagnosis[] {
  return group.sources.map(source => ({
    id: source.sourceId, diagnosis_id: source.diagnosisId, analysis_id: source.analysisId,
    flow_id: source.flowId, flow_ids: [source.flowId], workflow_id: group.workflowId, run_id: source.flowId,
    node_id: source.nodeId, weak_node_id: source.nodeId, failure_signature: group.signature, failure_mode: source.failureMode,
    executor_type: null, suggested_fix_kind: null, lesson_id_hit: null, error_text: null, reasoning: source.reasoning,
    evidence_event_ids: source.evidenceEventIds, created_by: null, gmt_create: source.completedAtMs, gmt_modified: source.completedAtMs,
  }));
}
