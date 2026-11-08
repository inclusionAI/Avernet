import type { RepairTrial, RepairTrialRequest, RepairTrialOptions, RepairTrialTarget } from '../../server/contracts/repair-trial'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readOnlyJson } from './read-only-json'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import type { RepairCandidatesQuery, RepairCandidatesResponse, RepairDiff, RepairFeedbackRequest, RepairInboxItem, RepairSelectionRequest, RepairSelectionState, RepairTaskDetail } from '../../server/contracts/repair-workbench'
import type { RepairItem, RepairRevision, StoredRepairItem } from '../../server/contracts/repair-batch'
import type { RepairConfirmRequest, RepairDeployRequest, RepairApproval, RepairDeployment, RepairReleaseTarget } from '../../server/contracts/repair-release'
import type { RepairVerificationEvidence, RepairVerificationConclusion } from '../../server/contracts/repair-verification'

const base = '/api/workflow-repairs'
const segment = encodeURIComponent
const post = <T,>(url: string, body: unknown) => fetchJson<T>(url, { method: 'POST', body: JSON.stringify(body) })
// Coalesce only concurrent identical reads, scoped to the logged-in principal. Never cache a completed result.
const candidateReads = new Map<string, Promise<RepairCandidatesResponse>>()
async function readCandidates(workflowId: string, query: RepairCandidatesQuery): Promise<RepairCandidatesResponse> {
  const readPage = (metadataPage: number): Promise<RepairCandidatesResponse> => {
    if (query.previewSignatures !== undefined) return readOnlyJson<RepairCandidatesResponse>(
      `${base}/candidates/query`, undefined, { ...query, workflowId, metadataPage })
    const params = new URLSearchParams({ workflowId })
    params.set('metadataPage', String(metadataPage))
    if (query.versionScope) params.set('versionScope', query.versionScope)
    if (query.snapshotKey) params.set('snapshotKey', query.snapshotKey)
    if (query.state) params.set('state', query.state)
    if (query.page) params.set('page', String(query.page))
    if (query.pageSize) params.set('pageSize', String(query.pageSize))
    if (query.includeHistorical) params.set('includeHistorical', 'true')
    if (query.includeTasks !== undefined) params.set('includeTasks', String(query.includeTasks))
    if (query.nodeId) params.set('nodeId', query.nodeId)
    if (query.failureMode) params.set('failureMode', query.failureMode)
    if (query.signature) params.set('signature', query.signature)
    return readOnlyJson<RepairCandidatesResponse>(`${base}/candidates?${params}`)
  }
  const result = await readPage(query.metadataPage ?? 1)
  if (query.metadataPage !== undefined) return result
  const totalPages = result.metadata?.totalPages ?? 1
  for (let page = 2; page <= totalPages; page++) {
    const next = await readPage(page)
    if (next.inputDigest !== result.inputDigest || next.metadata?.digest !== result.metadata?.digest || next.metadata?.totalPages !== totalPages) throw new Error('建议来源已变化，请重试读取。')
    result.tasks.push(...next.tasks); result.repairSignatureKeys.push(...next.repairSignatureKeys)
    result.facets.nodes.push(...next.facets.nodes); result.facets.failureModes.push(...next.facets.failureModes)
  }
  return result
}
export const repairBatches = {
  selection: (workflowId: string, itemIds: string[], includeHistorical: boolean) => readOnlyJson<RepairSelectionState>(`${base}/selection/query`, undefined, { workflowId, itemIds, includeHistorical }),
  retryProposalIntegration: (workflowId: string) => post<{ ok: true }>(`${base}/proposal-integration/retry`, { workflowId }),
  trials: (taskId: string, offset = 0) => readOnlyJson<{ configured: boolean; nextOffset?: number | null; trials: RepairTrial[] }>(`${base}/${segment(taskId)}/trials?offset=${offset}`),
  trialOptions: (taskId: string, revision: number) => readOnlyJson<RepairTrialOptions & { inputSchemaDigest: string; targets: RepairTrialTarget[] }>(`${base}/${segment(taskId)}/trials/options?revision=${revision}`),
  startTrial: (taskId: string, request: RepairTrialRequest) => post<RepairTrial>(`${base}/${segment(taskId)}/trials`, request),
  stopTrial: (taskId: string, trialId: string) => post<RepairTrial>(`${base}/${segment(taskId)}/trials/${segment(trialId)}/stop`, {}),
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
    const principal = getClientUser()?.userId
    if (!principal) return readCandidates(workflowId, query)
    const key = JSON.stringify([principal, workflowId, Object.entries(query).filter(([, value]) => value !== undefined).sort(([a], [b]) => a.localeCompare(b))])
    let pending = candidateReads.get(key)
    if (!pending) {
      pending = readCandidates(workflowId, query)
      candidateReads.set(key, pending)
      void pending.then(() => candidateReads.delete(key), () => candidateReads.delete(key))
    }
    // Metadata assembly and callers may mutate arrays; they must not share their response object.
    return pending.then(result => structuredClone(result))
  },
  item: (workflowId: string, itemId: string, originalOnly = false) => readOnlyJson<RepairInboxItem>(`${base}/items/${segment(itemId)}?workflowId=${segment(workflowId)}${originalOnly ? '&originalOnly=true' : ''}`),
  task: (taskId: string, revisionPage = 1) => readOnlyJson<RepairTaskDetail>(`${base}/${segment(taskId)}${revisionPage > 1 ? `?revisionPage=${revisionPage}` : ''}`),
  material: (taskId:string,revision:number,itemId:string) => readOnlyJson<RepairItem>(`${base}/${segment(taskId)}/revisions/${revision}/materials/${segment(itemId)}`),
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
