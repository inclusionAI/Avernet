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

export type IssuePageQuery = { page: number; pageSize: number; nodeId?: string; failureMode?: string };
export type IssuePage = { groups: IssueGroupView[]; facets?: { nodes: string[]; modes: string[] };
  page?: { page: number; pageSize: number; total: number; totalPages: number } };
export function useIssueGroups(workflowId: string, query?: IssuePageQuery) {
  const params = new URLSearchParams({ workflowId, view: 'summary' });
  if (query) {
    params.set('page', String(query.page)); params.set('pageSize', String(query.pageSize));
    if (query.nodeId) params.set('nodeId', query.nodeId);
    if (query.failureMode) params.set('failureMode', query.failureMode);
  }
  return useQuery({ queryKey: ['evolve-issue-groups', workflowId, query],
    queryFn: ({ signal }) => readOnlyJson<IssuePage>(`/api/evolve/issue-groups?${params}`, signal),
    retry: false,
    enabled: !!workflowId,
    refetchInterval: query => query.state.data?.groups.some(group => group.aggregationStatus === 'queued') ? 15_000 : 60_000,
  });
}

export function useIssueGroupDetail(group: IssueGroupView | undefined) {
  const compact = group?.presentation === 'summary';
  const query = useQuery({ queryKey: ['issue-detail', group?.workflowId, group?.signature, group?.inputDigest, group?.aggregationId, group?.aggregationStatus],
    enabled: compact && !!group, staleTime: 30_000, gcTime: 300_000, retry: false,
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ workflowId: group!.workflowId, signature: group!.signature });
      const result = await readOnlyJson<{ groups: IssueGroupView[] }>(`/api/evolve/issue-groups?${params}`, signal);
      const detail = result.groups.find(value => value.signature === group!.signature);
      if (!detail) throw new Error('该问题已更新，请刷新问题列表');
      return detail;
    } });
  return { group: compact ? (query.error ? undefined : query.data) : group, loading: compact && query.isPending,
    error: compact && query.error ? query.error.message : '', retry: () => { void query.refetch(); } };
}

/** A selected issue stays reachable even when it is not on the current list page. */
export function useSelectedIssueGroup(workflowId: string, signature: string | null, current?: IssueGroupView) {
  const [lookup, setLookup] = useState<{ group?: IssueGroupView; error?: string }>({})
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    setLookup({})
    if (!signature || current) return
    const controller = new AbortController()
    const params = new URLSearchParams({ workflowId, signature, view: 'summary' })
    readOnlyJson<{ groups: IssueGroupView[] }>(`/api/evolve/issue-groups?${params}`, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        const group = result.groups.find(value => value.signature === signature)
        setLookup(group ? { group } : { error: '该问题已更新，请刷新问题列表。' })
      }).catch(reason => { if (!controller.signal.aborted) setLookup({ error: reason instanceof Error ? reason.message : String(reason) }) })
    return () => controller.abort()
  }, [workflowId, signature, current?.signature, refresh])
  return { group: current ?? (lookup.group?.signature === signature ? lookup.group : undefined),
    error: lookup.error, retry: () => setRefresh(value => value + 1) }
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
