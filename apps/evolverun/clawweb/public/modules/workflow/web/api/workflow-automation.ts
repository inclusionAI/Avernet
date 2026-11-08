import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AutomationPolicyWrite, AutomationSnapshot, WorkflowAutomationPolicy, AutomationRecordPage, AutomationRecordQuery } from '../../server/contracts/workflow-automation'
const base = (id: string) => `/api/task-guard/workflows/${encodeURIComponent(id)}`
async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error ?? '请求未完成，请刷新后核对')
  return value as T
}
export function useAutomationPolicy(id: string) {
  return useQuery({ queryKey: ['workflow-auto-analysis', id], queryFn: () => json<WorkflowAutomationPolicy>(`${base(id)}/auto-analysis`), staleTime: 0 })
}
export function useSaveAutomationPolicy(id: string) {
  const query = useQueryClient()
  return useMutation({ mutationFn: (policy: AutomationPolicyWrite) => json<WorkflowAutomationPolicy>(`${base(id)}/auto-analysis`, { method: 'PUT', body: JSON.stringify(policy) }),
    onSuccess: policy => { query.setQueryData(['workflow-auto-analysis', id], policy); void query.invalidateQueries({ queryKey: ['workflow-automation-status', id] }) } })
}
export function useAutomationStatus(id: string, enabled = true) {
  return useQuery({ queryKey: ['workflow-automation-status', id], queryFn: () => json<AutomationSnapshot>(`${base(id)}/automation-status`), enabled, retry: false, refetchInterval: 15000 })
}
export function useAutomationRecords(id: string, options: AutomationRecordQuery) {
  const params = new URLSearchParams({ period: options.period ?? 'all', status: options.status ?? 'all', limit: String(options.limit ?? 8) })
  if (options.kind) params.set('kind', options.kind)
  if (options.cursor) params.set('cursor', options.cursor)
  return useQuery({ queryKey: ['workflow-automation-records', id, params.toString()],
    queryFn: () => json<AutomationRecordPage>(`${base(id)}/automation-records?${params}`), retry: false,
    staleTime: 15000, refetchInterval: false, refetchOnWindowFocus: false })
}
export async function retryAutomationFailures(id: string, jobId?: string) { return json<AutomationSnapshot>(`${base(id)}/automation-retry`, { method: 'POST', body: JSON.stringify(jobId === undefined ? {} : { jobId }) }) }
