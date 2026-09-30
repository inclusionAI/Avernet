import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  useEvolveSuggestions,
  useRecordSuggestionAction,
  useSuggestionApplyTasks,
  useWorkflowAccess,
  useRunEvolutionAnalysis,
} from '../../api/hooks'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import RunEvolutionAnalysis from '../evolution/RunEvolutionAnalysis'
import { aggregateDiagnoses, diffWorkflowPatchOperations, timeValue, type DiagnosisCluster } from './evolution-utils'
import { groupDiagnoses, useIssueGroups } from './issue-groups'
import IssueSummary from './IssueSummary'
import { repairBatches } from '../../api/repair-batches'
import { repairSignatureKey, type RepairCandidatesResponse, type RepairInboxFilter, type RepairInboxItem } from '../../../server/contracts/repair-workbench'
import RepairItems from './repair-batch/RepairItems'
import RepairTaskDialog from './repair-batch/RepairTaskDialog'
import { exclusion, phases, primary, RepairDialog } from './repair-batch/repair-view'
import Pagination from '../Pagination'
import ApplySuggestionModal from './ApplySuggestionModal'
import RemediesPanel, { REMEDY_KIND } from './RemediesPanel'

export type EvoTab = 'diagnosis' | 'remedies'

const EVO_TABS: { key: EvoTab; label: string }[] = [
  { key: 'diagnosis', label: '问题与优化' },
  { key: 'remedies', label: '可复用经验' },
]

const SUGGESTION_STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: '待应用', cls: 'bg-gray-100 text-gray-600' },
  adopted: { label: '待应用', cls: 'bg-gray-100 text-gray-600' },
  applying: { label: '应用中', cls: 'bg-amber-50 text-amber-700' },
  applied_unverified: { label: '已应用 · 待验证', cls: 'bg-blue-50 text-blue-700' },
  verified: { label: '已验证', cls: 'bg-emerald-50 text-emerald-700' },
  ineffective: { label: '未达预期', cls: 'bg-red-50 text-red-700' },
  failed: { label: '应用失败', cls: 'bg-red-50 text-red-700' },
  rejected: { label: '已拒绝', cls: 'bg-gray-100 text-gray-500' },
  benched: { label: '已记录', cls: 'bg-gray-100 text-gray-500' },
}

type SuggestionStatus = EvolveSuggestion['status']
type ApplyTaskStatus = SuggestionApplyTask['status']
type IssueState = 'pending' | 'processing' | 'awaiting_verification' | 'closed' | 'no_action' | 'observing'
type DisplaySuggestion = Omit<EvolveSuggestion, 'status'> & { status: SuggestionStatus | ApplyTaskStatus }
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

function repairIssueState(item: RepairInboxItem): IssueState {
  if (item.state === 'processing') return 'processing'
  if (item.state === 'awaiting_verification') return 'awaiting_verification'
  if (item.state === 'verified' || item.state === 'ineffective') return 'closed'
  if (item.state === 'no_action') return 'no_action'
  if (item.state === 'pending') return 'pending'
  return 'observing'
}

function repairInboxFilter(state: IssueState | 'all'): RepairInboxFilter {
  if (state === 'observing') return 'all'
  return state
}

function SuggestionActions({ suggestion, canEdit, legacyApplyEnabled, onAction, onApply }: {
  suggestion: DisplaySuggestion
  canEdit: boolean
  legacyApplyEnabled: boolean
  onAction: (id: string, action: Exclude<SuggestionStatus, 'pending'>) => void
  onApply: (ids: string[]) => void
}) {
  if (!canEdit) return <span className="text-xs text-slate-400">当前账号为只读权限</span>
  if (suggestion.status === 'pending' || suggestion.status === 'adopted' || suggestion.status === 'failed') return legacyApplyEnabled ? <>
    <button onClick={() => onAction(suggestion.id, 'rejected')} className="rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800">忽略</button>
    <button onClick={() => onApply([suggestion.id])} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700">{suggestion.status === 'failed' ? '重新应用' : '应用建议'}</button>
  </> : <span className="text-xs text-slate-500">等待进入 Pack 修复流程</span>
  if (suggestion.status === 'applied_unverified') return <>
    <button onClick={() => onAction(suggestion.id, 'ineffective')} className="rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-red-50 hover:text-red-600">未达预期</button>
    <button onClick={() => onAction(suggestion.id, 'verified')} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700">确认有效</button>
  </>
  return <span className="text-xs text-slate-400">当前状态无需操作</span>
}

