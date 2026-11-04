import { Router, type Request, type Response, type NextFunction } from 'express'
import { invalidateResolvedCache } from './internal/approval-cards.js'
import { parseApprovalCardContent } from '../../shared/approval-display.js'
import { ApprovalWorkbenchError, activeApprovalRun, normalizeApprovalEmployee, searchApprovalWorkbench, type ApprovalPrincipal, type ApprovalWorkbenchFilters, type ApprovalWorkbenchStore } from '../services/approval-workbench.js'

/** The composition root must resolve identity and admin role from verified sources. */
export function createApprovalWorkbenchRouter(store: ApprovalWorkbenchStore, resolvePrincipal: (req: Request) => Promise<ApprovalPrincipal | null>, options: { publicBaseUrl?: string; allowPost?: boolean; allowGet?: boolean } = {}): Router {
  const router = Router()
  // The host's global CORS policy admits other internal sites. Approval data is
  // sensitive, so browser reads must not be exposed cross-origin with cookies.
  const requireSameOriginRead = (req: Request, res: Response, next: NextFunction) => {
    const origin = req.get('origin'), fetchSite = req.get('sec-fetch-site')
    let expectedOrigin = ''
    try { if (options.publicBaseUrl) expectedOrigin = new URL(options.publicBaseUrl).origin } catch { /* fail closed for Origin requests */ }
    if ((origin && (!expectedOrigin || origin !== expectedOrigin)) ||
        (fetchSite && fetchSite !== 'same-origin' && fetchSite !== 'none')) {
      res.status(403).json({message:'审批请求来源不可信'}); return
    }
    next()
  }
  const search = async (req: Request, res: Response) => {
    res.setHeader('Cache-Control', 'no-store')
    try {
      const principal = await resolvePrincipal(req)
      if (!principal) { res.status(401).json({code:'UNAUTHENTICATED',message:'请先登录'}); return }
      const input = req.method === 'POST' ? req.body : req.query
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', '无效请求参数')
      const stringParam = (key: string) => {
        const value = input[key]
        if (value == null) return undefined
        if (typeof value === 'number' && Number.isFinite(value) && ['limit','start','end'].includes(key)) return String(value)
        if (typeof value !== 'string' || !value.trim() || value.length > 256) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', `无效参数: ${key}`)
        return value.trim()
      }
      const numberParam = (key: string) => {
        const text = stringParam(key)
        if (text == null) return undefined
        const value = Number(text)
        if (!Number.isFinite(value)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', `无效参数: ${key}`)
        return value
      }
      const dateParam = (key: string) => {
        const text = stringParam(key)
        if (text == null) return undefined
        if (/^\d+$/.test(text)) return Number(text)
        if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(text)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', `无效日期: ${key}`)
        const value = Date.parse(text)
        if (!Number.isFinite(value)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT', `无效日期: ${key}`)
        return Math.ceil(value / 1000)
      }
      const status = stringParam('personalStatus')
      if (status && !['pending','handled','approved','rejected'].includes(status)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT','无效审批状态')
      const cardStatus = stringParam('status')
      if (cardStatus && !['pending','approved','rejected'].includes(cardStatus)) throw new ApprovalWorkbenchError('INVALID_ARGUMENT','无效卡片状态')
      const scope = stringParam('scope')
      if (scope && scope !== 'all') throw new ApprovalWorkbenchError('INVALID_ARGUMENT','无效查询范围')
      const filters: ApprovalWorkbenchFilters = { scope:scope as ApprovalWorkbenchFilters['scope'], empId:stringParam('empId'),workflowId:stringParam('workflowId'),flowId:stringParam('flowId'),
        personalStatus:status as ApprovalWorkbenchFilters['personalStatus'], status:cardStatus as ApprovalWorkbenchFilters['status'], createdFrom:dateParam('createdFrom'),createdTo:dateParam('createdTo'),limit:numberParam('limit'),cursor:stringParam('cursor'),start:numberParam('start'),end:numberParam('end') }
      const result = await searchApprovalWorkbench(store, principal, filters)
      const base = (options.publicBaseUrl ?? '').replace(/\/+$/, '')
      res.json({ ...result, ...(scope === 'all' ? {scope: 'all'} : {}), page: { returned: result.items.length, hasMore: Boolean(result.nextCursor), nextCursor: result.nextCursor },
        items:result.items.map(item=>({...item,createdAt:new Date(item.createdAt * 1000).toISOString(),approvalUrl:`${base}/approval/${item.cardId}`})) })
    } catch (error) {
      if (error instanceof ApprovalWorkbenchError) { res.status(error.code === 'FORBIDDEN' ? 403 : 400).json({code:error.code,message:error.message}); return }
      console.error('[approval-workbench] query failed')
      res.status(503).json({code:'UNAVAILABLE',message:'审批查询暂不可用'})
    }
  }
  if (options.allowGet !== false) router.get('/search', requireSameOriginRead, search)
  if (options.allowPost) router.post('/search', search)
  if (options.allowGet !== false) router.get('/workbench/:id', requireSameOriginRead, async (req, res) => {
    res.setHeader('Cache-Control', 'no-store')
    try {
      const principal = await resolvePrincipal(req)
      if (!principal) { res.status(401).json({message:'请先登录'}); return }
      const id = Number(req.params.id)
      if (!Number.isSafeInteger(id) || id <= 0) { res.status(400).json({message:'无效审批 ID'}); return }
      const card = await store.findById?.(id)
      if (!card) { res.status(404).json({message:'审批记录未找到'}); return }
      const ids = (value: string | null) => (value ?? '').split(',').map(v=>v.trim()).filter(Boolean)
      const approverIds = ids(card.approver_ids), approvedBy = ids(card.approved_by), rejectedBy = ids(card.rejected_by)
      const hasActor = (values: string[]) => values.some(v=>normalizeApprovalEmployee(v)===normalizeApprovalEmployee(principal.userId))
      const isApprover = hasActor(approverIds)
      if (!isApprover && !principal.isAdmin) { res.status(403).json({message:'无权查看该审批'}); return }
      const runStatus = await store.findRunStatus?.(card.flow_id) ?? null
      const latestCardId = await store.findLatestCardId?.(card.flow_id, card.node_id) ?? null
      const {fields:cardFields, sections, display} = parseApprovalCardContent(card.card_fields_json)
      let note: string | undefined, detail: unknown
      try { const parsed = card.comment ? JSON.parse(card.comment) : null; note = parsed?.note; detail = parsed?.detail } catch { /* ordinary text comment */ }
      res.json({id:card.id,flowId:card.flow_id,nodeId:card.node_id,workflowId:card.workflow_id,workflowTitle:card.workflow_title,
        approvalType:card.approval_type,message:card.message,cardFields,sections,display,approverIds,
        approverNames:ids(card.approver_names),approvalPolicy:card.approval_policy,approvedBy,rejectedBy,status:card.status,
        deliveryMode:card.delivery_mode,createdAt:card.created_at,resolvedAt:card.resolved_at,comment:card.comment,note,detail,
        runStatus, canAct:isApprover && latestCardId === id && activeApprovalRun(runStatus) && card.status==='pending' && !hasActor(approvedBy) && !hasActor(rejectedBy)})
    } catch { res.status(503).json({message:'审批详情暂不可用'}) }
  })
  if (options.allowGet !== false) router.post('/workbench/:id/resolve', async (req, res) => {
    res.setHeader('Cache-Control', 'no-store')
    try {
      const expectedOrigin = options.publicBaseUrl ? new URL(options.publicBaseUrl).origin : ''
      if (!expectedOrigin || req.get('origin') !== expectedOrigin || (req.get('sec-fetch-site') && req.get('sec-fetch-site') !== 'same-origin')) {
        res.status(403).json({message:'审批请求来源不可信'}); return
      }
      const principal = await resolvePrincipal(req)
      if (!principal) { res.status(401).json({message:'请先登录'}); return }
      const id = Number(req.params.id), { action, comment, detail } = req.body ?? {}
      if (!Number.isSafeInteger(id) || id <= 0 || (action !== 'approve' && action !== 'reject') ||
          (comment != null && (typeof comment !== 'string' || Buffer.byteLength(comment, 'utf8') > 10000)) ||
          (detail != null && (typeof detail !== 'object' || Array.isArray(detail) || Buffer.byteLength(JSON.stringify(detail), 'utf8') > 64000))) {
        res.status(400).json({message:'无效审批参数'}); return
      }
      if (!store.resolveActive) { res.status(503).json({message:'审批服务暂不可用'}); return }
      const storedComment = detail && Object.keys(detail).length ? JSON.stringify({note:comment ?? '',detail}) : comment ?? undefined
      const result = await store.resolveActive(id, principal.userId, action, storedComment)
      if (!result.ok) {
        const status = result.error === '您不是此审批的授权审批人' ? 403 : result.error === '审批记录未找到' ? 404 : 409
        res.status(status).json({message:result.error ?? '审批未完成'}); return
      }
      invalidateResolvedCache()
      res.json({ ...result, action })
    } catch { res.status(503).json({message:'审批结果未确认，请刷新后查看'}) }
  })
  return router
}
