import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readOnlyJson } from './read-only-json'
import type { RepairCandidatesQuery, RepairCandidatesResponse, RepairDiff, RepairFeedbackRequest, RepairInboxItem, RepairSelectionRequest, RepairTaskDetail } from '../../server/contracts/repair-workbench'
import type { RepairRevision, StoredRepairItem } from '../../server/contracts/repair-batch'
import type { RepairConfirmRequest, RepairDeployRequest, RepairApproval, RepairDeployment, RepairReleaseTarget } from '../../server/contracts/repair-release'
import type { RepairVerificationEvidence, RepairVerificationConclusion } from '../../server/contracts/repair-verification'

const base = '/api/workflow-repairs'
const segment = encodeURIComponent
const post = <T,>(url: string, body: unknown) => fetchJson<T>(url, { method: 'POST', body: JSON.stringify(body) })
export const repairBatches = {
  request: (workflowId: string, requestId: string) => readOnlyJson<{ revision: RepairRevision | null }>(`${base}/requests/lookup?${new URLSearchParams({ workflowId, requestId })}`),
  publish: (taskId: string, request: RepairConfirmRequest) => post<RepairDeployment>(`${base}/${segment(taskId)}/publish`, request),
  deploymentTargets: (taskId: string) => readOnlyJson<{ targets: RepairReleaseTarget[] }>(`${base}/${segment(taskId)}/deployment-targets`),
  confirm: (taskId: string, request: RepairConfirmRequest) => post<RepairApproval>(`${base}/${segment(taskId)}/confirm`, request),
  revokeConfirmation: (taskId: string, request: RepairDeployRequest) => post<RepairApproval>(`${base}/${segment(taskId)}/revoke-confirmation`, request),
  deploy: (taskId: string, request: RepairDeployRequest) => post<RepairDeployment>(`${base}/${segment(taskId)}/deployment`, request),
  reconcileDeployment: (taskId: string, deploymentId: string) => post<RepairDeployment>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/reconcile`, {}),
  resumeDeployment: (taskId: string, deploymentId: string, requestId: string) => post<RepairDeployment>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/resume`, { requestId }),
  stopDeployment: (taskId: string, deploymentId: string, requestId: string) => post<RepairDeployment>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/stop`, { requestId }),
  verification: (taskId: string, deploymentId: string) => readOnlyJson<{ evidence: RepairVerificationEvidence; conclusions: RepairVerificationConclusion[] }>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/verification`),
  conclude: (taskId: string, deploymentId: string, request: { requestId: string; itemId: string; expectedStateVersion: number;
    result: 'verified' | 'ineffective' | 'inconclusive'; reason: string; flowIds: string[] }) => post<RepairVerificationConclusion>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/verification`, request),
  reopenItem: (taskId: string, deploymentId: string, request: { requestId: string; itemId: string; expectedStateVersion: number }) => post<unknown>(`${base}/${segment(taskId)}/deployment/${segment(deploymentId)}/reopen-item`, request),
  candidates: (workflowId: string, query: RepairCandidatesQuery = {}) => {
    if (query.previewSignatures !== undefined) return readOnlyJson<RepairCandidatesResponse>(
      `${base}/candidates/query`, undefined, { ...query, workflowId })
    const params = new URLSearchParams({ workflowId })
    if (query.state) params.set('state', query.state)
    if (query.page) params.set('page', String(query.page))
    if (query.pageSize) params.set('pageSize', String(query.pageSize))
    if (query.includeHistorical) params.set('includeHistorical', 'true')
    if (query.nodeId) params.set('nodeId', query.nodeId)
    if (query.failureMode) params.set('failureMode', query.failureMode)
    if (query.signature) params.set('signature', query.signature)
    return readOnlyJson<RepairCandidatesResponse>(`${base}/candidates?${params}`)
  },
  item: (workflowId: string, itemId: string) => readOnlyJson<RepairInboxItem>(`${base}/items/${segment(itemId)}?workflowId=${segment(workflowId)}`),
  task: (taskId: string, revisionPage = 1) => readOnlyJson<RepairTaskDetail>(`${base}/${segment(taskId)}${revisionPage > 1 ? `?revisionPage=${revisionPage}` : ''}`),
  create: (request: RepairSelectionRequest) => post<RepairRevision>(base, request),
  revise: (taskId: string, request: RepairFeedbackRequest) => post<RepairRevision>(`${base}/${segment(taskId)}/revisions`, request),
  disposition: (itemId: string, request: { workflowId: string; inputDigest: string; expectedStateVersion: number; contentRevision: number; untilMs?: number; action: 'no_action' | 'restore'; reason: string; requestId: string; includeHistorical?: boolean }) =>
    post<StoredRepairItem>(`${base}/items/${segment(itemId)}/disposition`, request),
  cancel: (taskId: string, expectedRevision: number) => post<unknown>(`${base}/${segment(taskId)}/cancel`, { expectedRevision }),
  retryResult: (taskId: string, expectedRevision: number) => post<{ ok: true }>(`${base}/${segment(taskId)}/retry-result`, { expectedRevision }),
  retryGeneration: (taskId: string, request: { expectedRevision: number; requestId: string }) => post<RepairRevision>(`${base}/${segment(taskId)}/retry-generation`, request),
  retryDispatch: (taskId: string, expectedRevision: number) => post<{ ok: true }>(`${base}/${segment(taskId)}/retry-dispatch`, { expectedRevision }),
  diff: (taskId: string, revision: number, comparison: 'baseline' | 'parent') =>
    readOnlyJson<RepairDiff>(`${base}/${segment(taskId)}/revisions/${revision}/diff?base=${comparison}`),
}
