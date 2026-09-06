import { useState, useCallback, useEffect, useMemo } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { useFlowRun, useDbWorkflow, useEvolveDiagnoses, useEvolveSuggestions, useAnalyzeRun, useResetAnalysisRun, useAnalysisProgress, useRunEvolutionAnalysis } from '../api/hooks'
import RunSummaryHeader from '../components/RunSummaryHeader'
import NodeExecutionList from '../components/NodeExecutionList'
import RunDagView from '../components/RunDagView'
import ErrorState from '../components/ErrorState'
import SimpleRunLogsPanel from '../components/SimpleRunLogsPanel'
import AnalysisModal from '../components/AnalysisModal'
import InterventionPanel from '../components/InterventionPanel'
import AnalyzeRunBotModal from '../components/AnalyzeRunBotModal'
import RunEvolutionAnalysis from '../components/evolution/RunEvolutionAnalysis'
import type { RunEvolutionAnalysisResponse, WorkflowAnalysisProgressResponse } from '@avernet/clawweb-shared/web/api/client'
import type { FlowRun, NodeExecution } from '@avernet/clawweb-shared/web/types'

type TabId = 'nodes' | 'logs' | 'dag'

const ANALYSIS_STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  analyzing: { label: '分析中', cls: 'bg-amber-50 text-amber-700' },
  completed: { label: '已分析', cls: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '分析失败', cls: 'bg-red-50 text-red-700' },
  none: { label: '未分析', cls: 'bg-gray-100 text-gray-500' },
}

