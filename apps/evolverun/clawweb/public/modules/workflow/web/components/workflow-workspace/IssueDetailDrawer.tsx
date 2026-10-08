import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { EvolveSuggestion, SuggestionApplyTask } from '@avernet/clawweb-shared/web/api/client'
import { useRunEvolutionAnalysis } from '../../api/hooks'
import RunEvolutionAnalysis from '../evolution/RunEvolutionAnalysis'
import { diffWorkflowPatchOperations, type DiagnosisCluster } from './evolution-utils'
import IssueSummary from './IssueSummary'
import IssueIdentity from './IssueIdentity'
import DetailDrawer from './DetailDrawer'
import { REMEDY_KIND } from './RemediesPanel'

export const SUGGESTION_STATUS: Record<string, { label: string; cls: string }> = {
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
type DisplaySuggestion = Omit<EvolveSuggestion, 'status'> & { status: SuggestionStatus | ApplyTaskStatus }
export function SuggestionActions({ suggestion, canEdit, legacyApplyEnabled, onAction, onApply }: {
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

export default function IssueDetailDrawer({ cluster, suggestion, task, previousTask, selectedFlowId, selectedAnalysisId, canEdit,
  legacyApplyEnabled, repairContent, initialTab, onAction, onApply, onClose }: {
  cluster: DiagnosisCluster
  repairContent: ReactNode
  initialTab?: 'causes' | 'repairs' | 'evidence'
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
  const [tab, setTab] = useState<'causes' | 'repairs' | 'evidence'>(initialTab ?? (selectedFlowId || selectedAnalysisId ? 'evidence' : 'causes'))
  const summary = cluster.latest.error_text ?? cluster.latest.reasoning ?? cluster.mode
  const proposalDiff = suggestion?.proposal && previousTask?.proposal
    ? diffWorkflowPatchOperations(previousTask.proposal, suggestion.proposal)
    : null

  return <DetailDrawer title="问题详情" onClose={onClose} header={<>
          <div className="flex flex-wrap items-center gap-2">
            <IssueIdentity node={cluster.node} mode={cluster.mode} />
          </div>
          <p className="mt-1 text-xs text-slate-400">问题累计涉及 {cluster.runIds.length} 个运行</p>
      </>} navigation={<nav aria-label="问题详情分区" className="flex shrink-0 gap-2 border-b border-slate-200 px-5">
        {([['causes', '问题原因'], ['repairs', '修复建议'], ['evidence', '证据与历史']] as const).map(([key, label]) =>
          <button type="button" key={key} aria-pressed={tab === key} onClick={() => setTab(key)}
            className={`border-b-2 px-3 py-3 text-sm ${tab === key ? 'border-blue-600 font-medium text-blue-700' : 'border-transparent text-slate-500'}`}>{label}</button>)}
      </nav>} footer={tab === 'evidence' && suggestion && !cluster.aggregation && <footer className="flex min-h-16 items-center justify-end gap-2 border-t border-slate-200 bg-white px-5 py-3">
        <SuggestionActions suggestion={suggestion} canEdit={canEdit} legacyApplyEnabled={legacyApplyEnabled} onAction={onAction} onApply={onApply} />
      </footer>}>
        {tab === 'causes' && (cluster.aggregation ? <IssueSummary group={cluster.aggregation} /> : <section>
          <p className="text-xs font-semibold text-slate-900">最新诊断结论</p>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{summary}</p>
          <p className="mt-2 truncate font-mono text-[10px] text-slate-400" title={cluster.signature}>{cluster.signature}</p>
        </section>)}
        {tab === 'repairs' && repairContent}
        {tab === 'evidence' && <>
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

        <IssueEvidence cluster={cluster} selectedFlowId={selectedFlowId} selectedAnalysisId={selectedAnalysisId} />
        </>}

  </DetailDrawer>
}


function IssueEvidence({ cluster, selectedFlowId, selectedAnalysisId }: {
  cluster: DiagnosisCluster; selectedFlowId?: string; selectedAnalysisId?: string
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

  return <>        <section className="mt-6 border-t border-slate-100 pt-5">
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
                    <span className={`block truncate font-mono text-[11px] ${active ? 'font-medium text-blue-700' : 'text-slate-700'}`}>{new Date(instance.occurredAtMs).toLocaleString()}</span>
                    <span className="block truncate text-[10px] text-slate-400">运行 {instance.flowId} · {analysisLabel}</span>
                  </span>
                </button>
                <Link to={`/runs/${instance.flowId}?${params.toString()}`} aria-label={`打开运行 ${instance.flowId}`} className="text-[11px] text-blue-600">→</Link>
              </div>
            })}
          </div>
          {cluster.instances.length > 10 && <p className="mt-2 text-[10px] text-slate-400">仅展示最近 10 条分析记录</p>}
        </section>

        {selectedInstance && selectedInstance.analysisId !== 'legacy' && <details open className="mt-6 border-t border-slate-100 pt-5">
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
        </details>}</>
}

function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest > 0 ? `${minutes}分${rest}秒` : `${minutes}分`
}

export function ApplyTaskStatusBadge({ task }: { task: SuggestionApplyTask | undefined }) {
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
