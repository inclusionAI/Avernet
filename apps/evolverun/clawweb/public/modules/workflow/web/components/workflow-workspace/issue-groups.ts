import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { EvolveRunDiagnosis } from '@avernet/clawweb-shared/web/api/client';
import { readOnlyJson } from '../../api/read-only-json';

export type IssueGroupView = {
  presentation?: 'summary';
  workflowId: string; signature: string; inputDigest: string; flowIds: string[];
  aggregationStatus: string; aggregationId: string | null; stale: boolean;
  aggregationInputVersion?: 'workflow-issue-summary-input/v1' | 'workflow-issue-summary-input/v2' | null;
  aggregationInputSummary?: { totalSources: number; fullSources: number; compactSources: number } | null;
  sources: Array<{ sourceId: string; flowId: string; flowIds: string[]; analysisId: string; diagnosisId: string;
    nodeId: string | null; failureSignature: string; failureMode: string; reasoning: string; completedAtMs: number; evidenceEventIds: string[];
    proposal?: { summary: string; operations: unknown[] } }>;
  summarySources?: IssueGroupView['sources'];
  summary: null | { summary: string; unknowns: string[]; causes: Array<{ title: string; conclusion: string;
    certainty: 'supported' | 'hypothesis' | 'unknown'; sourceIds: string[] }> };
};

export function useIssueGroups(workflowId: string) {
  return useQuery({ queryKey: ['evolve-issue-groups', workflowId],
    queryFn: ({ signal }) => readOnlyJson<{ groups: IssueGroupView[] }>(`/api/evolve/issue-groups?workflowId=${encodeURIComponent(workflowId)}&view=summary`, signal),
    retry: false,
    enabled: !!workflowId,
    refetchInterval: query => query.state.data?.groups.some(group => group.aggregationStatus === 'queued') ? 15_000 : 60_000,
  });
}

export function useIssueGroupDetail(group: IssueGroupView | undefined) {
  const [data, setData] = useState<IssueGroupView>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const compact = group?.presentation === 'summary';
  useEffect(() => {
    if (!compact || !group) return;
    const controller = new AbortController();
    setLoading(true); setData(undefined); setError('');
    const params = new URLSearchParams({ workflowId: group.workflowId, signature: group.signature });
    readOnlyJson<{ groups: IssueGroupView[] }>(`/api/evolve/issue-groups?${params}`, controller.signal)
      .then(result => { if (!controller.signal.aborted) {
        const detail = result.groups.find(value => value.signature === group.signature);
        if (!detail) throw new Error('该问题已更新，请刷新问题列表');
        setData(detail);
      } }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [compact, group?.workflowId, group?.signature, group?.inputDigest, group?.aggregationId, group?.aggregationStatus, refresh]);
  return { group: compact ? data : group, loading: compact && (loading || !data && !error), error: compact ? error : '', retry: () => setRefresh(value => value + 1) };
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
