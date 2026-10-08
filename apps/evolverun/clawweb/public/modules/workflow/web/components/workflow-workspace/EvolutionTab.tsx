import { useEffect, useMemo, useRef, useState } from 'react'
import {
  useEvolveSuggestions,
  useRecordSuggestionAction,
  useSuggestionApplyTasks,
  useWorkflowAccess,
} from '../../api/hooks'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import { aggregateDiagnoses, timeValue } from './evolution-utils'
import { groupDiagnoses, useIssueGroups } from './issue-groups'
import IssueDetailDrawer, { ApplyTaskStatusBadge, SuggestionActions, SUGGESTION_STATUS } from './IssueDetailDrawer'
import { repairBatches } from '../../api/repair-batches'
import { repairReadError } from '../../api/repair-read-error'
import IssueIdentity from './IssueIdentity'
import { repairSignatureKey, type RepairCandidatesResponse, type RepairInboxFilter, type RepairInboxItem } from '../../../server/contracts/repair-workbench'
import RepairItems, { RepairStateCounts } from './repair-batch/RepairItems'
import RepairItemDetail from './repair-batch/RepairItemDetail'
import DetailDrawer from './DetailDrawer'
import RepairTaskDialog from './repair-batch/RepairTaskDialog'
import { exclusion, itemTitle, phases, primary, RepairDialog } from './repair-batch/repair-view'
import Pagination from '../Pagination'
import ApplySuggestionModal from './ApplySuggestionModal'
import RemediesPanel from './RemediesPanel'

export type EvoTab = 'diagnosis' | 'remedies'

const EVO_TABS: { key: EvoTab; label: string }[] = [
  { key: 'diagnosis', label: '问题与优化' },
  { key: 'remedies', label: '可复用经验' },
]

type SuggestionStatus = EvolveSuggestion['status']
type ApplyTaskStatus = SuggestionApplyTask['status']
type IssueState = 'pending' | 'processing' | 'awaiting_verification' | 'closed' | 'no_action' | 'observing'
type RepairViewState = {
  repairOpen: boolean
  stateFilter: IssueState | 'all'
  repairPage: number
  repairPageSize: number
  includeHistorical: boolean
  selectedTaskId: string
}

const ISSUE_STATE_LABELS: Record<IssueState | 'all', string> = {
  all: '全部状态',
  pending: '待处理',
  processing: '处理中',
  awaiting_verification: '待验证',
  closed: '已关闭',
  no_action: '暂不处理',
  observing: '观察中',
}

function readRepairView(workflowId: string): RepairViewState {
  const fallback: RepairViewState = { repairOpen: false, stateFilter: 'all', repairPage: 1, repairPageSize: 20,
    includeHistorical: false, selectedTaskId: '' }
  try {
    const saved = JSON.parse(sessionStorage.getItem(`workflow-repair:${workflowId}`) ?? '{}')
    return {
      repairOpen: saved.repairOpen === true,
      stateFilter: typeof saved.stateFilter === 'string' && saved.stateFilter in ISSUE_STATE_LABELS
        ? saved.stateFilter as IssueState | 'all' : fallback.stateFilter,
      repairPage: Number.isSafeInteger(saved.repairPage) && saved.repairPage > 0 ? saved.repairPage : fallback.repairPage,
      repairPageSize: Number.isSafeInteger(saved.repairPageSize) && saved.repairPageSize > 0 ? saved.repairPageSize : fallback.repairPageSize,
      includeHistorical: saved.includeHistorical === true,
      selectedTaskId: typeof saved.selectedTaskId === 'string' ? saved.selectedTaskId : '',
    }
  } catch { return fallback }
}

function resolveSuggestionStatus(suggestion: EvolveSuggestion, localStatus: Record<string, Exclude<SuggestionStatus, 'pending'>>, task: SuggestionApplyTask | undefined): SuggestionStatus | ApplyTaskStatus {
  const storedStatus = suggestion.status as SuggestionStatus | 'applied'
  const baseStatus = localStatus[suggestion.id] ?? (storedStatus === 'applied' ? 'applied_unverified' : storedStatus) ?? 'pending'
  const taskStatus: SuggestionStatus | undefined = task == null
    ? undefined
    : ['succeeded', 'completed', 'applied_unverified'].includes(task.status)
      ? 'applied_unverified'
      : ['failed', 'canceled'].includes(task.status)
        ? 'failed'
        : ['created', 'pending', 'dispatching', 'dispatched', 'running', 'applying'].includes(task.status)
          ? 'applying'
          : undefined
  return ['verified', 'ineffective'].includes(baseStatus) ? baseStatus : taskStatus ?? baseStatus
}

function issueState(status?: SuggestionStatus | ApplyTaskStatus): IssueState {
  if (!status) return 'observing'
  if (status === 'pending' || status === 'adopted') return 'pending'
  if (['applying', 'dispatching', 'dispatched', 'running', 'created'].includes(status)) return 'processing'
  if (status === 'applied_unverified') return 'awaiting_verification'
  if (status === 'verified' || status === 'ineffective') return 'closed'
  if (status === 'rejected' || status === 'benched') return 'no_action'
  return 'observing'
}

function repairInboxFilter(state: IssueState | 'all'): RepairInboxFilter {
  if (state === 'observing') return 'all'
  return state
}

