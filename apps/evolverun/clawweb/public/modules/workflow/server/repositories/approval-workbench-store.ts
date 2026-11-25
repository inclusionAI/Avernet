import type { IDatabase } from '@avernet/clawweb-shared/server/db'
import { ApprovalCardRepository, type ApprovalCardRow } from './approval-card-repository.js'
import { activeApprovalRun, normalizeApprovalEmployee, type ApprovalWorkbenchCard, type ApprovalWorkbenchStore } from '../services/approval-workbench.js'

/** Keyset scan. Personal matching happens before the service applies a page limit. */
export function createApprovalWorkbenchStore(db: IDatabase): ApprovalWorkbenchStore {
  return {
    findById: (id) => new ApprovalCardRepository(db).findById(id),
    async findLatestCardId(flowId, nodeId) { const rows = await db.query<{id:number}>('SELECT id FROM approval_cards WHERE flow_id = ? AND node_id = ? ORDER BY id DESC LIMIT 1', [flowId,nodeId]); return rows[0] ? Number(rows[0].id) : null },
    async findRunStatus(flowId) { const rows = await db.query<{status:string}>('SELECT status FROM flow_runs WHERE flow_id = ? LIMIT 1', [flowId]); return rows[0]?.status ?? null },
    async resolveActive(id, actorId, action, comment) {
      // Pre-read only to locate the coordination row. All facts are re-read inside
      // the transaction after locking the run and then the card, in that order.
      const location = (await db.query<{flow_id:string;node_id:string}>('SELECT flow_id, node_id FROM approval_cards WHERE id = ?', [id]))[0]
      const fail = (error: string) => ({ ok:false, status:'pending' as const, approvedBy:[], rejectedBy:[], error })
      if (!location) return fail('审批记录未找到')
      return db.transaction(async tx => {
        const repo = new ApprovalCardRepository(tx)
        const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : ''
        const run = await tx.query<{status:string}>(`SELECT status FROM flow_runs WHERE flow_id = ? LIMIT 1${lock}`, [location.flow_id])
        const card = (await tx.query<ApprovalCardRow>(`SELECT * FROM approval_cards WHERE id = ?${lock}`, [id]))[0]
        if (!card) return fail('审批记录未找到')
        if (card.flow_id !== location.flow_id || card.node_id !== location.node_id) return fail('审批状态已变更，请刷新后重试')
        if (!activeApprovalRun(run[0]?.status)) return fail('工作流已结束或状态无法确认')
        const latest = await tx.query<{id:number}>('SELECT id FROM approval_cards WHERE flow_id = ? AND node_id = ? ORDER BY id DESC LIMIT 1', [card.flow_id,card.node_id])
        if (Number(latest[0]?.id) !== id) return fail('该审批已过期，请刷新工作台')
        const hasActor = (csv: string | null) => repo.parseList(csv).some(value => normalizeApprovalEmployee(value) === normalizeApprovalEmployee(actorId))
        if (repo.isApprover(card, actorId) && (hasActor(card.approved_by) || hasActor(card.rejected_by))) return fail('您已处理此审批，请刷新工作台')
        return repo.recordAction(id, actorId, action, comment)
      })
    },
    async scan(before, limit, candidate) {
      // INSTR is only a broad candidate filter; exact normalized membership is
      // verified by the service before pagination (e.g. 123 must not match 9123).
      const clauses = ['a.delivery_mode = ?']
      const params: unknown[] = ['card-web']
      if (candidate.empId) { clauses.push('INSTR(a.approver_ids, ?) > 0'); params.push(candidate.empId) }
      if (candidate.workflowId) { clauses.push('a.workflow_id = ?'); params.push(candidate.workflowId) }
      if (candidate.flowId) { clauses.push('a.flow_id = ?'); params.push(candidate.flowId) }
      if (candidate.status) { clauses.push('a.status = ?'); params.push(candidate.status) }
      if (candidate.createdFrom != null) { clauses.push('a.created_at >= ?'); params.push(candidate.createdFrom) }
      if (candidate.createdTo != null) { clauses.push('a.created_at < ?'); params.push(candidate.createdTo) }
      if (before) { clauses.push('(a.created_at < ? OR (a.created_at = ? AND a.id < ?))'); params.push(before.createdAt, before.createdAt, before.id) }
      params.push(limit)
      return db.query<ApprovalWorkbenchCard>(`SELECT a.id, a.flow_id, a.node_id, a.workflow_id, a.workflow_title, a.message, a.approver_ids, a.approved_by, a.rejected_by, a.status, a.delivery_mode, a.created_at, r.status AS run_status, CASE WHEN EXISTS (SELECT 1 FROM approval_cards newer WHERE newer.flow_id = a.flow_id AND newer.node_id = a.node_id AND newer.id > a.id) THEN 0 ELSE 1 END AS is_latest FROM approval_cards a LEFT JOIN flow_runs r ON r.flow_id = a.flow_id WHERE ${clauses.join(' AND ')} ORDER BY a.created_at DESC, a.id DESC LIMIT ?`, params)
    },
  }
}
