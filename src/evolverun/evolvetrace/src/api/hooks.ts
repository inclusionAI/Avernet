import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type {
  FlowRunsResponse,
  WorkflowTypeRow,
  WorkflowTypesResponse,
  DeployHistoryItem,
  VersionSnapshot,
  FacadeBinding,
  WorkflowHealth,
  WorkflowNodeStats,
  TCLogBotsResponse,
  TCLogQueryParams,
  TCLogQueryResponse,
  TCLogTaskListParams,
  TCLogTaskListResponse,
  TCLogTaskSearchParams,
  TCLogTaskSearchResponse,
  TCLogTrace,
  TCLogTraceDetail,
} from '../types'

const BASE = '/api'

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text().catch(() => 'unknown')}`)
  return res.json() as Promise<T>
}

// ── Workflow list ──

export function useWorkflowTypes(userId?: string) {
  return useQuery({
    queryKey: ['workflow-types', userId],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (userId) sp.set('userId', userId)
      sp.set('limit', '500')
      const qs = sp.toString()
      const res = await fetchJson<WorkflowTypesResponse>(`${BASE}/runs/workflow-types${qs ? `?${qs}` : ''}`)
      return res.workflows
    },
  })
}

// ── Runs ──

export function useFlowRuns(params?: {
  status?: string
  workflowId?: string
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: ['runs', params],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (params?.status) sp.set('status', params.status)
      if (params?.workflowId) sp.set('workflowId', params.workflowId)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson<FlowRunsResponse>(`${BASE}/runs${qs ? `?${qs}` : ''}`)
    },
    enabled: !!params?.workflowId,
  })
}

// ── Workflow health & node stats ──

export function useWorkflowHealth(workflowId: string | null) {
  return useQuery({
    queryKey: ['workflow-health', workflowId],
    queryFn: async () => {
      const res = await fetchJson<{ data: WorkflowHealth }>(`${BASE}/workflows/${encodeURIComponent(workflowId!)}/health`)
      return res.data
    },
    enabled: !!workflowId,
    staleTime: 30_000,
  })
}

export function useWorkflowHealthTrend(workflowId: string | null, days = 7) {
  return useQuery({
    queryKey: ['workflow-health-trend', workflowId, days],
    queryFn: async () => {
      const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId!)}/health-trend?days=${days}`)
      if (!res.ok) return []
      const json = await res.json()
      return (json.data ?? []) as Array<{ snapshot_date: string; overall_score: number; success_rate: number }>
    },
    enabled: !!workflowId,
    staleTime: 300_000,
  })
}

export function useWorkflowNodeStats(workflowId: string | null, days?: number) {
  return useQuery({
    queryKey: ['workflow-node-stats', workflowId, days],
    queryFn: async () => {
      const qs = days ? `?days=${days}` : ''
      const res = await fetchJson<{ data: WorkflowNodeStats }>(`${BASE}/workflows/${encodeURIComponent(workflowId!)}/node-stats${qs}`)
      return res.data
    },
    enabled: !!workflowId,
    staleTime: 30_000,
  })
}

// ── Workflow history & lifecycle ──

export function useWorkflowHistory(workflowId: string | null, limit = 20) {
  return useQuery({
    queryKey: ['workflow-history', workflowId, limit],
    queryFn: async () => {
      const res = await fetchJson<{ workflowId: string; history: DeployHistoryItem[] }>(
        `${BASE}/workflows/${encodeURIComponent(workflowId!)}/history?limit=${limit}`,
      )
      return res
    },
    enabled: !!workflowId,
    staleTime: 60_000,
  })
}

