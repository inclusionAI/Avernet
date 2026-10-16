import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AutomationPolicyWrite, AutomationSnapshot, WorkflowAutomationPolicy } from '../../server/contracts/workflow-automation'
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
export function useAutomationStatus(id: string) {
  return useQuery({ queryKey: ['workflow-automation-status', id], queryFn: () => json<AutomationSnapshot>(`${base(id)}/automation-status`), retry: false, refetchInterval: 15000 })
}
export async function retryAutomationFailures(id: string) { return json<AutomationSnapshot>(`${base(id)}/automation-retry`, { method: 'POST', body: '{}' }) }
