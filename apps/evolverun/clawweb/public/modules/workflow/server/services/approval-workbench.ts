import { createHash } from 'node:crypto'

/** Person-centric approval-card search. The host supplies a verified principal. */
export type ApprovalWorkbenchCard = {
  id: number; flow_id: string; node_id: string; workflow_id: string; workflow_title: string | null;
  message: string | null; approver_ids: string; approved_by: string | null; rejected_by: string | null;
  status: string; delivery_mode: string; created_at: number; run_status?: string | null; is_latest?: number;
}
export type ApprovalPrincipal = { userId: string; isAdmin: boolean }
export type ApprovalWorkbenchStore = {
  scan(before: { createdAt: number; id: number } | null, limit: number, candidate: { empId?: string; workflowId?: string; flowId?: string; createdFrom?: number; createdTo?: number; status?: string }): Promise<ApprovalWorkbenchCard[]>
  findById?(id: number): Promise<import('../repositories/approval-card-repository.js').ApprovalCardRow | null>
  findRunStatus?(flowId: string): Promise<string | null>
  findLatestCardId?(flowId: string, nodeId: string): Promise<number | null>
  resolveActive?(id: number, actorId: string, action: 'approve' | 'reject', comment?: string): Promise<import('../repositories/approval-card-repository.js').ApprovalActionResult>
}
export type ApprovalWorkbenchFilters = {
  empId?: string; scope?: 'all'; workflowId?: string; flowId?: string;
  personalStatus?: 'pending' | 'handled' | 'approved' | 'rejected';
  status?: 'pending' | 'approved' | 'rejected';
  createdFrom?: number; createdTo?: number; limit?: number; cursor?: string; start?: number; end?: number;
}
export class ApprovalWorkbenchError extends Error {
  constructor(public readonly code: 'FORBIDDEN' | 'INVALID_ARGUMENT', message: string) { super(message) }
}
export function activeApprovalRun(status: string | null | undefined): boolean { return status != null && ['running','pending','waiting','blocked'].includes(status) }
const terminalRun = (status: string | null | undefined) => status != null && ['cancelled','canceled','failed','succeeded','completed','aborted'].includes(status)
export const normalizeApprovalEmployee = (id: string) => id.trim().replace(/^0+/, '') || '0'
const list = (value: string | null) => (value ?? '').split(',').map(id => id.trim()).filter(Boolean)
function includes(ids: string[], id: string) { return ids.some(value => normalizeApprovalEmployee(value) === normalizeApprovalEmployee(id)) }