function IssueDetailDrawer({ cluster, suggestion, task, previousTask, selectedFlowId, selectedAnalysisId, canEdit,
  legacyApplyEnabled, onAction, onApply, onClose }: {
  cluster: DiagnosisCluster
  suggestion?: DisplaySuggestion
  task?: SuggestionApplyTask
  previousTask?: SuggestionApplyTask
  selectedFlowId?: string
  selectedAnalysisId?: string
  canEdit: boolean
  legacyApplyEnabled: boolean
  onAction: (id: string, action: Exclude<SuggestionStatus, 'pending'>) => void
  onApply: (ids: string[]) => void
  onClose: () => void
}) {
  const initialInstance = cluster.instances.find((instance) =>
    (!selectedFlowId || instance.flowId === selectedFlowId)
    && (!selectedAnalysisId || instance.analysisId === selectedAnalysisId)) ?? cluster.instances[0]
  const [selectedInstanceKey, setSelectedInstanceKey] = useState(
    initialInstance ? `${initialInstance.analysisId}\u0000${initialInstance.diagnosisId}\u0000${initialInstance.flowId}` : '',
  )
  const selectedInstance = cluster.instances.find((instance) =>
    `${instance.analysisId}\u0000${instance.diagnosisId}\u0000${instance.flowId}` === selectedInstanceKey) ?? initialInstance
  const instanceAnalysis = useRunEvolutionAnalysis(
    selectedInstance?.flowId ?? '',
    selectedInstance?.analysisId === 'legacy' ? undefined : selectedInstance?.analysisId,
    Boolean(selectedInstance && selectedInstance.analysisId !== 'legacy'),
  )
  const summary = cluster.latest.error_text ?? cluster.latest.reasoning ?? cluster.mode
  const status = suggestion ? SUGGESTION_STATUS[suggestion.status] ?? SUGGESTION_STATUS.pending : null
  const proposalDiff = suggestion?.proposal && previousTask?.proposal
    ? diffWorkflowPatchOperations(previousTask.proposal, suggestion.proposal)
    : null

  return <div className="fixed inset-0 z-40">
    <button type="button" aria-label="关闭问题详情" className="absolute inset-0 cursor-default bg-slate-950/20" onClick={onClose} />
    <aside role="dialog" aria-modal="true" aria-label="问题详情" className="absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col border-l border-slate-200 bg-white shadow-[-16px_0_40px_rgba(15,23,42,0.14)]">
      <header className="flex items-start gap-4 border-b border-slate-200 px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-slate-950">{cluster.node}</h3>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{cluster.mode}</span>
            {status ? <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${status.cls}`}>{status.label}</span> : <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">观察中</span>}
          </div>
          <p className="mt-1 text-xs text-slate-400">影响 {cluster.runIds.length} 个运行</p>
        </div>
        <button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="关闭">×</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        {cluster.aggregation ? <IssueSummary group={cluster.aggregation} /> : <section>
          <p className="text-xs font-semibold text-slate-900">最新诊断结论</p>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{summary}</p>
          <p className="mt-2 truncate font-mono text-[10px] text-slate-400" title={cluster.signature}>{cluster.signature}</p>
        </section>}

        <details className="mt-6 border-t border-slate-100 pt-5" open={!cluster.aggregation}>
          <summary className="cursor-pointer text-xs font-semibold text-slate-900">历史建议与任务（独立于本次修复）</summary>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold text-slate-900">已有建议</p>
            {suggestion && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{REMEDY_KIND[suggestion.kind] ?? suggestion.kind}</span>}
          </div>
          {suggestion ? <>
            <p className="mt-2 text-xs text-amber-700">建议与聚合结论独立；应用或验证此建议不代表所有原因均已解决。</p>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{suggestion.description}</p>
            {suggestion.verificationStatus === 'recurrence_detected' && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs leading-5 text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
            {proposalDiff && (proposalDiff.added.length + proposalDiff.changed.length + proposalDiff.removed.length > 0) && <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs leading-5 text-slate-600">
              <p className="font-medium text-blue-700">与上次已应用方案相比</p>
              <p>新增 {proposalDiff.added.length} 项 · 调整 {proposalDiff.changed.length} 项 · 移除 {proposalDiff.removed.length} 项</p>
              {[...proposalDiff.added, ...proposalDiff.changed, ...proposalDiff.removed].slice(0, 6).map((operation, index) => <p key={`${String(operation.nodeId)}-${String(operation.path)}-${index}`} className="mt-1 font-mono text-[10px] text-slate-500">{String(operation.nodeId ?? 'workflow')} {String(operation.path ?? '')}</p>)}
            </div>}
            <ApplyTaskStatusBadge task={task} />
            {cluster.aggregation && <div className="mt-3 flex flex-wrap justify-end gap-2"><SuggestionActions suggestion={suggestion} canEdit={canEdit}
              legacyApplyEnabled={legacyApplyEnabled} onAction={onAction} onApply={onApply} /></div>}
          </> : <p className="mt-2 rounded-lg bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-700">暂无可执行建议，暂不处理，等待更多证据或人工判断。</p>}
        </details>

        <section className="mt-6 border-t border-slate-100 pt-5">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-semibold text-slate-900">相关分析记录</p>
            <span className="text-[10px] text-slate-400">{cluster.instances.length} 条</span>
          </div>
          <div className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-200">
            {cluster.instances.slice(0, 10).map((instance) => {
              const params = new URLSearchParams({ from: 'workspace', workspaceView: 'diagnosis', issueSignature: cluster.signature })
              if (instance.analysisId !== 'legacy') params.set('analysisId', instance.analysisId)
              const active = selectedInstance?.flowId === instance.flowId && selectedInstance?.analysisId === instance.analysisId
              const analysisLabel = instance.analysisId === 'legacy' ? '历史诊断' : instance.analysisId
              return <div key={`${instance.analysisId}-${instance.flowId}`} className={`flex items-center gap-2 px-3 py-2.5 transition-colors ${active ? 'bg-blue-50/80' : 'hover:bg-slate-50'}`}>
                <button
                  type="button"
                  aria-label={`选择分析 ${instance.flowId} ${analysisLabel}`}
                  onClick={() => setSelectedInstanceKey(`${instance.analysisId}\u0000${instance.diagnosisId}\u0000${instance.flowId}`)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                >
                  <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${active ? 'bg-blue-600 ring-4 ring-blue-100' : 'bg-slate-300'}`} />
                  <span className="min-w-0">
                    <span className={`block truncate font-mono text-[11px] ${active ? 'font-medium text-blue-700' : 'text-slate-700'}`}>{instance.flowId}</span>
                    <span className="block truncate text-[10px] text-slate-400">{analysisLabel} · {new Date(instance.occurredAtMs).toLocaleString()}</span>
                  </span>
                </button>
                <Link to={`/runs/${instance.flowId}?${params.toString()}`} aria-label={`打开运行 ${instance.flowId}`} className="text-[11px] text-blue-600">→</Link>
              </div>
            })}
          </div>
          {cluster.instances.length > 10 && <p className="mt-2 text-[10px] text-slate-400">仅展示最近 10 条分析记录</p>}
        </section>

        {selectedInstance?.analysisId !== 'legacy' && <details open className="mt-6 border-t border-slate-100 pt-5">
          <summary className="flex cursor-pointer flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-semibold text-slate-900">所选分析详情</p>
            <p className="font-mono text-[10px] text-slate-400">{selectedInstance.analysisId}</p>
          </summary>
          {instanceAnalysis.isLoading && <p className="mt-2 text-xs text-slate-500">加载所选分析...</p>}
          {instanceAnalysis.isError && <p className="mt-2 text-xs text-red-600">分析结果加载失败</p>}
          {!instanceAnalysis.isLoading && !instanceAnalysis.isError && !instanceAnalysis.data?.analysis && (
            <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
              未找到所选分析详情，请重试或打开关联运行查看。
            </p>
          )}
          {instanceAnalysis.data?.analysis && <div className="mt-3"><RunEvolutionAnalysis
            analysis={instanceAnalysis.data.analysis}
            variant="evidence"
            focusDiagnosisId={selectedInstance.diagnosisId}
            focusFailureSignature={cluster.signature}
          /></div>}
        </details>}
      </div>

      {suggestion && !cluster.aggregation && <footer className="flex min-h-16 items-center justify-end gap-2 border-t border-slate-200 bg-white px-5 py-3">
        <SuggestionActions suggestion={suggestion} canEdit={canEdit} legacyApplyEnabled={legacyApplyEnabled} onAction={onAction} onApply={onApply} />
      </footer>}
    </aside>
  </div>
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
  const [repairData, setRepairData] = useState<RepairCandidatesResponse | null>(null)
  const [repairOpen, setRepairOpen] = useState(initialRepairView.repairOpen)
  const [repairLoading, setRepairLoading] = useState(false)
  const [repairError, setRepairError] = useState('')
  const [repairRefresh, setRepairRefresh] = useState(0)
  const [repairPage, setRepairPage] = useState(initialRepairView.repairPage)
  const [repairPageSize, setRepairPageSize] = useState(initialRepairView.repairPageSize)
  const [includeHistorical, setIncludeHistorical] = useState(initialRepairView.includeHistorical)
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
      if (initializedDigest.current !== result.inputDigest) {
        const limit = Math.min(100, result.limits.maxItems)
        setSelectedRepairIds(result.items.filter(item => !exclusion(item)).slice(0, limit).map(item => item.itemId))
        initializedDigest.current = result.inputDigest
      }
    }).catch(() => {
      if (current) setRepairError('修复任务与处理状态加载失败；问题与证据仍可查看。')
    }).finally(() => { if (current) setRepairLoading(false) })
    return () => { current = false }
  }, [workflowId, repairOpen, repairRefresh, repairPage, repairPageSize, includeHistorical, selectedRepairState])

  if (isLoading) return <div className="p-4 text-xs text-slate-500">加载问题与建议...</div>
  if (isError) return <div role="alert" className="p-4 text-xs text-red-600">问题分组加载失败，不能显示为没有问题。<button type="button" onClick={() => void refetch()} className="ml-2 underline">重试</button></div>

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
  const pagedEnriched = enriched.filter(({ cluster }) => !repairData || !repairBackedSignatureKeys.has(repairSignatureKey(cluster.signature))
    || repairBySignature.has(cluster.signature))
  const filtered = pagedEnriched.filter(({ cluster, repairItems: groupItems, state }) =>
    (nodeFilter === 'all' || cluster.node === nodeFilter)
    && (modeFilter === 'all' || cluster.mode === modeFilter)
    && (stateFilter === 'all' || (groupItems.length ? groupItems.some(item => repairIssueState(item) === stateFilter) : stateFilter === state)))
  const pendingCount = repairData?.counts.pending ?? enriched.filter((item) => item.state === 'pending').length
  const processingCount = repairData?.counts.processing ?? enriched.filter((item) => item.state === 'processing').length
  const verifyingCount = repairData?.counts.awaiting_verification ?? enriched.filter((item) => item.state === 'awaiting_verification').length
  const closedCount = repairData?.counts.closed ?? enriched.filter((item) => item.state === 'closed').length
  const noActionCount = repairData?.counts.no_action ?? 0
  const activeRepairTask = repairData?.tasks.find(task => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase))
  const selectedIssue = filtered.find(({ cluster }) => cluster.signature === selectedSignature)
  const selectableFilteredIds = filtered.flatMap(({ repairItems: groupItems }) => groupItems.filter(item => !exclusion(item)).map(item => item.itemId))
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
    setRepairDetails({})
    setRepairDetailLoading({})
    setRepairDetailErrors({})
    detailRequests.current.clear()
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
    detailRequests.current.add(itemId)
    setRepairDetailLoading(current => ({ ...current, [itemId]: true }))
    setRepairDetailErrors(current => ({ ...current, [itemId]: '' }))
    try {
      const detail = await repairBatches.item(workflowId, itemId)
      setRepairDetails(current => ({ ...current, [itemId]: detail }))
    } catch (error) {
      setRepairDetailErrors(current => ({ ...current, [itemId]: error instanceof Error ? error.message : String(error) }))
    } finally {
      detailRequests.current.delete(itemId)
      setRepairDetailLoading(current => ({ ...current, [itemId]: false }))
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
      setDraftOpen(false); setRepairNotice(`已创建 ${result.taskId}，AIS 将生成可审阅 Pack 草稿，不会自动部署。`)
      resetRequestId(); initializedDigest.current = ''; setRepairRefresh(value => value + 1)
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
      setDisposition(null); setDispositionReason(''); resetRequestId(); initializedDigest.current = ''; setRepairRefresh(value => value + 1)
    } catch (error) { setRepairActionError(error instanceof Error ? error.message : String(error)) }
    finally { setRepairBusy(false) }
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold text-slate-900">从问题到效果验证</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">问题列表持续保留；进入修复处理后才加载候选项并生成可审阅 Pack 草稿。</p></div>
          {!repairOpen && <button type="button" className={primary} onClick={() => setRepairOpen(true)}>进入修复处理</button>}
        </div>
      </div>

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
          <button type="button" className={primary} disabled={!canEdit || !repairData.canEdit || !repairData.capabilities.generation || !selectedRepairIds.length || !!activeRepairTask || repairBusy}
            onClick={() => { resetRequestId(); setRepairActionError(''); setDraftOpen(true) }}>
            生成 Pack 草稿（{selectedRepairIds.length}）
          </button>
        </div>
        {!repairData.capabilities.generation && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">{repairData.capabilities.reason || '当前 Host 未接入 AIS 生成能力。问题仍可查看，但不能伪装为已生成或已部署。'}</p>}
        {activeRepairTask && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">已有可继续的修复任务，请先在现有任务中审阅、反馈或取消。</p>}
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

      {diagnoses.length === 0 && standaloneSuggestions.length === 0 && standaloneRepairItems.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-xs text-slate-500">
          当前工作流暂无已记录异常。任务护航会分析失败运行，也会保留成功运行中的异常和退化信号。
        </div>
      )}

      {diagnoses.length > 0 && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-200 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span><strong className="mr-1 text-sm font-semibold text-slate-900">{clusters.length}</strong>问题</span>
            <span><strong className="mr-1 text-sm font-semibold text-amber-700">{pendingCount}</strong>待处理</span>
            <span><strong className="mr-1 text-sm font-semibold text-blue-700">{processingCount}</strong>处理中</span>
            <span><strong className="mr-1 text-sm font-semibold text-blue-700">{verifyingCount}</strong>待验证</span>
            <span><strong className="mr-1 text-sm font-semibold text-emerald-700">{closedCount}</strong>已关闭</span>
            <span><strong className="mr-1 text-sm font-semibold text-slate-600">{noActionCount}</strong>暂不处理</span>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canEdit && selectableFilteredIds.length > 0 && <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <input
                type="checkbox"
                aria-label="选择当前筛选下全部待处理项"
                checked={allFilteredSelected}
                onChange={toggleAllFiltered}
                className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600"
              />
              全选
            </label>}
            <select
              aria-label="问题状态"
              value={stateFilter}
              onChange={(e) => {
                initializedDigest.current = ''
                setSelectedRepairIds([])
                setRepairPage(1)
                setStateFilter(e.target.value as IssueState | 'all')
              }}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600"
            >
              {Object.entries(ISSUE_STATE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
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
            <span className="pl-1 text-[11px] text-slate-400">{filtered.length} / {clusters.length}</span>
          </div>
        </div>

        {filtered.length === 0 && <div className="px-4 py-10 text-center text-xs text-slate-400">没有符合当前筛选条件的问题</div>}

        <div className="divide-y divide-slate-100">
          {filtered.map(({ cluster, suggestion, repairItems: groupItems }) => {
          const status = suggestion ? SUGGESTION_STATUS[suggestion.status] ?? SUGGESTION_STATUS.pending : null
          const summary = cluster.aggregation?.summary?.summary ?? '聚合结论尚未生成，查看单次运行分析。'
          const task = suggestion ? applyTaskMap[suggestion.id] : undefined
          return <article key={cluster.signature} data-layout="compact-issue-row" className="px-4 py-3 transition-colors hover:bg-slate-50/70">
            <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">{cluster.node}</span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{cluster.mode}</span>
                  {groupItems.length > 1 && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">混合状态</span>}
                  {!groupItems.length && status && <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${status.cls}`}>{status.label}</span>}
                  {!groupItems.length && !status && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">观察中</span>}
                </div>
                <p className="mt-1.5 line-clamp-2 max-w-5xl text-xs leading-5 text-slate-600" title={summary}>{summary}</p>
                {groupItems.length > 0 ? <div className="mt-3"><RepairItems items={groupItems} allItems={groupItems}
                  selected={selectedRepairIds} onToggle={toggleRepairItem} canEdit={canEdit && repairData?.canEdit === true}
                  limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
                  details={repairDetails} detailLoading={repairDetailLoading} detailErrors={repairDetailErrors} onLoadDetail={itemId => void loadRepairDetail(itemId)}
                  onDisposition={(item, action) => { resetRequestId(); setRepairActionError(''); setDisposition({ item, action }); setDispositionReason('') }} /></div>
                  : cluster.aggregation ? <p className="mt-2 rounded-lg bg-blue-50/70 px-3 py-2 text-xs text-blue-700">查看问题、建议总览与原始证据；可执行处理项同步后会直接出现在这里。</p> : suggestion ? <div className="mt-2 flex max-w-5xl items-start gap-2 rounded-lg bg-blue-50/70 px-3 py-2">
                  <span className="shrink-0 text-[10px] font-semibold text-blue-700">建议</span>
                  <p className="line-clamp-2 min-w-0 text-xs leading-5 text-blue-700" title={suggestion.description}>{suggestion.description}</p>
                </div> : <p className="mt-2 max-w-5xl rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">暂无可执行建议，暂不处理，等待更多运行证据或人工判断。</p>}
                {suggestion?.verificationStatus === 'recurrence_detected' && <p className="mt-1.5 text-[11px] text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
                <ApplyTaskStatusBadge task={task} />
                <div className="mt-2 flex flex-wrap items-center gap-x-3 text-[10px] text-slate-400">
                  <span>影响 {cluster.runIds.length} 个运行</span>
                  <span>{new Date(timeValue(cluster.latest.gmt_create)).toLocaleString()}</span>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setSelectedSignature(cluster.signature)}
                className="whitespace-nowrap text-[11px] font-medium text-slate-500 hover:text-slate-900"
              >
                查看
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
        <RepairItems items={standaloneRepairItems} allItems={standaloneRepairItems} selected={selectedRepairIds} onToggle={toggleRepairItem}
          canEdit={canEdit && repairData?.canEdit === true} limit={Math.min(100, repairData?.limits.maxItems ?? 100)}
          details={repairDetails} detailLoading={repairDetailLoading} detailErrors={repairDetailErrors} onLoadDetail={itemId => void loadRepairDetail(itemId)}
          onDisposition={(item, action) => { resetRequestId(); setRepairActionError(''); setDisposition({ item, action }); setDispositionReason('') }} />
      </section>}

      {repairData && <Pagination page={repairData.page.page} pageSize={repairData.page.pageSize} total={repairData.page.total}
        onChange={(page, pageSize) => { setRepairPage(page); setRepairPageSize(pageSize) }} />}

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
                <SuggestionActions suggestion={suggestion} canEdit={canEdit} legacyApplyEnabled={!repairOpen || repairData?.capabilities.generation === false || !repairBackedSignatureKeys.has(repairSignatureKey(suggestion.signature))}
                  onAction={onAction} onApply={onApply} />
              </div>
            </article>
          })}
        </div>
      </section>}

      {selectedIssue && <IssueDetailDrawer
        cluster={selectedIssue.cluster}
        suggestion={selectedIssue.suggestion}
        task={selectedIssue.suggestion ? applyTaskMap[selectedIssue.suggestion.id] : undefined}
        previousTask={selectedIssue.suggestion ? applyTasks.find((task) => task.suggestionId === selectedIssue.suggestion?.id
          && task.proposal != null
          && ['succeeded', 'completed', 'applied_unverified'].includes(task.status)
          && task.proposalDigest !== selectedIssue.suggestion?.proposalDigest) : undefined}
        selectedFlowId={runId}
        selectedAnalysisId={analysisId}
        canEdit={canEdit}
        legacyApplyEnabled={!repairOpen || repairData?.capabilities.generation === false || selectedIssue.repairItems.length === 0}
        onAction={onAction}
        onApply={onApply}
        onClose={() => setSelectedSignature(null)}
      />}

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
        onChanged={() => { initializedDigest.current = ''; setRepairRefresh(value => value + 1) }} />}
    </div>
  )
}


function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest > 0 ? `${minutes}分${rest}秒` : `${minutes}分`
}

function ApplyTaskStatusBadge({ task }: { task: SuggestionApplyTask | undefined }) {
  if (!task) return null
  const active = ['created', 'pending', 'dispatching', 'dispatched', 'running', 'applying'].includes(task.status)
  const progress = ['created', 'pending', 'dispatching'].includes(task.status)
    ? '任务已创建，等待派发'
    : ['dispatched', 'running', 'applying'].includes(task.status)
      ? 'Bot 正在执行应用和部署'
      : ['succeeded', 'completed', 'applied_unverified'].includes(task.status)
        ? 'Bot 已完成应用，等待效果验证'
        : task.status === 'failed'
          ? '应用失败'
          : task.status === 'canceled'
            ? '应用已取消'
            : null
  const elapsedMs = task.progress?.elapsedMs ?? null
  const stalled = active && task.progress?.stalled === true
  return (
    <div className="mt-2 space-y-1 text-[10px] text-slate-400">
      {progress && (
        <div className="flex items-center gap-1.5 font-medium text-blue-600">
          {['created', 'pending', 'dispatching', 'dispatched', 'running', 'applying'].includes(task.status) && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" aria-hidden="true" />}
          <span>{progress}</span>
        </div>
      )}
      {active && task.progress && elapsedMs != null && (
        <div className="font-medium text-slate-600">
          {task.progress.message} · 已用时 {formatElapsed(elapsedMs)}
        </div>
      )}
      {stalled && (
        <div className="text-amber-600">当前阶段超过 90 秒未更新，Bot 可能仍在处理或卡住</div>
      )}
      {task.progress?.history && task.progress.history.length > 0 && (
        <details className="group max-w-md rounded-md border border-slate-200 bg-slate-50/70 px-2.5 py-1.5 text-slate-500">
          <summary className="cursor-pointer list-none font-medium text-slate-600 marker:hidden">
            <span className="inline-flex items-center gap-1">
              <span className="text-[9px] transition-transform group-open:rotate-90" aria-hidden="true">▶</span>
              执行记录（{task.progress.history.length}）
            </span>
          </summary>
          <ol className="mt-1.5 space-y-1 border-l border-slate-200 pl-2.5">
            {task.progress.history.map((item, index) => (
              <li key={`${item.updatedAtMs}-${item.phase}-${index}`} className="flex items-start justify-between gap-3">
                <span className="min-w-0 text-slate-600">{item.message}</span>
                <time className="shrink-0 tabular-nums text-slate-400">
                  {new Date(item.updatedAtMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </time>
              </li>
            ))}
          </ol>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {task.botId && (
        <span>
          Bot: {task.botName ?? task.botId}{task.botEnv ? ` · ${task.botEnv}` : ''}
        </span>
      )}
      {task.errorMessage && (
        <span className="max-w-xs truncate text-red-600" title={task.errorMessage}>
          {task.errorMessage}
        </span>
      )}
      {task.appliedAt && (
        <span>{new Date(task.appliedAt).toLocaleString()}</span>
      )}
      </div>
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
