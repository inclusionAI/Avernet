import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { RepairCandidatesResponse, RepairDiff, RepairFeedbackRequest, RepairSelectionRequest, RepairTaskDetail } from '../../server/contracts/repair-workbench'
import type { RepairRevision, StoredRepairItem } from '../../server/contracts/repair-batch'

const base = '/api/workflow-repairs'
const segment = encodeURIComponent
const post = <T,>(url: string, body: unknown) => fetchJson<T>(url, { method: 'POST', body: JSON.stringify(body) })
export const repairBatches = {
  candidates: (workflowId: string) => fetchJson<RepairCandidatesResponse>(`${base}/candidates?workflowId=${segment(workflowId)}`),
  task: (taskId: string) => fetchJson<RepairTaskDetail>(`${base}/${segment(taskId)}`),
  create: (request: RepairSelectionRequest) => post<RepairRevision>(base, request),
  revise: (taskId: string, request: RepairFeedbackRequest) => post<RepairRevision>(`${base}/${segment(taskId)}/revisions`, request),
  disposition: (itemId: string, request: { workflowId: string; inputDigest: string; expectedStateVersion: number; contentRevision: number; action: 'no_action' | 'restore'; reason: string; requestId: string }) =>
    post<StoredRepairItem>(`${base}/items/${segment(itemId)}/disposition`, request),
  cancel: (taskId: string, expectedRevision: number) => post<unknown>(`${base}/${segment(taskId)}/cancel`, { expectedRevision }),
  retryDispatch: (taskId: string, expectedRevision: number) => post<{ ok: true }>(`${base}/${segment(taskId)}/retry-dispatch`, { expectedRevision }),
  diff: (taskId: string, revision: number, comparison: 'baseline' | 'parent') =>
    fetchJson<RepairDiff>(`${base}/${segment(taskId)}/revisions/${revision}/diff?base=${comparison}`),
}
