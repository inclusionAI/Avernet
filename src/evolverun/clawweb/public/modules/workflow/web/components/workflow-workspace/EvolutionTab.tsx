import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  useEvolveLessons,
  useEvolveDiagnoses,
  useEvolveSuggestions,
  useRecordSuggestionAction,
  useEligibleBotsForSuggestion,
  useApplySuggestionsBatch,
  useSuggestionApplyTasks,
  useWorkflowAccess,
  useRunEvolutionAnalysis,
} from '../../api/hooks'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import RunEvolutionAnalysis from '../evolution/RunEvolutionAnalysis'
import { aggregateDiagnoses, diffWorkflowPatchOperations, timeValue, type DiagnosisCluster } from './evolution-utils'

export type EvoTab = 'diagnosis' | 'remedies'

const EVO_TABS: { key: EvoTab; label: string }[] = [
  { key: 'diagnosis', label: '问题与优化' },
  { key: 'remedies', label: '可复用经验' },
]

const REMEDY_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: '草稿', cls: 'bg-gray-100 text-gray-600' },
  verified: { label: '已验证', cls: 'bg-blue-50 text-blue-700' },
  published: { label: '已上线', cls: 'bg-emerald-50 text-emerald-700' },
  retired: { label: '已失效', cls: 'bg-gray-100 text-gray-400' },
}

const REMEDY_KIND: Record<string, string> = {
  kb_hint: '提示',
  prompt_patch: '提示词补丁',
  arg_template_fix: '参数模板修正',
  node_patch: '节点结构补丁',
  alert: '告警',
  'adjust-timeout': '超时调整',
  'retry-as-is': '直接重试',
  'skip-retry': '跳过重试',
}

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
type IssueState = 'pending' | 'verifying' | 'observing' | 'closed'
type DisplaySuggestion = Omit<EvolveSuggestion, 'status'> & { status: SuggestionStatus | ApplyTaskStatus }

const ISSUE_STATE_LABELS: Record<IssueState | 'all', string> = {
  all: '全部状态',
  pending: '待应用',
  verifying: '应用 / 验证中',
  observing: '观察中',
  closed: '已关闭',
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
  if (['applying', 'applied_unverified', 'pending', 'dispatching', 'dispatched', 'running', 'created'].includes(status)) return 'verifying'
  return 'closed'
}

function SuggestionActions({ suggestion, canEdit, onAction, onApply }: {
  suggestion: DisplaySuggestion
  canEdit: boolean
  onAction: (id: string, action: Exclude<SuggestionStatus, 'pending'>) => void
  onApply: (ids: string[]) => void
}) {
  if (!canEdit) return <span className="text-xs text-slate-400">当前账号为只读权限</span>
  if (suggestion.status === 'pending' || suggestion.status === 'adopted' || suggestion.status === 'failed') return <>
    <button onClick={() => onAction(suggestion.id, 'rejected')} className="rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800">忽略</button>
    <button onClick={() => onApply([suggestion.id])} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700">{suggestion.status === 'failed' ? '重新应用' : '应用建议'}</button>
  </>
  if (suggestion.status === 'applied_unverified') return <>
    <button onClick={() => onAction(suggestion.id, 'ineffective')} className="rounded-lg px-3 py-2 text-xs font-medium text-slate-500 hover:bg-red-50 hover:text-red-600">未达预期</button>
    <button onClick={() => onAction(suggestion.id, 'verified')} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700">确认有效</button>
  </>
  return <span className="text-xs text-slate-400">当前状态无需操作</span>
}