export function useDeleteWorkflow() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (workflowId: string) => {
      return fetchJson<{ ok: boolean }>(`${BASE}/workflows/${encodeURIComponent(workflowId)}`, { method: 'DELETE' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

export function useRestoreWorkflowVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ workflowId, version }: { workflowId: string; version: number }) => {
      const snapshot = await fetchJson<VersionSnapshot>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/history/${version}`)
      return fetchJson<unknown>(`${BASE}/workflows/save`, {
        method: 'POST',
        body: JSON.stringify({ workflowId, spec: JSON.parse(snapshot.specJson) }),
      })
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['workflow-history', variables.workflowId] })
      void queryClient.invalidateQueries({ queryKey: ['workflow-types'] })
    },
  })
}

// ── Facade bindings ──

export function useFacadeBindings() {
  return useQuery({
    queryKey: ['facade-bindings'],
    queryFn: async () => {
      const res = await fetchJson<FacadeBinding[] | { data: FacadeBinding[] }>(`${BASE}/facades`)
      if (Array.isArray(res)) return res
      if (res && typeof res === 'object' && 'data' in res && Array.isArray(res.data)) return res.data
      return []
    },
  })
}

// ── TCLog ──

export function useTCLogBots(
  params?: { ownerId?: string; status?: 'active' | 'all' },
  enabled = true,
) {
  return useQuery({
    queryKey: ['tclog-bots', params],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (params?.ownerId) sp.set('ownerId', params.ownerId)
      if (params?.status) sp.set('status', params.status)
      return fetchJson<TCLogBotsResponse>(`${BASE}/tclog/bots?${sp.toString()}`)
    },
    enabled,
  })
}

export function useTCLogQuery(params: TCLogQueryParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-query', params],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (params.ownerId) sp.set('ownerId', params.ownerId)
      if (params.embed) sp.set('embed', 'true')
      if (params.botId) sp.set('botId', params.botId)
      if (params.traceId) sp.set('traceId', params.traceId)
      if (params.sessionKey) sp.set('sessionKey', params.sessionKey)
      if (params.sessionId) sp.set('sessionId', params.sessionId)
      if (params.keyword) sp.set('keyword', params.keyword)
      sp.set('from', String(params.from))
      sp.set('to', String(params.to))
      if (params.dataSource) sp.set('dataSource', params.dataSource)
      if (params.limit) sp.set('limit', String(params.limit))
      if (params.offset != null) sp.set('offset', String(params.offset))
      if (params.groupBy) sp.set('groupBy', params.groupBy)
      return fetchJson<TCLogQueryResponse>(`${BASE}/tclog/query?${sp.toString()}`)
    },
    enabled,
  })
}

export function useTCLogTasks(params: TCLogTaskListParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-tasks', params],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (params.ownerId) sp.set('ownerId', params.ownerId)
      if (params.botId) sp.set('botId', params.botId)
      if (params.bizScene) sp.set('bizScene', params.bizScene)
      if (params.taskId) sp.set('taskId', params.taskId)
      sp.set('from', String(params.from))
      sp.set('to', String(params.to))
      if (params.limit) sp.set('limit', String(params.limit))
      return fetchJson<TCLogTaskListResponse>(`${BASE}/tclog/tasks?${sp.toString()}`)
    },
    enabled,
  })
}

export function useTCLogTaskSearch(params: TCLogTaskSearchParams, enabled = true) {
  return useQuery({
    queryKey: ['tclog-task-search', params],
    queryFn: async () => fetchJson<TCLogTaskSearchResponse>(`${BASE}/tclog/task-search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),
    enabled,
  })
}

export function useTCLogTrace(
  traceId: string | null,
  ownerId?: string,
  dataSource: TCLogQueryParams['dataSource'] = 'auto',
  botId?: string,
  embed?: boolean,
) {
  return useQuery({
    queryKey: ['tclog-trace', traceId, ownerId, dataSource, botId, embed],
    queryFn: async () => {
      const sp = new URLSearchParams()
      if (ownerId) sp.set('ownerId', ownerId)
      if (dataSource) sp.set('dataSource', dataSource)
      if (botId) sp.set('botId', botId)
      if (embed) sp.set('embed', 'true')
      return fetchJson<TCLogTraceDetail>(`${BASE}/tclog/traces/${encodeURIComponent(traceId!)}?${sp.toString()}`)
    },
    enabled: !!traceId,
  })
}
