import { useEffect, useMemo, useRef, useState } from 'react'
import {
  useEvolveSuggestions,
  useRecordSuggestionAction,
  useSuggestionApplyTasks,
  useWorkflowAccess,
} from '../../api/hooks'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import { aggregateDiagnoses, timeValue } from './evolution-utils'
import { groupDiagnoses, useIssueGroups, useSelectedIssueGroup } from './issue-groups'
import IssueDetailDrawer, { ApplyTaskStatusBadge, SuggestionActions, SUGGESTION_STATUS } from './IssueDetailDrawer'
import { repairBatches } from '../../api/repair-batches'
import { repairReadError } from '../../api/repair-read-error'
import IssueIdentity, { issueModeLabel } from './IssueIdentity'
import { repairSignatureKey, type RepairCandidatesResponse, type RepairInboxItem } from '../../../server/contracts/repair-workbench'
import RepairItemDetail from './repair-batch/RepairItemDetail'
import IssueRepairSuggestions from './repair-batch/IssueRepairSuggestions'
import { selectionConflicts } from './repair-batch/repair-selection'
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
type RepairViewState = {
  repairOpen: boolean
  repairPage: number
  repairPageSize: number
  includeHistorical: boolean
  selectedTaskId: string
}

function readRepairView(workflowId: string): RepairViewState {
  const fallback: RepairViewState = { repairOpen: false, repairPage: 1, repairPageSize: 20,
    includeHistorical: false, selectedTaskId: '' }
  try {
    const saved = JSON.parse(sessionStorage.getItem(`workflow-repair:${workflowId}`) ?? '{}')
    return {
      repairOpen: saved.repairOpen === true,
      repairPage: Number.isSafeInteger(saved.repairPage) && saved.repairPage > 0 ? saved.repairPage : fallback.repairPage,
      repairPageSize: Number.isSafeInteger(saved.repairPageSize) && [10, 20, 50].includes(saved.repairPageSize) ? saved.repairPageSize : fallback.repairPageSize,
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
  const [initialRepairView] = useState(() => readRepairView(workflowId))
  const [nodeFilter, setNodeFilter] = useState<string>('all')
  const [modeFilter, setModeFilter] = useState<string>('all')
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
  const [historyOpen, setHistoryOpen] = useState(false)
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
  const { data, isLoading, isError, refetch } = useIssueGroups(workflowId, { page: repairPage, pageSize: repairPageSize,
    ...(nodeFilter !== 'all' ? { nodeId: nodeFilter } : {}), ...(modeFilter !== 'all' ? { failureMode: modeFilter } : {}) })
  const diagnoses = (data?.groups ?? []).flatMap(groupDiagnoses)

  useEffect(() => {
    try { sessionStorage.setItem(`workflow-repair:${workflowId}`, JSON.stringify({
      repairOpen, repairPage, repairPageSize, includeHistorical, selectedTaskId,
    })) } catch { /* Session storage can be disabled. */ }
  }, [workflowId, repairOpen, repairPage, repairPageSize, includeHistorical, selectedTaskId])

  useEffect(() => {
    if (!repairOpen) return
    let current = true
    setRepairLoading(true)
    // Read task/capability context only. Candidate pages belong to an opened issue, never the main list.
    repairBatches.candidates(workflowId, { state: 'all', page: 1, pageSize: 1, includeHistorical }).then(result => {
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
  }, [workflowId, repairOpen, repairRefresh, includeHistorical, canEdit])

  const clusters = aggregateDiagnoses(diagnoses)
  for (const cluster of clusters) cluster.aggregation = data?.groups.find(group => group.signature === cluster.signature)
  const nodes = data?.facets?.nodes ?? Array.from(new Set(clusters.map((cluster) => cluster.node))).sort()
  const modes = data?.facets?.modes ?? Array.from(new Set(clusters.map((cluster) => cluster.mode).filter(Boolean)))
  const suggestionBySignature = new Map(suggestions.map((suggestion) => {
    const status = resolveSuggestionStatus(suggestion, localStatus, applyTaskMap[suggestion.id])
    return [suggestion.signature, { ...suggestion, status }] as const
  }))
  const standaloneSuggestions = suggestions.map(suggestion =>
    ({ ...suggestion, status: resolveSuggestionStatus(suggestion, localStatus, applyTaskMap[suggestion.id]) }))
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
    }
  })
  const repairBackedSignatureKeys = new Set(repairData?.repairSignatureKeys ?? [])
  const filtered = enriched
  const issuePage = data?.page ?? { page: 1, pageSize: repairPageSize, total: enriched.length, totalPages: 1 }
  const activeRepairTask = repairData?.tasks.find(task => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase))
  const selectedItems = selectedRepairIds.flatMap(id => selectionItems[id] ? [selectionItems[id]] : [])
  const conflicts = selectionConflicts(selectedItems)
  const selectedGroup = useSelectedIssueGroup(workflowId, selectedSignature, data?.groups.find(group => group.signature === selectedSignature))
  const externalCluster = selectedGroup.group ? aggregateDiagnoses(groupDiagnoses(selectedGroup.group))[0] : undefined
  if (externalCluster) externalCluster.aggregation = selectedGroup.group
  const selectedIssue = enriched.find(({ cluster }) => cluster.signature === selectedSignature)
    ?? (externalCluster ? { cluster: externalCluster, suggestion: suggestionBySignature.get(externalCluster.signature) } : undefined)
  const selectedRepairItem = selectedRepairItemId ? selectionItems[selectedRepairItemId] : undefined
  const openRepairDetail = (item: RepairInboxItem) => {
    const signature = typeof item.context?.signature === 'string' ? item.context.signature : null
    setSelectedSignature(signature)
    setSelectedRepairItemId(item.itemId)
    setDrawerEntry('repairs')
  }
  const closeDetail = () => { setSelectedSignature(null); setSelectedRepairItemId(null) }
  const toggleRepairItem = (id: string) => setSelectedRepairIds(current => current.includes(id)
    ? current.filter(item => item !== id)
    : current.length < Math.min(100, repairData?.limits.maxItems ?? 100) ? [...current, id] : current)
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
    if (!repairData || !selectedRepairIds.length || repairBusy || conflicts.length || activeRepairTask
      || !canEdit || !repairData.canEdit || !repairData.capabilities.generation || repairLoading || repairError) return
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
      setDraftOpen(false); setSelectedTaskId(result.taskId); setRepairNotice(`已创建 ${result.taskId}，AIS 将生成可审阅 Pack 草稿，不会自动部署。`)
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

  const acceptRepairPage = (result: RepairCandidatesResponse) => {
        if (initializedDigest.current && initializedDigest.current !== result.inputDigest) {
          setSelectedRepairIds([]); setDraftOpen(false); setRepairData(null)
          initializedDigest.current = result.inputDigest
          detailEpoch.current += 1; detailRequests.current.clear()
          setSelectionItems({}); setRepairDetails({}); setRepairDetailLoading({}); setRepairDetailErrors({})
          setSelectionNotice('建议来源已更新，原选择已清空，请重新确认范围。')
          setRepairRefresh(value => value + 1)
          return
        }
        const unavailable = new Set(result.items.filter(item => exclusion(item)).map(item => item.itemId))
        setSelectedRepairIds(previous => previous.filter(id => !unavailable.has(id)))
        setSelectionItems(previous => ({ ...previous, ...Object.fromEntries(result.items.map(item => [item.itemId, item])) }))
      }

  const renderRepairItem = (item: RepairInboxItem, scopeCanEdit = true) => <RepairItemDetail key={`${item.itemId}:${initializedDigest.current}`} item={item}
      showSelection={!!selectedRepairItemId || !selectedSignature && !historyOpen}
      embedded={!selectedRepairItemId && (!!selectedSignature || historyOpen)}
      detail={repairDetails[item.itemId]} loading={repairDetailLoading[item.itemId]} error={repairDetailErrors[item.itemId]}
      onLoad={itemId => void loadRepairDetail(itemId)} selected={selectedRepairIds.includes(item.itemId)} onToggle={toggleRepairItem}
      canEdit={scopeCanEdit && canEdit && repairData?.canEdit === true && !repairLoading && !repairError && !repairBusy}
      limitReached={selectedRepairIds.length >= Math.min(100, repairData?.limits.maxItems ?? 100)}
      onDisposition={(item, action) => { resetRequestId(); setRepairActionError(''); setDisposition({ item, action }); setDispositionReason('') }} />
  const repairContent = !repairOpen ? <button type="button" className={primary} onClick={() => setRepairOpen(true)}>进入修复处理</button>
    : repairError ? <p role="alert" className="text-sm text-red-700">{repairError}<button className="ml-2 underline" onClick={() => setRepairRefresh(value => value + 1)}>重试修复读取</button></p>
    : repairLoading || !repairData ? <p role="status" className="text-sm text-slate-600">正在读取修复任务与权限…</p>
    : selectedRepairItem ? <div className="space-y-4">
      {selectedIssue && <button type="button" className="text-sm text-blue-700 hover:underline" onClick={() => setSelectedRepairItemId(null)}>返回此问题的建议列表</button>}
      {renderRepairItem(selectedRepairItem)}
    </div>
    : selectedIssue ? <IssueRepairSuggestions key={`${selectedIssue.cluster.signature}:${repairRefresh}`} workflowId={workflowId}
      signature={selectedIssue.cluster.signature} includeHistorical={includeHistorical}
      selected={selectedRepairIds} onToggle={toggleRepairItem} canEdit={canEdit && repairData?.canEdit === true && !repairLoading && !repairBusy}
      limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
      onResult={acceptRepairPage}
      renderItem={renderRepairItem} />
    : <p className="text-sm text-slate-600">请选择问题查看修复建议。</p>

  return (
    <div className="space-y-3">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold text-slate-900">问题与优化</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">选择问题，比较修复建议，再确认本次修复范围。生成草稿不会部署。</p></div>
          {!repairOpen && <button type="button" className={primary} onClick={() => setRepairOpen(true)}>进入修复处理</button>}
        </div>
      </div>

      {isLoading && <p role="status" className="rounded-lg bg-slate-50 p-4 text-xs text-slate-500">正在加载问题摘要；修复建议和任务独立加载。</p>}
      {isError && <div role="alert" className="rounded-lg bg-red-50 p-4 text-xs text-red-600">问题分组加载失败，不能显示为没有问题。<button type="button" onClick={() => void refetch()} className="ml-2 underline">重试问题</button></div>}

      {repairOpen && repairLoading && <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">修复任务与处理状态仍在加载，问题列表可继续查看。</p>}
      {repairOpen && repairError && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{repairError}<button type="button" className="ml-2 underline" onClick={() => setRepairRefresh(value => value + 1)}>重试</button></p>}
      {suggestionsLoading && <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">历史建议仍在加载，不影响查看问题与修复任务。</p>}
      {repairNotice && <p role="status" className="rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700">{repairNotice}</p>}



      {!isLoading && !isError && diagnoses.length === 0 && standaloneSuggestions.length === 0 && nodeFilter === 'all' && modeFilter === 'all' && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-xs text-slate-500">
          当前工作流暂无已记录异常。任务护航会分析失败运行，也会保留成功运行中的异常和退化信号。
        </div>
      )}

      {data && !isLoading && !isError && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-900">问题列表 · 共 {issuePage.total} 个问题</h3>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <select
              aria-label="问题节点"
              value={nodeFilter}
              onChange={(e) => { setNodeFilter(e.target.value); setRepairPage(1) }}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              <option value="all">全部节点</option>
              {nodes.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <select
              aria-label="问题模式"
              value={modeFilter}
              onChange={(e) => { setModeFilter(e.target.value); setRepairPage(1) }}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              <option value="all">全部模式</option>
              {modes.map((m) => <option key={m} value={m}>{issueModeLabel(m)}</option>)}
            </select>
            <span className="pl-1 text-[11px] text-slate-400">本页 {filtered.length} 个问题</span>
          </div>
        </div>

        {filtered.length === 0 && <div className="px-4 py-10 text-center text-xs text-slate-400">没有符合当前筛选条件的问题</div>}


        <div className="divide-y divide-slate-100">
          {filtered.map(({ cluster, suggestion }) => {
          const summary = cluster.aggregation?.summary?.summary ?? '聚合结论尚未生成，查看单次运行分析。'
          const task = suggestion ? applyTaskMap[suggestion.id] : undefined
          return <article key={cluster.signature} data-layout="compact-issue-row" className="px-4 py-3 transition-colors hover:bg-slate-50/70">
            <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <IssueIdentity node={cluster.node} mode={cluster.mode} />
                  {cluster.aggregation?.stale && <span tabIndex={0} title="当前保留上次生成的摘要，尚未覆盖最新分析。请在问题详情核对摘要依据和当前关联运行。" className="rounded bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800">摘要待更新</span>}

                </div>
                <p className="mt-1.5 line-clamp-1 max-w-5xl text-xs leading-5 text-slate-600" title={summary}>{summary}</p>
                {suggestion?.verificationStatus === 'recurrence_detected' && <p className="mt-1.5 text-[11px] text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
                <ApplyTaskStatusBadge task={task} />
                <div className="mt-2 flex flex-wrap items-center gap-x-3 text-[10px] text-slate-400">
                  <span>问题累计涉及 {cluster.runIds.length} 个运行</span>
                  <span>{new Date(timeValue(cluster.latest.gmt_create)).toLocaleString()}</span>
                </div>
              </div>

              <button
                type="button"
                onClick={() => { setSelectedSignature(cluster.signature); setSelectedRepairItemId(null); setDrawerEntry('causes'); setRepairOpen(true) }}
                className="inline-flex self-start items-center py-1 text-sm font-medium text-blue-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              >
                问题详情
              </button>
            </div>
          </article>
          })}
        </div>
      </section>}

      {data && !isLoading && <section aria-label="问题分页" className="rounded-lg border border-slate-200 bg-white px-4">
        <Pagination page={issuePage.page} pageSize={issuePage.pageSize} total={issuePage.total}
          onChange={(page, pageSize) => { setRepairPage(page); setRepairPageSize(pageSize) }} />
      </section>}
      {repairData && <section aria-label="处理任务" className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">本次修复</h3>
            <p className="mt-1 text-sm text-slate-500">选择建议 → 生成草稿 → 审阅修改</p>
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
          </div>
          {selectionNotice && !selectionNotice.startsWith('默认不选择') && <p className="mt-2 leading-5">{selectionNotice}</p>}

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
          </div>) : null}
        </div>
      </section>}

      <button type="button" className="text-sm text-slate-600 hover:text-blue-700 hover:underline"
        onClick={() => { setRepairOpen(true); setHistoryOpen(true) }}>全部建议与历史</button>
      {historyOpen && <RepairDialog title="全部建议与历史" onClose={() => setHistoryOpen(false)}>
        <p className="mb-3 text-xs text-slate-500">用于查找未出现在当前问题页的旧建议；这里的分页仅针对建议。</p>
        <IssueRepairSuggestions key={repairRefresh} workflowId={workflowId} includeHistorical={includeHistorical}
          selected={selectedRepairIds} onToggle={toggleRepairItem} canEdit={canEdit && repairData?.canEdit === true && !repairLoading && !repairBusy}
          limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
          onResult={acceptRepairPage}
          renderItem={renderRepairItem} />
      </RepairDialog>}

      {standaloneSuggestions.length > 0 && issuePage.total === 0 && <section aria-label="已有建议跟进" className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-900">已有建议跟进</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">保留已有建议与应用记录，不代表整个问题已验证解决。</p>
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
        repairFooter={<div className="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
          <span className="text-sm text-slate-600">本次修复已选 {selectedRepairIds.length} 条建议</span>
          <button type="button" className={primary} disabled={!selectedRepairIds.length || !repairData || repairBusy || repairLoading}
            onClick={() => { closeDetail(); resetRequestId(); setRepairActionError(''); setDraftOpen(true) }}>确认修复范围</button>
        </div>}
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

      {selectedSignature && !selectedIssue && <DetailDrawer title="问题详情" onClose={closeDetail} header={<h3>问题详情</h3>}>
        {selectedGroup.error ? <p role="alert">{selectedGroup.error}<button onClick={selectedGroup.retry}>重试问题</button></p>
          : <p role="status">正在读取所选问题…</p>}
      </DetailDrawer>}
      {!selectedSignature && selectedRepairItemId && selectedRepairItem && <DetailDrawer title="建议详情" onClose={closeDetail}
        header={<h3 className="text-base font-semibold text-slate-900">建议详情</h3>}>{repairContent}</DetailDrawer>}

      {selectionOpen && <RepairDialog title="已选修复建议" onClose={() => setSelectionOpen(false)}>
        <p className="mb-3 text-xs text-slate-500">包括其他页和被筛选隐藏的选择，共 {selectedRepairIds.length} 项。</p>
        <ul className="space-y-2">{selectedRepairIds.map(id => <li key={id} className="flex items-start justify-between gap-3 rounded-lg bg-slate-50 p-3 text-sm">
          <button type="button" className="text-left text-blue-700 underline" onClick={() => { if (selectionItems[id]) { setSelectionOpen(false); openRepairDetail(selectionItems[id]) } }}>
            {selectionItems[id] ? itemTitle(selectionItems[id]) : id}</button>
          <button type="button" className="shrink-0 text-xs text-blue-700" disabled={repairBusy} aria-label={`移除 ${selectionItems[id] ? itemTitle(selectionItems[id]) : id}`}
            onClick={() => setSelectedRepairIds(current => current.filter(value => value !== id))}>移除</button>
        </li>)}</ul>
      </RepairDialog>}

      {draftOpen && repairData && <RepairDialog title="生成 Pack 草稿" busy={repairBusy} onClose={() => setDraftOpen(false)}>
        <p className="text-xs leading-5 text-slate-500">已选择 {selectedRepairIds.length} 个待处理项。只生成可审阅候选，不会应用或部署。</p>
        <ul aria-label="本次修复范围" className="mt-3 divide-y divide-slate-100">
          {selectedItems.map(item => <li key={item.itemId} className="flex items-start justify-between gap-3 py-3 text-sm">
            <span>{itemTitle(item)}</span>
            <button type="button" aria-label={`移除 ${itemTitle(item)}`} disabled={repairBusy} className="shrink-0 text-xs text-blue-700"
              onClick={() => setSelectedRepairIds(ids => ids.filter(id => id !== item.itemId))}>移除</button>
          </li>)}
        </ul>
        {conflicts.length > 0 && <div role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          <p>所选建议修改了同一配置，且方案不同。请移除不采用的方案后生成，系统不会替你选择。</p>
          <ul className="mt-2 space-y-2">{conflicts.map(conflict => <li key={conflict.itemIds.join(':')}>
            {conflict.nodeId} · {conflict.path}：{conflict.itemIds.map(id => itemTitle(selectionItems[id])).join(' / ')}
          </li>)}</ul>
        </div>}
        <label className="mt-4 block text-xs font-medium text-slate-700">本次修复要求
          <textarea aria-label="本次修复要求" rows={5} maxLength={20_000} value={instructions} disabled={repairBusy}
            onChange={event => setInstructions(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
        </label>
        {repairActionError && <p role="alert" className="mt-3 text-xs text-red-600">{repairActionError}</p>}
        <button type="button" className={`${primary} mt-4`} disabled={repairBusy || !selectedRepairIds.length || conflicts.length > 0 || !!activeRepairTask
          || !canEdit || !repairData.canEdit || !repairData.capabilities.generation || repairLoading || !!repairError} onClick={() => void submitDraft()}>
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