function IssueDetailDrawer({ cluster, suggestion, task, previousTask, selectedFlowId, selectedAnalysisId, canEdit, onAction, onApply, onClose }: {
  cluster: DiagnosisCluster
  suggestion?: DisplaySuggestion
  task?: SuggestionApplyTask
  previousTask?: SuggestionApplyTask
  selectedFlowId?: string
  selectedAnalysisId?: string
  canEdit: boolean
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
          <p className="mt-1 text-xs text-slate-400">{cluster.diagnoses.length} 次出现 · 影响 {cluster.runIds.length} 个运行</p>
        </div>
        <button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="关闭">×</button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        <section>
          <p className="text-xs font-semibold text-slate-900">聚合结论</p>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{summary}</p>
          <p className="mt-2 truncate font-mono text-[10px] text-slate-400" title={cluster.signature}>{cluster.signature}</p>
        </section>

        <section className="mt-6 border-t border-slate-100 pt-5">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold text-slate-900">当前建议</p>
            {suggestion && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{REMEDY_KIND[suggestion.kind] ?? suggestion.kind}</span>}
          </div>
          {suggestion ? <>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{suggestion.description}</p>
            {suggestion.verificationStatus === 'recurrence_detected' && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs leading-5 text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
            {proposalDiff && (proposalDiff.added.length + proposalDiff.changed.length + proposalDiff.removed.length > 0) && <div className="mt-3 rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs leading-5 text-slate-600">
              <p className="font-medium text-blue-700">与上次已应用方案相比</p>
              <p>新增 {proposalDiff.added.length} 项 · 调整 {proposalDiff.changed.length} 项 · 移除 {proposalDiff.removed.length} 项</p>
              {[...proposalDiff.added, ...proposalDiff.changed, ...proposalDiff.removed].slice(0, 6).map((operation, index) => <p key={`${String(operation.nodeId)}-${String(operation.path)}-${index}`} className="mt-1 font-mono text-[10px] text-slate-500">{String(operation.nodeId ?? 'workflow')} {String(operation.path ?? '')}</p>)}
            </div>}
            <ApplyTaskStatusBadge task={task} />
          </> : <p className="mt-2 rounded-lg bg-amber-50/70 px-3 py-2 text-xs leading-5 text-amber-700">暂无可执行建议，暂不处理，等待更多证据或人工判断。</p>}
        </section>

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

        {selectedInstance?.analysisId !== 'legacy' && <section className="mt-6 border-t border-slate-100 pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-semibold text-slate-900">所选分析详情</p>
            <p className="font-mono text-[10px] text-slate-400">{selectedInstance.analysisId}</p>
          </div>
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
        </section>}
      </div>

      {suggestion && <footer className="flex min-h-16 items-center justify-end gap-2 border-t border-slate-200 bg-white px-5 py-3">
        <SuggestionActions suggestion={suggestion} canEdit={canEdit} onAction={onAction} onApply={onApply} />
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
  const { data, isLoading } = useEvolveDiagnoses({ workflowId, limit: 100 })
  const diagnoses = data?.diagnoses ?? []
  const [nodeFilter, setNodeFilter] = useState<string>('all')
  const [modeFilter, setModeFilter] = useState<string>('all')
  const [stateFilter, setStateFilter] = useState<IssueState | 'all'>('all')
  const [selectedSignature, setSelectedSignature] = useState<string | null>(issueSignature ?? null)
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState<string[]>([])

  if (isLoading || suggestionsLoading) return <div className="p-4 text-xs text-slate-500">加载问题与建议...</div>

  const clusters = aggregateDiagnoses(diagnoses)
  const nodes = Array.from(new Set(clusters.map((cluster) => cluster.node))).sort()
  const modes = Array.from(new Set(clusters.map((cluster) => cluster.mode).filter(Boolean)))
  const suggestionBySignature = new Map(suggestions.map((suggestion) => {
    const status = resolveSuggestionStatus(suggestion, localStatus, applyTaskMap[suggestion.id])
    return [suggestion.signature, { ...suggestion, status }] as const
  }))
  const enriched = clusters.map((cluster) => {
    const suggestion = suggestionBySignature.get(cluster.signature)
    const runIds = Array.from(new Set([
      ...cluster.runIds,
      ...(suggestion?.evidenceRuns ?? []),
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
    ]
    return {
      cluster: { ...cluster, runIds, instances },
      suggestion,
      state: issueState(suggestion?.status),
    }
  })
  const filtered = enriched.filter(({ cluster, state }) =>
    (nodeFilter === 'all' || cluster.node === nodeFilter)
    && (modeFilter === 'all' || cluster.mode === modeFilter)
    && (stateFilter === 'all' || stateFilter === state))
  const pendingCount = enriched.filter((item) => item.state === 'pending').length
  const verifyingCount = enriched.filter((item) => item.state === 'verifying').length
  const observingCount = enriched.filter((item) => item.state === 'observing').length
  const selectedIssue = enriched.find(({ cluster }) => cluster.signature === selectedSignature)
  const selectableFilteredIds = filtered.flatMap(({ suggestion }) => (
    suggestion && ['pending', 'adopted', 'failed'].includes(suggestion.status) ? [suggestion.id] : []
  ))
  const allFilteredSelected = selectableFilteredIds.length > 0
    && selectableFilteredIds.every((id) => selectedSuggestionIds.includes(id))
  const toggleSuggestion = (id: string) => {
    setSelectedSuggestionIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id])
  }
  const toggleAllFiltered = () => {
    setSelectedSuggestionIds((current) => allFilteredSelected
      ? current.filter((id) => !selectableFilteredIds.includes(id))
      : Array.from(new Set([...current, ...selectableFilteredIds])))
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">从问题到效果验证</h3>
        <p className="mt-1 text-xs leading-5 text-slate-500">同类异常按失败特征聚合；建议只在具备可执行方案时出现，应用完成后仍需验证实际效果。</p>
      </div>

      {diagnoses.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 text-xs text-slate-500">
          当前工作流暂无已记录异常。任务护航会分析失败运行，也会保留成功运行中的异常和退化信号。
        </div>
      )}

      {diagnoses.length > 0 && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-200 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span><strong className="mr-1 text-sm font-semibold text-slate-900">{clusters.length}</strong>问题</span>
            <span><strong className="mr-1 text-sm font-semibold text-amber-700">{pendingCount}</strong>待应用</span>
            <span><strong className="mr-1 text-sm font-semibold text-blue-700">{verifyingCount}</strong>应用 / 验证中</span>
            <span><strong className="mr-1 text-sm font-semibold text-slate-600">{observingCount}</strong>观察中</span>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canEdit && selectableFilteredIds.length > 0 && <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <input
                type="checkbox"
                aria-label="选择当前筛选下全部可应用建议"
                checked={allFilteredSelected}
                onChange={toggleAllFiltered}
                className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600"
              />
              全选
            </label>}
            {canEdit && selectedSuggestionIds.length > 0 && <button
              type="button"
              onClick={() => {
                onApply(selectedSuggestionIds)
                setSelectedSuggestionIds([])
              }}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
            >
              应用 {selectedSuggestionIds.length} 条建议
            </button>}
            <select
              aria-label="问题状态"
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value as IssueState | 'all')}
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
          {filtered.map(({ cluster, suggestion }) => {
          const status = suggestion ? SUGGESTION_STATUS[suggestion.status] ?? SUGGESTION_STATUS.pending : null
          const summary = cluster.latest.error_text ?? cluster.latest.reasoning ?? cluster.mode
          const task = suggestion ? applyTaskMap[suggestion.id] : undefined
          const selectable = canEdit && suggestion != null && ['pending', 'adopted', 'failed'].includes(suggestion.status)
          return <article key={cluster.signature} data-layout="compact-issue-row" className="px-4 py-3 transition-colors hover:bg-slate-50/70">
            <div className="grid items-start gap-3 lg:grid-cols-[20px_minmax(0,1fr)_auto]">
              {selectable ? <input
                type="checkbox"
                aria-label={`选择 ${cluster.node} 的建议`}
                checked={selectedSuggestionIds.includes(suggestion.id)}
                onChange={() => toggleSuggestion(suggestion.id)}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600"
              /> : <span aria-hidden="true" className="hidden h-4 w-4 lg:block" />}
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">{cluster.node}</span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{cluster.mode}</span>
                  {status && <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${status.cls}`}>{status.label}</span>}
                  {!status && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">观察中</span>}
                </div>
                <p className="mt-1.5 line-clamp-2 max-w-5xl text-xs leading-5 text-slate-600" title={summary}>{summary}</p>
                {suggestion ? <div className="mt-2 flex max-w-5xl items-start gap-2 rounded-lg bg-blue-50/70 px-3 py-2">
                  <span className="shrink-0 text-[10px] font-semibold text-blue-700">建议</span>
                  <p className="line-clamp-2 min-w-0 text-xs leading-5 text-blue-700" title={suggestion.description}>{suggestion.description}</p>
                </div> : <p className="mt-2 max-w-5xl rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">暂无可执行建议，暂不处理，等待更多运行证据或人工判断。</p>}
                {suggestion?.verificationStatus === 'recurrence_detected' && <p className="mt-1.5 text-[11px] text-red-600">应用后再次出现 {suggestion.recurrenceCount ?? 1} 次，需要重新判断。</p>}
                <ApplyTaskStatusBadge task={task} />
                <div className="mt-2 flex flex-wrap items-center gap-x-3 text-[10px] text-slate-400">
                  <span>{cluster.diagnoses.length} 次出现</span>
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
        onAction={onAction}
        onApply={onApply}
        onClose={() => setSelectedSignature(null)}
      />}
    </div>
  )
}


function RemediesPanel({ workflowId }: { workflowId: string }) {
  const { data, isLoading } = useEvolveLessons({ workflowId, limit: 100 })
  const lessons = data?.lessons ?? []

  if (isLoading) return <div className="p-4 text-xs text-gray-500">加载经验库...</div>

  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs text-slate-500">经验是经过复用边界审核的知识，不由建议应用自动生成。</p>
      </div>

      {lessons.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-lg text-slate-500">◇</div>
          <h4 className="mt-3 text-sm font-medium text-slate-900">还没有可复用经验</h4>
          <p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-slate-500">
            当同类问题多次出现、修复边界明确且效果经过人工审核后，才适合沉淀为经验。
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2 text-[11px] text-slate-500">
            <span className="rounded-full bg-slate-100 px-2.5 py-1">多次命中</span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1">边界明确</span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1">人工审核</span>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {lessons.map((r) => (
            <div key={r.lesson_id} className="flex items-start justify-between gap-5 px-4 py-3.5">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">{REMEDY_KIND[r.fix_kind] ?? r.fix_kind}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${REMEDY_STATUS[r.status]?.cls ?? REMEDY_STATUS.draft.cls}`}>{REMEDY_STATUS[r.status]?.label ?? r.status}</span>
                  <span className="text-[11px] text-slate-400">{r.workflow_id === workflowId ? '当前工作流' : '全局经验'}</span>
                </div>
                <p className="mt-1.5 truncate font-mono text-[11px] text-slate-500" title={r.failure_signature}>{r.failure_signature}</p>
                <p className="mt-1 text-[11px] text-slate-400">
                  {r.source === 'retry_healing' ? '自愈重试' : r.source === 'manual' ? '手动录入' : r.source === 'evolve_optimize' ? '进化优化' : '日志分析'}
                  {' · '}{String(r.gmt_create).slice(0, 10)}{' · '}{r.lesson_id}
                </p>
              </div>
              <div className="grid shrink-0 grid-cols-2 gap-5 text-right">
                <div>
                  <p className="text-sm font-semibold tabular-nums text-slate-900">{r.hit_count} / {r.rescued_count}</p>
                  <p className="text-[10px] text-slate-400">命中 / 救回</p>
                </div>
                <div>
                  <p className="text-sm font-semibold tabular-nums text-slate-900">{r.hit_count > 0 ? `${Math.round((r.successRate ?? 0) * 100)}%` : '—'}</p>
                  <p className="text-[10px] text-slate-400">成功率</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
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

type ApplySuggestionModalProps = {
  suggestions: EvolveSuggestion[]
  previousTask?: SuggestionApplyTask
  onClose: () => void
  onApplied: (suggestionIds: string[]) => void
}

export function ApplySuggestionModal({ suggestions, previousTask, onClose, onApplied }: ApplySuggestionModalProps) {
  const suggestionIds = suggestions.map((suggestion) => suggestion.id)
  const firstId = suggestionIds[0]
  const { data, isLoading, error } = useEligibleBotsForSuggestion(firstId, suggestionIds.length > 0)
  const [selectedBotId, setSelectedBotId] = useState<string>('')
  const [notice, setNotice] = useState<string | null>(null)
  const [isApplying, setIsApplying] = useState(false)
  const defaultSpec = useMemo(() => suggestions.map((suggestion) => suggestion.description).join('\n'), [suggestions])
  const [applicationSpec, setApplicationSpec] = useState(previousTask?.applicationSpec?.trim() || defaultSpec)
  const applyMutation = useApplySuggestionsBatch()
  const bots = useMemo(() => data?.bots ?? [], [data?.bots])
  const effectiveSelectedBotId = selectedBotId || (
    previousTask?.botId && bots.some((bot) => bot.botId === previousTask.botId)
      ? previousTask.botId
      : ''
  )
  const selectedBot = bots.find((b) => b.botId === effectiveSelectedBotId)
  const isBulk = suggestionIds.length > 1
  const isRetry = suggestions.some((suggestion) => suggestion.status === 'failed')

  const handleApply = async () => {
    const spec = applicationSpec.trim()
    if (suggestionIds.length === 0 || !effectiveSelectedBotId || !spec) return
    setIsApplying(true)
    try {
      await applyMutation.mutateAsync({ suggestionIds, botId: effectiveSelectedBotId, botEnv: selectedBot?.env ?? undefined, applicationSpec: spec })
      setNotice(`已派发 1 个任务处理 ${suggestionIds.length} 条建议；应用完成后仍需自然流量或人工验证效果`)
      onApplied(suggestionIds)
    } catch (err) {
      setNotice(`应用任务派发失败：${err instanceof Error ? err.message : String(err)}`)
    }
    setIsApplying(false)
  }

  if (suggestionIds.length === 0) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="apply-suggestion-modal-title"
        className="flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div data-testid="apply-suggestion-modal-body" className="min-h-0 flex-1 overflow-y-auto p-5 pb-4">
          <h3 id="apply-suggestion-modal-title" className="mb-3 text-sm font-semibold text-gray-900">
          {isBulk ? `批量应用 ${suggestionIds.length} 条建议` : '选择 Bot 自动应用建议'}
        </h3>
        <p className="mb-3 text-xs text-amber-700">
          选择一个具有编辑权限的 Bot。Bot 会使用 clawmind-workflow skill 读取完整配置，结合建议安全修改并部署；应用完成后仍需验证实际效果。
        </p>
        {notice && <div className="mb-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700">{notice}</div>}
        {isLoading && <div className="py-4 text-xs text-gray-500">加载可应用 Bot 中...</div>}
        {!isLoading && error && (
          <div className="py-3 text-xs text-red-600">加载失败：{error instanceof Error ? error.message : String(error)}</div>
        )}
        {!isLoading && bots.length === 0 && (
          <div className="py-3 text-xs text-gray-500">
            没有可用的 Bot 对该 workflow 拥有编辑权限。请先在权限管理中授予 Bot 的 can_edit 权限。
          </div>
        )}
        {!isLoading && bots.length > 0 && (
          <div className="mb-4 space-y-2">
            {bots.map((bot) => (
              <label key={bot.botId} className="flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 p-2 hover:bg-gray-50">
                <input
                  type="radio"
                  name="apply-bot"
                  value={bot.botId}
                  checked={effectiveSelectedBotId === bot.botId}
                  onChange={() => setSelectedBotId(bot.botId)}
                  className="text-blue-600"
                />
                <div className="text-xs">
                  <div className="font-medium text-gray-900">{bot.botName ?? bot.botId}</div>
                  <div className="text-gray-500">{bot.botId}{bot.env ? ` · ${bot.env}` : ''}</div>
                </div>
              </label>
            ))}
          </div>
        )}
        <label className="mb-4 block text-xs font-medium text-slate-700">
          本次修复要求
          <textarea
            aria-label="本次修复要求"
            value={applicationSpec}
            maxLength={20_000}
            onChange={(event) => setApplicationSpec(event.target.value)}
            rows={6}
            className="mt-1.5 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-xs leading-5 text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          />
          <span className="mt-1 block text-[10px] font-normal text-slate-400">只影响本次应用任务，不会修改原始分析和建议。</span>
        </label>
        </div>
        <div data-testid="apply-suggestion-modal-footer" className="flex shrink-0 justify-end gap-2 border-t border-slate-100 bg-white px-5 py-4">
          <button onClick={onClose} className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">取消</button>
          <button
            onClick={handleApply}
            disabled={!effectiveSelectedBotId || !applicationSpec.trim() || isApplying}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60">
            {isApplying ? '派发中...' : (isBulk ? `确认应用 ${suggestionIds.length} 条` : isRetry ? '重新应用' : '确认应用')}
          </button>
        </div>
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
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  })

  const applyTasks = applyTasksData?.tasks ?? []
  const applyTaskMap = applyTasks.reduce<Record<string, SuggestionApplyTask>>((acc, t) => {
    if (!acc[t.suggestionId]) acc[t.suggestionId] = t
    return acc
  }, {})

  const recordAction = useRecordSuggestionAction()
  const selectedApplySuggestions = suggestions.filter((suggestion) => applySuggestionIds.includes(suggestion.id))
  const previousApplyTask = selectedApplySuggestions.length === 1
    ? applyTasks.find((task) => task.suggestionId === selectedApplySuggestions[0].id && ['failed', 'canceled'].includes(task.status))
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

      {tab === 'diagnosis' && <DiagnosisPanel
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

      <ApplySuggestionModal
        key={applySuggestionIds.join('|')}
        suggestions={selectedApplySuggestions}
        previousTask={previousApplyTask}
        onClose={() => setApplySuggestionIds([])}
        onApplied={(ids) => {
          setLocalStatus((prev) => {
          const next = { ...prev }
          for (const id of ids) next[id] = 'applying'
          return next
        })
          showNotice(`${ids.length} 条建议应用任务已派发`)
          void refetchSuggestions()
          window.setTimeout(() => setApplySuggestionIds([]), 1200)
        }}
      />
    </div>
  )
}