function AnalysisStatusBadge({ status }: { status?: string | null }) {
  const cfg = ANALYSIS_STATUS_LABEL[status ?? 'none'] ?? ANALYSIS_STATUS_LABEL.none
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${cfg.cls}`}>{cfg.label}</span>
}

function formatAnalysisElapsed(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder > 0 ? `${minutes}分${remainder}秒` : `${minutes}分`
}

export default function RunDetail() {
  const { flowId } = useParams<{ flowId: string }>()
  const [searchParams] = useSearchParams()
  const fromWorkspace = searchParams.get('from') === 'workspace'
  const workspaceView = searchParams.get('workspaceView')
  const [activeTab, setActiveTab] = useState<TabId>('nodes')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [analyzingNode, setAnalyzingNode] = useState<NodeExecution | null>(null)
  const [analyzeModalOpen, setAnalyzeModalOpen] = useState(false)

  const {
    data: runDetail,
    isLoading: runLoading,
    isError: runError,
    error: runErrorObj,
    refetch: refetchRun,
  } = useFlowRun(flowId ?? '')
  const analyzeMutation = useAnalyzeRun()
  const resetAnalysisMutation = useResetAnalysisRun()
  const analysisProgressQuery = useAnalysisProgress(
    flowId ?? '',
    runDetail?.run?.evolution_analysis_status === 'analyzing' || analyzeMutation.isPending,
  )
  const analysisResultQuery = useRunEvolutionAnalysis(flowId ?? '', searchParams.get('analysisId') ?? undefined)
  const refetchAnalysisResult = analysisResultQuery.refetch

  useEffect(() => {
    if (!['completed', 'failed', 'insufficient_evidence'].includes(analysisProgressQuery.data?.status ?? '')) return
    void refetchRun()
    void refetchAnalysisResult()
  }, [analysisProgressQuery.data?.status, refetchAnalysisResult, refetchRun])

  const analyzeDispatchError = analyzeMutation.isError && analyzeMutation.error
    ? analyzeMutation.error instanceof Error ? analyzeMutation.error.message : String(analyzeMutation.error)
    : null

  const { data: diagnosesData, isLoading: diagnosesLoading } = useEvolveDiagnoses({
    workflowId: runDetail?.run?.workflow_id ?? '',
    limit: 100,
    enabled: !!runDetail?.run?.workflow_id,
  })
  const { data: suggestionsData, isLoading: suggestionsLoading } = useEvolveSuggestions({
    workflowId: runDetail?.run?.workflow_id ?? '',
    enabled: !!runDetail?.run?.workflow_id,
  })


  const handleNodeSelect = useCallback((nodeId: string) => {
    setSelectedNodeId((prev) => (prev === nodeId ? null : nodeId))
  }, [])

  const handleAnalyze = useCallback((node: NodeExecution) => {
    setAnalyzingNode(node)
  }, [])

  const handleCloseAnalysis = useCallback(() => {
    setAnalyzingNode(null)
  }, [])

  const handleReanalyzeRun = useCallback(() => {
    setAnalyzeModalOpen(true)
  }, [])

  const run = runDetail?.run
  const nodes = useMemo(() => runDetail?.nodes ?? [], [runDetail?.nodes])
  const workspaceReturnUrl = run?.workflow_id
    ? (() => {
        const params = new URLSearchParams({ workflowId: run.workflow_id })
        if (workspaceView === 'diagnosis') {
          params.set('tab', 'evolution')
          params.set('evoTab', 'diagnosis')
          const analysisId = searchParams.get('analysisId')
          const issueSignature = searchParams.get('issueSignature')
          if (analysisId) params.set('analysisId', analysisId)
          if (issueSignature) params.set('issueSignature', issueSignature)
        }
        return `/workflows/workspace?${params.toString()}`
      })()
    : '/'

  // Compute node progress from actual node_executions data.
  // flow_runs.succeeded_count / failed_count are auto-incremented by the completion
  // callback, but deriving from the nodes array ensures the progress bar always
  // reflects reality even if counts are stale.
  const nodeProgress = useMemo(() => {
    const succeeded = nodes.filter((n) => n.status === 'succeeded').length
    const failed = nodes.filter((n) => n.status === 'failed').length
    const total = Math.max(run?.node_count ?? 0, nodes.length)
    return { succeeded, failed, total }
  }, [nodes, run?.node_count])

  // Fetch workflow spec for DAG: all nodes + dependency edges
  const workflowQuery = useDbWorkflow(run?.workflow_id ?? '')

  const specNodes = useMemo(() => workflowQuery.data?.nodes ?? [], [workflowQuery.data])

  const nodeStatusMap = useMemo(() => {
    const map: Record<string, { status: string; executorType?: string; progressMessage?: string | null }> = {}
    for (const n of nodes) {
      map[n.node_id] = {
        status: n.status,
        executorType: n.executor_type,
        progressMessage: n.progress_message,
      }
    }
    return map
  }, [nodes])


  if (runError) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <ErrorState
          message={runErrorObj instanceof Error ? runErrorObj.message : 'Failed to load flow run'}
          onRetry={() => void refetchRun()}
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1440px] px-5 py-5 lg:px-8">
      <Link
        to={
          fromWorkspace && run?.workflow_id
            ? workspaceReturnUrl
            : run?.workflow_id
              ? `/workflows/${run.workflow_id}`
              : '/'
        }
        className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-slate-500 transition-colors hover:text-slate-800"
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        {fromWorkspace ? '返回任务护航' : '返回工作流'}
      </Link>

      {runLoading ? (
        <div className="flex items-center justify-center py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      ) : run ? (
        <>
          <RunSummaryHeader run={run} nodeCount={nodeProgress.total} succeededCount={nodeProgress.succeeded} failedCount={nodeProgress.failed} />

          <div className="mt-4">
            <InterventionPanel
              flowId={flowId ?? ''}
              runStatus={run.status}
              nodes={nodes}
            />
          </div>

          <div className="mt-4">
            <EvolutionAnalysisPanel
              workflowId={run.workflow_id ?? ''}
              flowId={flowId ?? ''}
              run={run}
              diagnoses={diagnosesData?.diagnoses ?? []}
              suggestions={suggestionsData?.suggestions ?? []}
              diagnosesLoading={diagnosesLoading}
              suggestionsLoading={suggestionsLoading}
              onReanalyze={handleReanalyzeRun}
              onReset={() => resetAnalysisMutation.mutate(run.flow_id)}
              resetting={resetAnalysisMutation.isPending}
              analyzing={run.evolution_analysis_status === 'analyzing' || analyzeMutation.isPending}
              progress={analysisProgressQuery.data?.progress ?? null}
              progressError={analysisProgressQuery.isError}
              dispatchError={analyzeDispatchError}
              analysis={analysisResultQuery.data?.analysis ?? null}
              analysisLoading={analysisResultQuery.isLoading}
              analysisError={analysisResultQuery.isError ? analysisResultQuery.error : null}
              onRetryAnalysisResult={() => void analysisResultQuery.refetch()}
            />
          </div>

          {run && (
            <AnalyzeRunBotModal
              flowId={run.flow_id}
              workflowId={run.workflow_id}
              originBotId={run.origin_bot_id ? String(run.origin_bot_id).split(":")[0].trim() : null}
              analyzeMutation={analyzeMutation}
              isOpen={analyzeModalOpen}
              onClose={() => setAnalyzeModalOpen(false)}
            />
          )}
          <div className="mt-5 border-b border-slate-200">
            <nav className="-mb-px flex gap-5" aria-label="运行详情视图">
              <TabButton active={activeTab === 'nodes'} onClick={() => setActiveTab('nodes')} label="节点" />
              <TabButton active={activeTab === 'logs'} onClick={() => setActiveTab('logs')} label="日志" />
              <TabButton active={activeTab === 'dag'} onClick={() => setActiveTab('dag')} label="DAG" />
            </nav>
          </div>

          <div className="mt-4">
            {activeTab === 'nodes' && (
              <div className="space-y-4">
                <NodeExecutionList
                  nodes={nodes}
                  onSelectNode={handleNodeSelect}
                  selectedNodeId={selectedNodeId ?? undefined}
                  workflowSpec={workflowQuery.data}
                  onAnalyze={handleAnalyze}
                />
              </div>
            )}

            {activeTab === 'logs' && (
              <SimpleRunLogsPanel flowId={flowId ?? ''} nodes={nodes.map((n) => ({ node_id: n.node_id, node_title: n.node_title }))} />
            )}

            {activeTab === 'dag' && (
              <RunDagView
                specNodes={specNodes}
                nodeStatusMap={nodeStatusMap}
                onNodeClick={(nodeId) => {
                  setSelectedNodeId(nodeId)
                  setActiveTab('nodes')
                }}
              />
            )}


          </div>
        </>
      ) : (
        <ErrorState
          title="未找到运行记录"
          message={`未找到ID为: ${flowId}`}
        />
      )}
      {analyzingNode && (
        <AnalysisModal node={analyzingNode} onClose={handleCloseAnalysis} />
      )}
    </div>
  )
}


function EvolutionAnalysisPanel({
  flowId,
  workflowId,
  run,
  diagnoses,
  suggestions,
  diagnosesLoading,
  suggestionsLoading,
  onReanalyze,
  onReset,
  analyzing,
  progress,
  progressError,
  resetting,
  dispatchError,
  analysis,
  analysisLoading,
  analysisError,
  onRetryAnalysisResult,
}: {
  flowId: string
  workflowId: string
  run: Parameters<typeof RunSummaryHeader>[0]['run']
  diagnoses: { diagnosis_id: string; flow_id?: string | null; failure_signature?: string | null; failure_mode?: string | null; error_text?: string | null; reasoning?: string | null }[]
  suggestions: { id: string; evidenceRuns?: string[]; weakNode?: string; kind?: string; description?: string; status?: string }[]
  diagnosesLoading: boolean
  suggestionsLoading: boolean
  onReanalyze: () => void
  onReset: () => void
  analyzing: boolean
  progress: WorkflowAnalysisProgressResponse['progress']
  progressError: boolean
  resetting: boolean
  dispatchError?: string | null
  analysis: RunEvolutionAnalysisResponse | null
  analysisLoading: boolean
  analysisError: unknown
  onRetryAnalysisResult: () => void
}) {
  const flowDiagnoses = diagnoses.filter((d) => d.flow_id === flowId)
  const flowSuggestions = suggestions.filter((s) => !s.evidenceRuns || s.evidenceRuns.includes(flowId))
  const hasAnalyzed = run.evolution_analysis_status === 'completed' || run.evolution_analysis_status === 'failed'
  const evolutionUrl = (diagnosis?: RunEvolutionAnalysisResponse['diagnoses'][number]) => {
    const params = new URLSearchParams({ workflowId, tab: 'evolution', evoTab: 'diagnosis', runId: flowId })
    if (analysis?.analysisId) params.set('analysisId', analysis.analysisId)
    if (diagnosis?.failureSignature) params.set('issueSignature', diagnosis.failureSignature)
    return `/workflows/workspace?${params.toString()}`
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white px-4 py-3.5" aria-label="进化分析">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-slate-900">进化分析</h3>
          <AnalysisStatusBadge status={run.evolution_analysis_status} />
          {run.evolution_analyzed_at && Number(run.evolution_analyzed_at) > 0 && (
            <span className="text-[11px] text-slate-400">
              {new Date(Number(run.evolution_analyzed_at) * 1000).toLocaleString()}
            </span>
          )}</div>
          {!diagnosesLoading && !suggestionsLoading && !analyzing && (
            <p className="mt-1.5 text-xs text-slate-500">
              {flowDiagnoses.length > 0 || flowSuggestions.length > 0
                ? `${flowDiagnoses.length} 条诊断 · ${flowSuggestions.length} 条建议`
                : '当前运行暂无诊断或建议'}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {analyzing && (
            <button
              type="button"
              onClick={onReset}
              disabled={resetting}
              className="px-2 py-1.5 text-xs font-medium text-slate-500 hover:text-slate-800 disabled:opacity-60"
            >
              {resetting ? '取消中...' : '取消分析'}
            </button>
          )}
          <button
            type="button"
            onClick={onReanalyze}
            disabled={analyzing}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-60 ${flowSuggestions.length > 0 ? 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50' : 'bg-blue-600 text-white hover:bg-blue-700'}`}
          >
            {analyzing ? '分析中...' : hasAnalyzed ? '重新分析' : '分析'}
          </button>
          {(run as FlowRun & { evolution_analysis_error?: string | null }).evolution_analysis_error && !analyzing && (
            <span className="max-w-[260px] truncate text-[10px] text-red-600" title={String((run as FlowRun & { evolution_analysis_error?: string | null }).evolution_analysis_error)}>
              {String((run as FlowRun & { evolution_analysis_error?: string | null }).evolution_analysis_error)}
            </span>
          )}
          {flowSuggestions.length > 0 && !analysis && (
            <Link
              to={evolutionUrl()}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
            >
              查看建议
            </Link>
          )}
        </div>
      </div>

      {(diagnosesLoading || suggestionsLoading) && (
        <p className="mt-3 text-xs text-gray-500">加载诊断与建议...</p>
      )}

      {analyzing && (
        <div
          className="mt-3 rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2.5"
          role="status"
          aria-live="polite"
        >
          <div className="flex flex-wrap items-center gap-2 text-xs text-blue-700">
            <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border border-current border-t-transparent" />
            <span className="font-medium">{progress?.message ?? '正在等待 Bot 上报进度'}</span>
            {progress && (
              <span className="text-[11px] tabular-nums text-slate-400">
                已用时 {formatAnalysisElapsed(progress.elapsedMs)}
              </span>
            )}
          </div>
          {progress?.inputSummary && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
              <span>{`结构化事件 ${progress.inputSummary.evidenceIncluded}/${progress.inputSummary.evidenceTotal}${progress.inputSummary.evidenceStatus === 'complete' ? '' : '（不完整）'}· 节点 ${progress.inputSummary.nodeCount}（失败 ${progress.inputSummary.failedNodeCount}）· Trace ${progress.inputSummary.traceCount}`}</span>
              {progress.inputSummary.truncated && (
                <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">输入已截断</span>
              )}
            </div>
          )}
          {!progress && progressError && (
            <p className="mt-1.5 text-[11px] text-slate-500">进度暂不可用，分析仍在后台运行</p>
          )}
        </div>
      )}

      {dispatchError && (
        <p className="mt-3 text-xs text-red-600">分析任务派发失败：{dispatchError}</p>
      )}

      {!analyzing && analysisLoading && (
        <p className="mt-3 text-xs text-slate-500">加载本次分析结果...</p>
      )}

      {!analyzing && analysisError && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">
          <span>分析结果加载失败：{analysisError instanceof Error ? analysisError.message : String(analysisError)}</span>
          <button type="button" onClick={onRetryAnalysisResult} className="shrink-0 font-medium hover:text-red-900">重试</button>
        </div>
      )}

      {!analyzing && analysis?.status === 'completed' && (
        <div className="mt-3">
          <RunEvolutionAnalysis
            analysis={analysis}
            renderOptimizeLink={(diagnosis) => diagnosis.proposal ? (
              <Link
                to={evolutionUrl(diagnosis)}
                className="shrink-0 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
              >
                查看建议
              </Link>
            ) : null}
          />
        </div>
      )}

      {!analyzing && !analysisLoading && !analysis && !analysisError && !diagnosesLoading && !suggestionsLoading && flowDiagnoses.length === 0 && flowSuggestions.length === 0 && (
        <p className="mt-3 text-xs text-slate-500">点击“分析”开始诊断。</p>
      )}
    </section>
  )
}

function TabButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`border-b-2 px-1 py-3 text-sm font-medium transition-colors ${
        active
          ? 'border-blue-600 text-blue-600'
          : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
      }`}
    >
      {label}
    </button>
  )
}