/** Filter BEFORE pagination, scanning ordered DB chunks instead of dropping matches past a page. */
export async function searchApprovalWorkbench(store: ApprovalWorkbenchStore, principal: ApprovalPrincipal, filters: ApprovalWorkbenchFilters) {
  const actor = principal.userId.trim(), all = filters.scope === 'all'
  const target = all ? undefined : (filters.empId ?? actor).trim()
  if (!actor || (!all && !target)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '审批人工号不能为空')
  if (all && !principal.isAdmin) throw new ApprovalWorkbenchError('FORBIDDEN', '仅管理员可以查询所有人的审批')
  if (all && filters.empId != null) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '所有人范围不能同时指定审批人工号')
  if (target && normalizeApprovalEmployee(actor) !== normalizeApprovalEmployee(target) && !principal.isAdmin) throw new ApprovalWorkbenchError('FORBIDDEN', '仅管理员可以查询其他人的审批')
  const hasRange = filters.start != null || filters.end != null
  if (hasRange && (filters.cursor || filters.limit != null || filters.start == null || filters.end == null ||
      !Number.isInteger(filters.start) || !Number.isInteger(filters.end) || filters.start < 1 || filters.end < filters.start || filters.end > 10000 || filters.end - filters.start + 1 > 100)) {
    throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '结果范围无效')
  }
  const limit = hasRange ? filters.end! - filters.start! + 1 : (filters.limit ?? 20)
  let skip = hasRange ? filters.start! - 1 : 0
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '每页数量必须在 1 至 100 之间')
  if (filters.createdFrom != null && filters.createdTo != null && filters.createdFrom >= filters.createdTo) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '创建时间范围无效')
  const queryKey = createHash('sha256').update(JSON.stringify([
    normalizeApprovalEmployee(actor), all ? {scope:'all'} : normalizeApprovalEmployee(target!), filters.workflowId ?? null, filters.flowId ?? null,
    filters.personalStatus ?? null, filters.status ?? null, filters.createdFrom ?? null, filters.createdTo ?? null,
  ])).digest('hex')
  let before: { createdAt: number; id: number } | null = null
  if (filters.cursor) {
    try {
      const decoded = JSON.parse(Buffer.from(filters.cursor, 'base64url').toString('utf8')) as {id:number;createdAt:number;key:string}
      if (!decoded || decoded.key !== queryKey || !Number.isSafeInteger(decoded.id) || decoded.id <= 0 || !Number.isFinite(decoded.createdAt)) throw new Error('invalid cursor')
      before = { id: decoded.id, createdAt: decoded.createdAt }
    } catch { throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '分页参数无效') }
  }
  const items: Array<{ cardId: number; flowId: string; nodeId: string; workflowId: string; workflowTitle: string | null; message: string | null; approverIds: string[]; status: string; personalStatus: 'pending' | 'approved' | 'rejected' | null; matchedApprovers?: Array<{empId:string;personalStatus:'pending'|'approved'|'rejected'}>; actionability: 'available' | 'unknown' | 'none'; createdAt: number }> = []
  let more = false, last: { createdAt: number; id: number } | null = null
  while (true) {
    const batch = await store.scan(before, 200, { empId: target ? normalizeApprovalEmployee(target) : undefined, workflowId: filters.workflowId, flowId: filters.flowId, createdFrom: filters.createdFrom, createdTo: filters.createdTo, status: filters.status })
    if (!batch.length) break
    for (const row of batch) {
      before = { createdAt: Number(row.created_at), id: Number(row.id) }
      if ((filters.status && row.status !== filters.status) || row.delivery_mode !== 'card-web' || (filters.workflowId && row.workflow_id !== filters.workflowId) || (filters.flowId && row.flow_id !== filters.flowId)) continue
      if (filters.createdFrom != null && Number(row.created_at) < filters.createdFrom) continue
      if (filters.createdTo != null && Number(row.created_at) >= filters.createdTo) continue
      const approverIds = list(row.approver_ids)
      const candidates = all ? approverIds : target && includes(approverIds, target) ? [target] : []
      const seen = new Set<string>()
      const matchedApprovers: Array<{empId:string;personalStatus:'pending'|'approved'|'rejected'}> = []
      for (const person of candidates) {
        const key = normalizeApprovalEmployee(person)
        if (seen.has(key)) continue
        seen.add(key)
        const approved = includes(list(row.approved_by), person), rejected = includes(list(row.rejected_by), person)
        if (approved && rejected) continue // inconsistent history is not actionable
        const personalStatus = approved ? 'approved' : rejected ? 'rejected' : row.status === 'pending' ? 'pending' : null
        if (personalStatus === 'pending' && (terminalRun(row.run_status) || row.is_latest === 0)) continue
        if (!personalStatus || (filters.personalStatus === 'handled' && personalStatus === 'pending') ||
            (filters.personalStatus && filters.personalStatus !== 'handled' && filters.personalStatus !== personalStatus)) continue
        matchedApprovers.push({empId: all ? person : approverIds.find(id => normalizeApprovalEmployee(id) === key)!, personalStatus})
      }
      if (!matchedApprovers.length) continue
      // An all-person card can contain several different personal decisions.
      const personalStatus = all ? null : matchedApprovers[0].personalStatus
      const hasPendingApprover = matchedApprovers.some(person => person.personalStatus === 'pending')
      if (skip > 0) { skip--; continue }
      if (items.length === limit) { more = true; break }
      items.push({ cardId: row.id, flowId: row.flow_id, nodeId: row.node_id, workflowId: row.workflow_id,
        workflowTitle: row.workflow_title, message: row.message, approverIds, status: row.status, personalStatus, ...(all ? {matchedApprovers} : {}), actionability: !hasPendingApprover ? 'none' : activeApprovalRun(row.run_status) && row.is_latest === 1 ? 'available' : 'unknown', createdAt: Number(row.created_at) })
      last = before
    }
    if (more || batch.length < 200) break
  }
  return { items, nextCursor: more && last ? Buffer.from(JSON.stringify({ ...last, key: queryKey })).toString('base64url') : null }
}