function DiagnosisPanel({
  workflowId,
  runId,
  analysisId,
  issueSignature,
  suggestions,
  suggestionsLoading,
  localStatus,
  applyTaskMap,
  applyTasks,
  onAction,
  onApply,
  canEdit,
}: {
  workflowId: string
  runId?: string
  analysisId?: string
  issueSignature?: string
  suggestions: EvolveSuggestion[]
  suggestionsLoading: boolean
  localStatus: Record<string, Exclude<SuggestionStatus, 'pending'>>
  applyTaskMap: Record<string, SuggestionApplyTask>
  applyTasks: SuggestionApplyTask[]
  onAction: (id: string, action: Exclude<SuggestionStatus, 'pending'>) => void
  onApply: (ids: string[]) => void
  canEdit: boolean
}) {
  const { data, isLoading, isError, refetch } = useIssueGroups(workflowId)
  const diagnoses = (data?.groups ?? []).flatMap(groupDiagnoses)
  const [initialRepairView] = useState(() => readRepairView(workflowId))
  const [nodeFilter, setNodeFilter] = useState<string>('all')
  const [modeFilter, setModeFilter] = useState<string>('all')
  const [stateFilter, setStateFilter] = useState<IssueState | 'all'>(initialRepairView.stateFilter)
  const [selectedSignature, setSelectedSignature] = useState<string | null>(issueSignature ?? null)
  const [selectedRepairItemId, setSelectedRepairItemId] = useState<string | null>(null)
  const [drawerEntry, setDrawerEntry] = useState<'causes' | 'repairs' | 'evidence' | undefined>()
  const [repairData, setRepairData] = useState<RepairCandidatesResponse | null>(null)
  const [repairOpen, setRepairOpen] = useState(initialRepairView.repairOpen)
  const [repairLoading, setRepairLoading] = useState(false)
  const [repairError, setRepairError] = useState('')
  const [repairRefresh, setRepairRefresh] = useState(0)
  const [repairPage, setRepairPage] = useState(initialRepairView.repairPage)
  const [repairPageSize, setRepairPageSize] = useState(initialRepairView.repairPageSize)
  const [includeHistorical, setIncludeHistorical] = useState(initialRepairView.includeHistorical)
  const [selectionItems, setSelectionItems] = useState<Record<string, RepairInboxItem>>({})
  const [selectionOpen, setSelectionOpen] = useState(false)
  const [selectionNotice, setSelectionNotice] = useState('')
  const [selectedRepairIds, setSelectedRepairIds] = useState<string[]>([])
  const [repairDetails, setRepairDetails] = useState<Record<string, RepairInboxItem>>({})
  const [repairDetailLoading, setRepairDetailLoading] = useState<Record<string, boolean>>({})
  const [repairDetailErrors, setRepairDetailErrors] = useState<Record<string, string>>({})
  const [draftOpen, setDraftOpen] = useState(false)
  const [instructions, setInstructions] = useState('')
  const [repairBusy, setRepairBusy] = useState(false)
  const [repairActionError, setRepairActionError] = useState('')
  const [repairNotice, setRepairNotice] = useState('')
  const [selectedTaskId, setSelectedTaskId] = useState(initialRepairView.selectedTaskId)
  const [disposition, setDisposition] = useState<{ item: RepairInboxItem; action: 'no_action' | 'restore' } | null>(null)
  const [dispositionReason, setDispositionReason] = useState('')
  const initializedDigest = useRef('')
  const requestId = useRef('')
  const requestPayloadKey = useRef('')
  const requestSequence = useRef(0)
  const detailRequests = useRef(new Set<string>())
  const detailEpoch = useRef(0)
  const selectedRepairState = repairInboxFilter(stateFilter)

  useEffect(() => {
    try { sessionStorage.setItem(`workflow-repair:${workflowId}`, JSON.stringify({
      repairOpen, stateFilter, repairPage, repairPageSize, includeHistorical, selectedTaskId,
    })) } catch { /* Session storage can be disabled. */ }
  }, [workflowId, repairOpen, stateFilter, repairPage, repairPageSize, includeHistorical, selectedTaskId])

  useEffect(() => {
    if (!repairOpen) return
    let current = true
    setRepairLoading(true)
    repairBatches.candidates(workflowId, { state: selectedRepairState, page: repairPage, pageSize: repairPageSize, includeHistorical }).then(result => {
      if (!current) return
      setRepairData(result)
      setRepairError('')
      const firstLoad = !initializedDigest.current
      const changed = !firstLoad && initializedDigest.current !== result.inputDigest
      if (firstLoad) {
        setSelectionNotice('默认不选择建议。请明确勾选本次修复范围；翻页不会自动增选。')
      } else if (changed) {
        setSelectedRepairIds([])
        setDraftOpen(false)
        detailEpoch.current += 1
        detailRequests.current.clear()
        setRepairDetails({})
        setRepairDetailLoading({})
        setRepairDetailErrors({})
        setSelectedRepairItemId(null)
        setSelectionNotice('建议来源已更新，原选择已清空，请重新确认范围。')
      } else {
        const unavailable = new Set(result.items.filter(item => exclusion(item)).map(item => item.itemId))
        setSelectedRepairIds(previous => previous.filter(id => !unavailable.has(id)))
      }
      setSelectionItems(previous => ({ ...(changed ? {} : previous), ...Object.fromEntries(result.items.map(item => [item.itemId, item])) }))
      initializedDigest.current = result.inputDigest
    }).catch(error => {
      if (!current) return
      setRepairError(repairReadError(error))
      setRepairData(null)
      setSelectedRepairIds([])
      setSelectionItems({})
      setSelectedRepairItemId(null)
      setDraftOpen(false)
      setDisposition(null)
      detailEpoch.current += 1
      detailRequests.current.clear()
      setRepairDetails({}); setRepairDetailLoading({}); setRepairDetailErrors({})
    }).finally(() => { if (current) setRepairLoading(false) })
    return () => { current = false }
  }, [workflowId, repairOpen, repairRefresh, repairPage, repairPageSize, includeHistorical, selectedRepairState, canEdit])

  const clusters = aggregateDiagnoses(diagnoses)
  for (const cluster of clusters) cluster.aggregation = data?.groups.find(group => group.signature === cluster.signature)
  const nodes = Array.from(new Set(clusters.map((cluster) => cluster.node))).sort()
  const modes = Array.from(new Set(clusters.map((cluster) => cluster.mode).filter(Boolean)))
  const suggestionBySignature = new Map(suggestions.map((suggestion) => {
    const status = resolveSuggestionStatus(suggestion, localStatus, applyTaskMap[suggestion.id])
    return [suggestion.signature, { ...suggestion, status }] as const
  }))
  const repairItems = repairData?.items ?? []
  const repairBySignature = new Map<string, RepairInboxItem[]>()
  for (const item of repairItems) {
    const signature = typeof item.context?.signature === 'string' ? item.context.signature : ''
    if (!signature) continue
    repairBySignature.set(signature, [...(repairBySignature.get(signature) ?? []), item])
  }
  const repairSignatures = new Set(repairBySignature.keys())
  const clusterSignatures = new Set(clusters.map(cluster => cluster.signature))
  const standaloneSuggestions = suggestions
    .filter(suggestion => !clusterSignatures.has(suggestion.signature) && !repairSignatures.has(suggestion.signature))
    .map(suggestion => ({ ...suggestion, status: resolveSuggestionStatus(suggestion, localStatus, applyTaskMap[suggestion.id]) }))
  const standaloneRepairItems = repairItems.filter(item => {
    const signature = typeof item.context?.signature === 'string' ? item.context.signature : ''
    return !signature || !clusterSignatures.has(signature)
  })
  const enriched = clusters.map((cluster) => {
    const suggestion = suggestionBySignature.get(cluster.signature)
    const runIds = Array.from(new Set([
      ...cluster.runIds,
    ]))
    const instances = [
      ...cluster.instances,
      ...runIds.filter((flowId) => !cluster.instances.some((instance) => instance.flowId === flowId)).map((flowId) => ({
        analysisId: 'legacy',
        diagnosisId: `related:${flowId}`,
        flowId,
        occurredAtMs: timeValue(cluster.latest.gmt_create),
        diagnosis: cluster.latest,
      })),
    ].filter((instance, index, all) => all.findIndex(item => item.flowId === instance.flowId) === index)
    return {
      cluster: { ...cluster, runIds, instances },
      suggestion,
      repairItems: repairBySignature.get(cluster.signature) ?? [],
      state: issueState(suggestion?.status),
    }
  })
  const repairBackedSignatureKeys = new Set(repairData?.repairSignatureKeys ?? [])
  const filtered = enriched.filter(({ cluster, state }) =>
    (nodeFilter === 'all' || cluster.node === nodeFilter)
    && (modeFilter === 'all' || cluster.mode === modeFilter)
    && (repairOpen || stateFilter === 'all' || stateFilter === state))
  const pendingCount = repairData?.counts.pending ?? (repairOpen ? '—' : enriched.filter((item) => item.state === 'pending').length)
  const processingCount = repairData?.counts.processing ?? (repairOpen ? '—' : enriched.filter((item) => item.state === 'processing').length)
  const verifyingCount = repairData?.counts.awaiting_verification ?? (repairOpen ? '—' : enriched.filter((item) => item.state === 'awaiting_verification').length)
  const closedCount = repairData?.counts.closed ?? (repairOpen ? '—' : enriched.filter((item) => item.state === 'closed').length)
  const noActionCount = repairData?.counts.no_action ?? (repairOpen ? '—' : 0)
  const activeRepairTask = repairData?.tasks.find(task => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase))
  const selectedIssue = enriched.find(({ cluster }) => cluster.signature === selectedSignature)
  const selectedRepairItem = (selectedRepairItemId ? repairItems.find(item => item.itemId === selectedRepairItemId) ?? selectionItems[selectedRepairItemId] : undefined)
    ?? selectedIssue?.repairItems[0]
  const openRepairDetail = (item: RepairInboxItem) => {
    const signature = typeof item.context?.signature === 'string' ? item.context.signature : null
    setSelectedSignature(signature && clusterSignatures.has(signature) ? signature : null)
    setSelectedRepairItemId(item.itemId)
    setDrawerEntry('repairs')
  }
  const closeDetail = () => { setSelectedSignature(null); setSelectedRepairItemId(null) }
  const selectableFilteredIds = [...filtered.flatMap(({ repairItems: groupItems }) => groupItems), ...standaloneRepairItems]
    .filter(item => !exclusion(item)).map(item => item.itemId)
  const allFilteredSelected = selectableFilteredIds.length > 0 && selectableFilteredIds.every(id => selectedRepairIds.includes(id))
  const toggleRepairItem = (id: string) => setSelectedRepairIds(current => current.includes(id)
    ? current.filter(item => item !== id)
    : current.length < Math.min(100, repairData?.limits.maxItems ?? 100) ? [...current, id] : current)
  const toggleAllFiltered = () => setSelectedRepairIds(current => allFilteredSelected
    ? current.filter(id => !selectableFilteredIds.includes(id))
    : [...new Set([...current, ...selectableFilteredIds])].slice(0, Math.min(100, repairData?.limits.maxItems ?? 100)))
  const resetRequestId = () => { requestId.current = ''; requestPayloadKey.current = '' }
  const changeHistoryScope = (next: boolean) => {
    initializedDigest.current = ''
    resetRequestId()
    setRepairData(null)
    setSelectedRepairIds([])
    setSelectionItems({})
    setSelectionNotice('历史范围已改变，原选择已清空；请重新勾选需要处理的建议。')
    setSelectedRepairItemId(null)
    setRepairDetails({})
    setRepairDetailLoading({})
    setRepairDetailErrors({})
    detailRequests.current.clear()
    detailEpoch.current += 1
    setDraftOpen(false)
    setDisposition(null)
    setRepairActionError('')
    setRepairPage(1)
    setIncludeHistorical(next)
  }
  const nextRequestId = (payload: unknown) => {
    const payloadKey = JSON.stringify(payload)
    if (!requestId.current || requestPayloadKey.current !== payloadKey) {
      requestSequence.current += 1
      requestId.current = globalThis.crypto?.randomUUID?.() ?? `repair-${Date.now()}-${requestSequence.current}`
      requestPayloadKey.current = payloadKey
    }
    return requestId.current
  }
  const loadRepairDetail = async (itemId: string) => {
    if (repairDetails[itemId] || detailRequests.current.has(itemId)) return
    const epoch = detailEpoch.current
    detailRequests.current.add(itemId)
    setRepairDetailLoading(current => ({ ...current, [itemId]: true }))
    setRepairDetailErrors(current => ({ ...current, [itemId]: '' }))
    try {
      const detail = await repairBatches.item(workflowId, itemId)
      if (epoch === detailEpoch.current) setRepairDetails(current => ({ ...current, [itemId]: detail }))
    } catch (error) {
      if (epoch === detailEpoch.current) setRepairDetailErrors(current => ({ ...current, [itemId]: error instanceof Error ? error.message : String(error) }))
    } finally {
      if (epoch === detailEpoch.current) {
        detailRequests.current.delete(itemId)
        setRepairDetailLoading(current => ({ ...current, [itemId]: false }))
      }
    }
  }
  const submitDraft = async () => {
    if (!repairData || !selectedRepairIds.length || repairBusy) return
    const input = { workflowId, itemIds: selectedRepairIds, inputDigest: repairData.inputDigest,
      instructions, includeHistorical: repairData.includeHistorical }
    const payload = { ...input, requestId: nextRequestId(input) }
    if (new TextEncoder().encode(JSON.stringify(payload)).length > repairData.limits.maxRequestBytes) {
      setRepairActionError('请求内容过大，请缩短说明或减少选择。')
      return
    }
    setRepairBusy(true); setRepairActionError('')
    try {
      const result = await repairBatches.create(payload)
      setSelectedRepairIds([])
      setDraftOpen(false); setRepairNotice(`已创建 ${result.taskId}，AIS 将生成可审阅 Pack 草稿，不会自动部署。`)
      resetRequestId(); setRepairRefresh(value => value + 1)
    } catch (error) {
      setRepairActionError(`生成请求状态未确认，请刷新任务记录后再决定是否重试：${error instanceof Error ? error.message : String(error)}`)
    } finally { setRepairBusy(false) }
  }
  const submitDisposition = async () => {
    if (!repairData || !disposition || !dispositionReason.trim() || repairBusy) return
    const input = { workflowId, inputDigest: repairData.inputDigest,
      expectedStateVersion: disposition.item.stateVersion, contentRevision: disposition.item.contentRevision,
      action: disposition.action, reason: dispositionReason.trim(), includeHistorical: repairData.includeHistorical }
    setRepairBusy(true); setRepairActionError('')
    try {
      await repairBatches.disposition(disposition.item.itemId, { ...input, requestId: nextRequestId(input) })
      if (disposition.action === 'no_action') setSelectedRepairIds(current => current.filter(id => id !== disposition.item.itemId))
      closeDetail()
      setSelectionItems(current => {
        const next = { ...current }
        delete next[disposition.item.itemId]
        return next
      })
      // State transitions keep the source digest; discard stale details and in-flight reads explicitly.
      detailEpoch.current += 1
      detailRequests.current.clear()
      setRepairDetails({}); setRepairDetailLoading({}); setRepairDetailErrors({})
      setDisposition(null); setDispositionReason(''); resetRequestId(); setRepairRefresh(value => value + 1)
    } catch (error) { setRepairActionError(error instanceof Error ? error.message : String(error)) }
    finally { setRepairBusy(false) }
  }

  const repairContent = <>
    {selectedIssue && selectedIssue.repairItems.length > 0 && <label className="mb-4 block text-xs font-medium text-slate-600">本页建议
      <select aria-label="切换建议" value={selectedRepairItem?.itemId ?? ''} onChange={event => setSelectedRepairItemId(event.target.value)}
        className="mt-2 w-full rounded-lg border border-slate-200 bg-white p-2 text-sm">
        {selectedRepairItem && !selectedIssue.repairItems.some(item => item.itemId === selectedRepairItem.itemId) && <option value={selectedRepairItem.itemId}>{itemTitle(selectedRepairItem)}（其他页）</option>}
        {selectedIssue.repairItems.map(item => <option key={item.itemId} value={item.itemId}>{itemTitle(item)}</option>)}
      </select>
    </label>}
    {selectedRepairItem ? <RepairItemDetail key={`${selectedRepairItem.itemId}:${initializedDigest.current}`} item={selectedRepairItem}
      detail={repairDetails[selectedRepairItem.itemId]} loading={repairDetailLoading[selectedRepairItem.itemId]} error={repairDetailErrors[selectedRepairItem.itemId]}
      onLoad={itemId => void loadRepairDetail(itemId)} selected={selectedRepairIds.includes(selectedRepairItem.itemId)} onToggle={toggleRepairItem}
      canEdit={canEdit && repairData?.canEdit === true && !repairLoading && !repairError && !repairBusy}
      limitReached={selectedRepairIds.length >= Math.min(100, repairData?.limits.maxItems ?? 100)}
      onDisposition={(item, action) => { resetRequestId(); setRepairActionError(''); setDisposition({ item, action }); setDispositionReason('') }} />
      : !repairOpen ? <button type="button" className={primary} onClick={() => setRepairOpen(true)}>进入修复处理</button>
        : <p className="text-xs text-slate-500">{repairLoading ? '正在加载修复建议…' : repairError || '本页没有该问题的建议；请翻阅建议分页，历史分析仍可查看。'}</p>}
  </>

  return (
    <div className="space-y-3">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold text-slate-900">从问题到效果验证</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">问题列表持续保留；进入修复处理后才加载候选项并生成可审阅 Pack 草稿。</p></div>
          {!repairOpen && <button type="button" className={primary} onClick={() => setRepairOpen(true)}>进入修复处理</button>}
        </div>
      </div>

      {isLoading && <p role="status" className="rounded-lg bg-slate-50 p-4 text-xs text-slate-500">正在加载问题摘要；修复建议和任务独立加载。</p>}
      {isError && <div role="alert" className="rounded-lg bg-red-50 p-4 text-xs text-red-600">问题分组加载失败，不能显示为没有问题。<button type="button" onClick={() => void refetch()} className="ml-2 underline">重试问题</button></div>}

      {repairOpen && repairLoading && <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">修复任务与处理状态仍在加载，问题列表可继续查看。</p>}
      {repairOpen && repairError && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{repairError}<button type="button" className="ml-2 underline" onClick={() => setRepairRefresh(value => value + 1)}>重试</button></p>}
      {suggestionsLoading && <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">历史建议仍在加载，不影响查看问题与修复任务。</p>}
      {repairNotice && <p role="status" className="rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700">{repairNotice}</p>}

      {repairData && <section aria-label="处理任务" className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">处理任务</h3>
            <p className="mt-1 text-xs text-slate-500">任务不会替代问题列表；旧批次、新建议和历史状态始终可见。</p>
          </div>
          <button type="button" className={primary} disabled={!canEdit || !repairData.canEdit || !repairData.capabilities.generation || !selectedRepairIds.length || !!activeRepairTask || repairBusy || repairLoading || !!repairError}
            onClick={() => { resetRequestId(); setRepairActionError(''); setDraftOpen(true) }}>
            生成 Pack 草稿（{selectedRepairIds.length}）
          </button>
        </div>
        {!repairData.capabilities.generation && <div className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
          <p>修复草稿生成服务尚不可用。可以查看和选择建议，但暂时不能生成 Pack。</p>
          {repairData.capabilities.reason && <details className="mt-2"><summary className="cursor-pointer">技术原因</summary>{repairData.capabilities.reason}</details>}
        </div>}
        {activeRepairTask && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">已有可继续的修复任务，请先在现有任务中审阅、反馈或取消。</p>}
        <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50/40 p-3 text-xs text-slate-600">
          <div className="flex flex-wrap items-center gap-3">
            <strong className="text-blue-800">已选 {selectedRepairIds.length} 项（跨页保留）</strong>
            <button type="button" className="text-blue-700 underline" disabled={!selectedRepairIds.length} onClick={() => setSelectionOpen(true)}>查看已选</button>
            <button type="button" className="text-blue-700 underline" disabled={!selectedRepairIds.length || repairBusy} onClick={() => setSelectedRepairIds([])}>清空选择</button>
            <button type="button" className="text-blue-700 underline" disabled={!canEdit || !repairData.canEdit || repairLoading || !selectableFilteredIds.length}
              onClick={toggleAllFiltered}>{allFilteredSelected ? '取消本页选择' : `选择本页可处理建议（${selectableFilteredIds.length}）`}</button>
          </div>
          <p className="mt-2 leading-5">{selectionNotice}</p>
          {selectedRepairIds.some(id => !selectableFilteredIds.includes(id)) && <p className="mt-1">其中 {selectedRepairIds.filter(id => !selectableFilteredIds.includes(id)).length} 项不在当前可见范围，仍会纳入草稿。</p>}
        </div>
        <label className="mt-3 flex items-center gap-2 text-xs text-slate-600">
          <input type="checkbox" aria-label="包含历史未复现" checked={includeHistorical} disabled={repairBusy}
            onChange={event => changeHistoryScope(event.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-blue-600" />
          包含历史未复现
        </label>
        <div className="mt-3 space-y-2">
          {repairData.tasks.length ? repairData.tasks.map(task => <div key={task.taskId} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs">
            <span className="break-all font-medium text-slate-700">{task.taskId} · v{task.revision} · {phases[task.phase] ?? task.phase}</span>
            <span className="text-slate-500">{task.itemCount} 项 · {new Date(task.updatedAtMs).toLocaleString()}</span>
            <button type="button" className="font-medium text-blue-600 hover:text-blue-800" onClick={() => setSelectedTaskId(task.taskId)}>查看任务</button>
          </div>) : <p className="text-xs text-slate-500">暂无修复任务。可从下面的问题列表选择待处理项。</p>}
        </div>
      </section>}

      {!isLoading && !isError && diagnoses.length === 0 && standaloneSuggestions.length === 0 && standaloneRepairItems.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-xs text-slate-500">
          当前工作流暂无已记录异常。任务护航会分析失败运行，也会保留成功运行中的异常和退化信号。
        </div>
      )}

      {diagnoses.length > 0 && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-200 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span><strong className="mr-1 text-sm font-semibold text-slate-900">{clusters.length}</strong>问题</span>
            {repairData && <span>修复建议：</span>}
            {repairOpen && !repairData && <span className="text-slate-600">{repairLoading ? '处理状态加载中' : '处理状态不可用'}</span>}
            <span><strong className="mr-1 text-sm font-semibold text-amber-700">{pendingCount}</strong>待处理</span>
            <span><strong className="mr-1 text-sm font-semibold text-blue-700">{processingCount}</strong>处理中</span>
            <span><strong className="mr-1 text-sm font-semibold text-blue-700">{verifyingCount}</strong>待验证</span>
            <span><strong className="mr-1 text-sm font-semibold text-emerald-700">{closedCount}</strong>已关闭</span>
            <span><strong className="mr-1 text-sm font-semibold text-slate-600">{noActionCount}</strong>暂不处理</span>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canEdit && repairData?.canEdit && selectableFilteredIds.length > 0 && <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <input
                type="checkbox"
                aria-label="选择当前筛选下全部待处理项"
                checked={allFilteredSelected}
                disabled={repairLoading || repairBusy}
                onChange={toggleAllFiltered}
                className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600"
              />
              选择本页可处理建议（{selectableFilteredIds.length}）
            </label>}
            <select
              aria-label={repairOpen ? '建议状态' : '问题状态'}
              value={repairOpen && stateFilter === 'observing' ? 'all' : stateFilter}
              onChange={(e) => {
                setRepairPage(1)
                setStateFilter(e.target.value as IssueState | 'all')
              }}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              {Object.entries(ISSUE_STATE_LABELS).filter(([value]) => !repairOpen || value !== 'observing').map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <select
              aria-label="问题节点"
              value={nodeFilter}
              onChange={(e) => setNodeFilter(e.target.value)}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              <option value="all">全部节点</option>
              {nodes.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <select
              aria-label="问题模式"
              value={modeFilter}
              onChange={(e) => setModeFilter(e.target.value)}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              <option value="all">全部模式</option>
              {modes.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <span className="pl-1 text-[11px] text-slate-400">显示 {filtered.length} / {clusters.length} 个问题</span>
          </div>
        </div>

        {filtered.length === 0 && <div className="px-4 py-10 text-center text-xs text-slate-400">没有符合当前筛选条件的问题</div>}

        <div className="divide-y divide-slate-100">
          {filtered.map(({ cluster, suggestion, repairItems: groupItems }) => {
          const status = suggestion ? SUGGESTION_STATUS[suggestion.status] ?? SUGGESTION_STATUS.pending : null
          const summary = cluster.aggregation?.summary?.summary ?? '聚合结论尚未生成，查看单次运行分析。'
          const task = suggestion ? applyTaskMap[suggestion.id] : undefined
          const offPageRepair = repairOpen && !groupItems.length && repairBackedSignatureKeys.has(repairSignatureKey(cluster.signature))
          return <article key={cluster.signature} data-layout="compact-issue-row" className="px-4 py-3 transition-colors hover:bg-slate-50/70">
            <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <IssueIdentity node={cluster.node} mode={cluster.mode} />
                  {cluster.aggregation?.stale && <span tabIndex={0} title="当前保留上次生成的摘要，尚未覆盖最新分析。请在问题详情核对摘要依据和当前关联运行。" className="rounded bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800">摘要待更新</span>}
                  {groupItems.length > 0 && <RepairStateCounts items={groupItems} />}
                  {offPageRepair && <span className="text-xs text-slate-500">建议状态见对应分页</span>}
                  {repairOpen && !repairData && <span className="text-xs text-slate-500">状态未获取</span>}
                  {!repairOpen && !groupItems.length && !offPageRepair && status && <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${status.cls}`}>{status.label}</span>}
                  {!repairOpen && !groupItems.length && !offPageRepair && !status && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">观察中</span>}
                </div>
                <p className="mt-1.5 line-clamp-1 max-w-5xl text-xs leading-5 text-slate-600" title={summary}>{summary}</p>
                {groupItems.length > 0 ? <div className="mt-3"><p className="mb-2 text-xs font-semibold text-slate-700">本页修复建议 · {groupItems.length} 条</p><RepairItems embedded items={groupItems}
                  selected={selectedRepairIds} onToggle={toggleRepairItem} canEdit={canEdit && repairData?.canEdit === true}
                  limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
                  onOpenDetail={openRepairDetail} /></div>
                  : repairOpen && repairBackedSignatureKeys.has(repairSignatureKey(cluster.signature)) ? <p className="mt-2 text-xs text-slate-500">建议不在当前建议分页或筛选范围内；问题与历史证据仍可查看。</p>
                  : cluster.aggregation ? null : suggestion ? <div className="mt-2 flex max-w-5xl items-start gap-2 rounded-lg bg-blue-50/70 px-3 py-2">
                  <span className="shrink-0 text-[10px] font-semibold text-blue-700">建议</span>
                  <p className="line-clamp-2 min-w-0 text-xs leading-5 text-blue-700" title={suggestion.description}>{suggestion.description}</p>
                </div> : <p className="mt-2 max-w-5xl rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">暂无可执行建议，暂不处理，等待更多运行证据或人工判断。</p>}
                {suggestion?.verificationStatus === 'recurrence_detected' && <p className="mt-1.5 text-[11px] text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
                <ApplyTaskStatusBadge task={task} />
                <div className="mt-2 flex flex-wrap items-center gap-x-3 text-[10px] text-slate-400">
                  <span>问题累计涉及 {cluster.runIds.length} 个运行</span>
                  <span>{new Date(timeValue(cluster.latest.gmt_create)).toLocaleString()}</span>
                </div>
              </div>

              <button
                type="button"
                onClick={() => { setSelectedSignature(cluster.signature); setSelectedRepairItemId(null); setDrawerEntry('causes') }}
                className="whitespace-nowrap text-[11px] font-medium text-slate-500 hover:text-slate-900"
              >
                问题详情
              </button>
            </div>
          </article>
          })}
        </div>
      </section>}

      {standaloneRepairItems.length > 0 && <section aria-label="历史处理项" className="overflow-hidden rounded-xl border border-slate-200 bg-white px-4">
        <div className="border-b border-slate-200 py-3">
          <h3 className="text-sm font-semibold text-slate-900">历史建议与任务</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">即使最新分析不再包含对应诊断，处理状态、任务和历史结论仍会保留。</p>
        </div>
        <RepairItems items={standaloneRepairItems} selected={selectedRepairIds} onToggle={toggleRepairItem}
          canEdit={canEdit && repairData?.canEdit === true} limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
          onOpenDetail={openRepairDetail} />
      </section>}

      {repairData && <section aria-label="修复建议分页" className="rounded-lg border border-slate-200 bg-white p-3">
        <p className="mb-2 text-xs text-slate-500">修复建议分页 · 共 {repairData.page.total} 条建议。翻页仅切换建议，不隐藏问题；状态筛选仅作用于建议。</p>
        <Pagination page={repairData.page.page} pageSize={repairData.page.pageSize} total={repairData.page.total}
          onChange={(page, pageSize) => { setRepairPage(page); setRepairPageSize(pageSize) }} />
      </section>}

      {standaloneSuggestions.length > 0 && <section aria-label="已有建议跟进" className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-900">已有建议跟进</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">最新分析已无对应诊断；已有建议和任务仍需独立跟进，不代表修复已验证有效。</p>
        </div>
        <div className="divide-y divide-slate-100">
          {standaloneSuggestions.map(suggestion => {
            const status = SUGGESTION_STATUS[suggestion.status] ?? SUGGESTION_STATUS.pending
            return <article key={suggestion.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">{suggestion.weakNode || '工作流建议'}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${status.cls}`}>{status.label}</span>
                </div>
                <p className="mt-1.5 break-words text-xs leading-5 text-slate-600">{suggestion.description}</p>
                <p className="mt-1 break-all text-[10px] text-slate-400">{suggestion.signature}</p>
                <ApplyTaskStatusBadge task={applyTaskMap[suggestion.id]} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <SuggestionActions suggestion={suggestion} canEdit={canEdit} legacyApplyEnabled={!repairOpen || !!repairData && (repairData.capabilities.generation === false || !repairBackedSignatureKeys.has(repairSignatureKey(suggestion.signature)))}
                  onAction={onAction} onApply={onApply} />
              </div>
            </article>
          })}
        </div>
      </section>}

      {selectedIssue && <IssueDetailDrawer
        key={`${selectedIssue.cluster.signature}:${drawerEntry}`}
        initialTab={drawerEntry}
        cluster={selectedIssue.cluster}
        repairContent={repairContent}
        suggestion={selectedIssue.suggestion}
        task={selectedIssue.suggestion ? applyTaskMap[selectedIssue.suggestion.id] : undefined}
        previousTask={selectedIssue.suggestion ? applyTasks.find((task) => task.suggestionId === selectedIssue.suggestion?.id
          && task.proposal != null
          && ['succeeded', 'completed', 'applied_unverified'].includes(task.status)
          && task.proposalDigest !== selectedIssue.suggestion?.proposalDigest) : undefined}
        selectedFlowId={runId}
        selectedAnalysisId={analysisId}
        canEdit={canEdit}
        legacyApplyEnabled={!repairOpen || !!repairData && (repairData.capabilities.generation === false || !repairBackedSignatureKeys.has(repairSignatureKey(selectedIssue.cluster.signature)))}
        onAction={onAction}
        onApply={onApply}
        onClose={closeDetail}
      />}

      {!selectedIssue && selectedRepairItemId && selectedRepairItem && <DetailDrawer title="建议详情" onClose={closeDetail}
        header={<h3 className="text-base font-semibold text-slate-900">建议详情</h3>}>{repairContent}</DetailDrawer>}

      {selectionOpen && <RepairDialog title="已选修复建议" onClose={() => setSelectionOpen(false)}>
        <p className="mb-3 text-xs text-slate-500">包括其他页和被筛选隐藏的选择，共 {selectedRepairIds.length} 项。</p>
        <ul className="space-y-2">{selectedRepairIds.map(id => <li key={id} className="flex items-start justify-between gap-3 rounded-lg bg-slate-50 p-3 text-sm">
          <span>{selectionItems[id] ? itemTitle(selectionItems[id]) : id}</span>
          <button type="button" className="shrink-0 text-xs text-blue-700" disabled={repairBusy} aria-label={`移除 ${selectionItems[id] ? itemTitle(selectionItems[id]) : id}`}
            onClick={() => setSelectedRepairIds(current => current.filter(value => value !== id))}>移除</button>
        </li>)}</ul>
      </RepairDialog>}

      {draftOpen && repairData && <RepairDialog title="生成 Pack 草稿" busy={repairBusy} onClose={() => setDraftOpen(false)}>
        <p className="text-xs leading-5 text-slate-500">已选择 {selectedRepairIds.length} 个待处理项。只生成可审阅候选，不会应用或部署。</p>
        <label className="mt-4 block text-xs font-medium text-slate-700">本次修复要求
          <textarea aria-label="本次修复要求" rows={5} maxLength={20_000} value={instructions} disabled={repairBusy}
            onChange={event => setInstructions(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
        </label>
        {repairActionError && <p role="alert" className="mt-3 text-xs text-red-600">{repairActionError}</p>}
        <button type="button" className={`${primary} mt-4`} disabled={repairBusy || !selectedRepairIds.length} onClick={() => void submitDraft()}>
          {repairBusy ? '提交中…' : '确认生成'}
        </button>
      </RepairDialog>}

      {disposition && <RepairDialog title={disposition.action === 'restore' ? '恢复待处理' : '暂不处理'} busy={repairBusy} onClose={() => setDisposition(null)}>
        <p className="text-xs leading-5 text-slate-500">只处置当前建议内容版本，原始问题、证据和历史任务都会保留。</p>
        <label className="mt-4 block text-xs font-medium text-slate-700">原因
          <textarea aria-label="处置原因" rows={4} maxLength={2000} value={dispositionReason} disabled={repairBusy}
            onChange={event => setDispositionReason(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
        </label>
        {repairActionError && <p role="alert" className="mt-3 text-xs text-red-600">{repairActionError}</p>}
        <button type="button" className={`${primary} mt-4`} disabled={repairBusy || !dispositionReason.trim()} onClick={() => void submitDisposition()}>
          {repairBusy ? '提交中…' : '确认'}
        </button>
      </RepairDialog>}

      {selectedTaskId && repairData && <RepairTaskDialog workflowId={workflowId} taskId={selectedTaskId}
        includeHistorical={repairData.includeHistorical} canEdit={canEdit && repairData.canEdit} onClose={() => setSelectedTaskId('')}
        onChanged={() => setRepairRefresh(value => value + 1)} />}
    </div>
  )
}


interface EvolutionTabProps {
  workflowId: string
  runId?: string
  analysisId?: string
  issueSignature?: string
  section?: EvoTab
  onSectionChange?: (section: EvoTab) => void
}

export default function EvolutionTab({ workflowId, runId, analysisId, issueSignature, section, onSectionChange }: EvolutionTabProps) {
  const [localTab, setLocalTab] = useState<EvoTab>('diagnosis')
  const tab = section ?? localTab
  const [localStatus, setLocalStatus] = useState<Record<string, Exclude<SuggestionStatus, 'pending'>>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [applySuggestionIds, setApplySuggestionIds] = useState<string[]>([])
  const { data: access } = useWorkflowAccess(workflowId)
  const canEdit = access?.canEdit === true

  const { data: suggestionsData, isLoading: suggestionsLoading, refetch: refetchSuggestions } = useEvolveSuggestions({
    workflowId,
    enabled: tab === 'diagnosis',
  })

  const suggestions = suggestionsData?.suggestions ?? []
  const suggestionIds = suggestions.map((s) => s.id)

  const { data: applyTasksData } = useSuggestionApplyTasks(suggestionIds, {
    enabled: tab === 'diagnosis' && suggestionIds.length > 0,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })

  const applyTasks = applyTasksData?.tasks ?? []
  const applyTaskMap = applyTasks.reduce<Record<string, SuggestionApplyTask>>((acc, t) => {
    if (!acc[t.suggestionId]) acc[t.suggestionId] = t
    return acc
  }, {})

  const recordAction = useRecordSuggestionAction()
  const selectedApplySuggestions = suggestions.filter(suggestion => applySuggestionIds.includes(suggestion.id))
  const previousApplyTask = selectedApplySuggestions.length === 1
    ? applyTasks.find(task => task.suggestionId === selectedApplySuggestions[0].id && ['failed', 'canceled'].includes(task.status))
    : undefined

  const showNotice = (text: string) => {
    setNotice(text)
    window.setTimeout(() => setNotice(null), 2500)
  }

  const switchTab = (nextTab: EvoTab) => {
    if (onSectionChange) {
      onSectionChange(nextTab)
      return
    }
    setLocalTab(nextTab)
  }

  const handleSuggestionAction = (id: string, action: Exclude<SuggestionStatus, 'pending'>) => {
    const suggestion = suggestionsData?.suggestions.find((s) => s.id === id)
    if (!suggestion) return
    if (action === 'verified' && !window.confirm('确认业务效果已经由人工验证？这不会自动沉淀为经验。')) return
    if (action === 'ineffective' && !window.confirm('确认该建议未达到预期？系统会保留应用和观察记录。')) return

    recordAction.mutate(
      {
        suggestionId: suggestion.id,
        workflowId,
        signature: suggestion.signature,
        nodeId: suggestion.weakNode,
        action,
        fixKind: suggestion.kind,
        note: `建议面板 ${action}：${suggestion.description}`,
      },
      {
        onSuccess: () => {
          setLocalStatus((prev) => ({ ...prev, [id]: action }))
          const label = action === 'adopted'
            ? '已进入待应用'
            : action === 'benched'
              ? '已记录 Bench 决策'
              : action === 'verified'
                ? '已人工确认有效'
                : action === 'ineffective'
                  ? '已标记未达预期'
                  : '已拒绝'
          showNotice(`${suggestion.id} ${label}`)
        },
        onError: (err) => {
          showNotice(`记录操作失败：${err instanceof Error ? err.message : String(err)}`)
        },
      },
    )
  }

  return (
    <div>
      {!section && <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-1.5 rounded-full bg-gray-100 p-1">
          {EVO_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => switchTab(t.key)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-all ${
                tab === t.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>}

      {notice && (
        <div className="mb-3 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          {notice}
        </div>
      )}

      {access && !canEdit && (
        <div className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          当前账号为只读权限，可以查看问题、建议和经验，但不能应用或验证建议。
        </div>
      )}

      {tab === 'diagnosis' && <DiagnosisPanel key={workflowId}
        workflowId={workflowId}
        runId={runId}
        analysisId={analysisId}
        issueSignature={issueSignature}
        suggestions={suggestions}
        suggestionsLoading={suggestionsLoading}
        localStatus={localStatus}
        applyTaskMap={applyTaskMap}
        applyTasks={applyTasks}
        onAction={handleSuggestionAction}
        onApply={setApplySuggestionIds}
        canEdit={canEdit}
      />}

      {tab === 'remedies' && <RemediesPanel workflowId={workflowId} />}

      <ApplySuggestionModal key={applySuggestionIds.join('|')} suggestions={selectedApplySuggestions} previousTask={previousApplyTask}
        onClose={() => setApplySuggestionIds([])} onApplied={(ids) => {
          setLocalStatus(previous => {
            const next = { ...previous }
            for (const id of ids) next[id] = 'applying'
            return next
          })
          showNotice(`${ids.length} 条建议应用任务已派发`)
          void refetchSuggestions()
          window.setTimeout(() => setApplySuggestionIds([]), 1200)
        }} />
    </div>
  )
}
