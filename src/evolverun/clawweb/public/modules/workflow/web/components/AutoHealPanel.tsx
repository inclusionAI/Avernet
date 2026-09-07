import { useState, useCallback, useEffect } from 'react'
import { useAutoHealDiagnose, useAutoHealApply, useAutoHealRun } from '@avernet/workflow/web/api/hooks'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import type { FlowRun, AutoHealDiagnosisResult, AutoHealDiffItem, AutoHealErrorChainItem, AutoHealFixSuggestion } from '@avernet/clawweb-shared/web/types'
import { formatDuration } from '@avernet/workflow/web/utils/time'

type Phase = 'idle' | 'diagnosing' | 'diagnosed' | 'applying' | 'applied' | 'running' | 'error'

interface AutoHealPanelProps {
  run: FlowRun
  onClose: () => void
  onRerunComplete?: () => void
}

export default function AutoHealPanel({ run, onClose, onRerunComplete }: AutoHealPanelProps) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [customPrompt, setCustomPrompt] = useState('')
  const [diagnosisResult, setDiagnosisResult] = useState<AutoHealDiagnosisResult | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [showCustomPrompt, setShowCustomPrompt] = useState(false)
  const [diffExpanded, setDiffExpanded] = useState(true)
  const [errorChainExpanded, setErrorChainExpanded] = useState(true)
  const [suggestionsExpanded, setSuggestionsExpanded] = useState(true)

  const diagnoseMutation = useAutoHealDiagnose()
  const applyMutation = useAutoHealApply()
  const runMutation = useAutoHealRun()

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const handleDiagnose = useCallback(() => {
    if (!run.flow_id) {
      setPhase('error')
      setErrorMessage('运行记录缺少 flow_id，无法发起诊断。请刷新页面后重试。')
      return
    }

    setPhase('diagnosing')
    setErrorMessage(null)
    setDiagnosisResult(null)

    diagnoseMutation.mutate(
      {
        flowId: run.flow_id,
        useBaas: true,
        customPrompt: customPrompt.trim() || undefined,
      },
      {
        onSuccess: (data) => {
          setDiagnosisResult(data)
          setPhase('diagnosed')
        },
        onError: (err) => {
          setPhase('error')
          setErrorMessage(extractErrorMessage(err, '诊断失败，请稍后重试'))
        },
      },
    )
  }, [run.flow_id, customPrompt, diagnoseMutation])

  // Track elapsed time while diagnosing for progress display
  const [diagnosingElapsed, setDiagnosingElapsed] = useState(0)
  useEffect(() => {
    if (phase !== 'diagnosing') {
      setDiagnosingElapsed(0)
      return
    }
    const startTime = Date.now()
    const timer = setInterval(() => {
      setDiagnosingElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    return () => clearInterval(timer)
  }, [phase])

  const handleApply = useCallback(() => {
    if (!diagnosisResult?.fixedSpec) return

    setPhase('applying')
    setErrorMessage(null)

    applyMutation.mutate(
      {
        workflowId: diagnosisResult.workflowId,
        spec: diagnosisResult.fixedSpec,
        diagnosisId: diagnosisResult.diagnosisId,
        autoRun: false,
      },
      {
        onSuccess: () => {
          setPhase('applied')
        },
        onError: (err) => {
          setPhase('error')
          setErrorMessage(extractErrorMessage(err, '应用修复失败'))
        },
      },
    )
  }, [diagnosisResult, applyMutation])

  const handleRerun = useCallback(() => {
    if (!diagnosisResult) return

    setPhase('running')
    setErrorMessage(null)

    runMutation.mutate(
      { workflowId: diagnosisResult.workflowId },
      {
        onSuccess: () => {
          setPhase('idle')
          setDiagnosisResult(null)
          setCustomPrompt('')
          onRerunComplete?.()
        },
        onError: (err) => {
          setPhase('error')
          setErrorMessage(extractErrorMessage(err, '重新运行失败'))
        },
      },
    )
  }, [diagnosisResult, runMutation, onRerunComplete])

  const handleReset = useCallback(() => {
    setPhase('idle')
    setDiagnosisResult(null)
    setErrorMessage(null)
    setCustomPrompt('')
  }, [])

  const isFailed = ['failed', 'blocked', 'waiting'].includes(run.status)

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <PanelHeader run={run} onClose={onClose} />

        {/* Phase Indicator */}
        <PhaseIndicator phase={phase} />

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {/* Error */}
          {phase === 'error' && errorMessage && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
              <div className="flex items-center gap-2 mb-1">
                <svg className="h-4 w-4 text-red-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
                </svg>
                <span className="font-medium text-red-800 text-sm">操作失败</span>
              </div>
              <p className="text-red-700 text-sm">{errorMessage}</p>
            </div>
          )}

          {/* Idle: Diagnosis Init */}
          {phase === 'idle' && (
            <IdleContent
              run={run}
              isFailed={isFailed}
              customPrompt={customPrompt}
              setCustomPrompt={setCustomPrompt}
              showCustomPrompt={showCustomPrompt}
              setShowCustomPrompt={setShowCustomPrompt}
              onDiagnose={handleDiagnose}
            />
          )}

          {/* Diagnosing */}
          {phase === 'diagnosing' && <DiagnosingContent elapsed={diagnosingElapsed} />}

          {/* Diagnosed: Results */}
          {(phase === 'diagnosed' || phase === 'error') && diagnosisResult && (
            <DiagnosisResults
              result={diagnosisResult}
              diffExpanded={diffExpanded}
              setDiffExpanded={setDiffExpanded}
              errorChainExpanded={errorChainExpanded}
              setErrorChainExpanded={setErrorChainExpanded}
              suggestionsExpanded={suggestionsExpanded}
              setSuggestionsExpanded={setSuggestionsExpanded}
            />
          )}

          {/* Applied: Success state */}
          {phase === 'applied' && (
            <div className="rounded-lg border border-green-200 bg-green-50 px-5 py-4 text-center">
              <svg className="mx-auto mb-2 h-10 w-10 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h3 className="font-semibold text-green-800">修复已应用</h3>
              <p className="mt-1 text-green-700 text-sm">工作流 YAML 已更新。你可以重新运行工作流来验证修复效果。</p>
            </div>
          )}

          {/* Running */}
          {phase === 'running' && (
            <div className="flex flex-col items-center py-12 text-gray-400">
              <div className="mb-3 h-10 w-10 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
              <p className="font-medium text-gray-600 text-sm">正在重新运行工作流…</p>
              <p className="text-xs">请稍候</p>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <PanelFooter
          phase={phase}
          isFailed={isFailed}
          hasFixedSpec={!!diagnosisResult?.fixedSpec}
          onDiagnose={handleDiagnose}
          onApply={handleApply}
          onRerun={handleRerun}
          onReset={handleReset}
          isDiagnosing={diagnoseMutation.isPending}
          isApplying={applyMutation.isPending}
          isRunning={runMutation.isPending}
        />
      </div>
    </div>
  )
}

// ── Sub-Components ──────────────────────────────────────────────

function PanelHeader({ run, onClose }: { run: FlowRun; onClose: () => void }) {
  return (
    <div className="flex items-center justify-between border-b border-gray-200 bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <svg className="h-5 w-5 text-white/80" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
          </svg>
          <h2 className="font-bold text-white text-lg">自动修复</h2>
        </div>
        <div className="mt-1 flex items-center gap-3">
          <span className="font-mono text-white/70 text-xs">{run.flow_id}</span>
          <StatusBadge status={run.status} />
        </div>
        <div className="mt-1 flex items-center gap-4 text-white/60 text-xs">
          <span>{run.workflow_title || run.workflow_id}</span>
          {run.total_duration_ms != null && <span>{formatDuration(run.total_duration_ms)}</span>}
          <span>{run.node_count} 节点</span>
          {run.failed_count > 0 && <span className="text-red-300">{run.failed_count} 失败</span>}
        </div>
      </div>
      <button
        onClick={onClose}
        className="ml-4 rounded-md p-1.5 text-white/70 transition-colors hover:bg-white/10 hover:text-white"
        title="关闭"
      >
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  )
}

function PhaseIndicator({ phase }: { phase: Phase }) {
  const steps = [
    { key: 'diagnose', label: '诊断', active: phase === 'diagnosing', done: ['diagnosed', 'applying', 'applied', 'running'].includes(phase) },
    { key: 'apply', label: '修复', active: phase === 'applying', done: ['applied', 'running'].includes(phase) },
    { key: 'rerun', label: '重跑', active: phase === 'running', done: phase === 'running' },
  ]

  return (
    <div className="flex items-center gap-3 border-b border-gray-100 bg-gray-50 px-6 py-2.5 text-xs">
      {steps.map((step, i) => (
        <span key={step.key} className="flex items-center gap-1">
          <StepDot active={step.active} done={step.done} />
          <span className={step.active ? 'font-medium text-blue-600' : step.done ? 'text-green-600' : 'text-gray-400'}>
            {step.label}
          </span>
          {i < steps.length - 1 && <span className="mx-1 text-gray-300">→</span>}
        </span>
      ))}
      {phase === 'error' && <span className="ml-auto font-medium text-red-500">失败</span>}
      {phase === 'applied' && <span className="ml-auto font-medium text-green-600">修复已应用</span>}
    </div>
  )
}

function StepDot({ active, done }: { active: boolean; done: boolean }) {
  if (done) return <span className="text-green-600">✓</span>
  if (active) return (
    <svg className="h-3 w-3 animate-spin text-blue-600" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
  return <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
}

interface IdleContentProps {
  run: FlowRun
  isFailed: boolean
  customPrompt: string
  setCustomPrompt: (v: string) => void
  showCustomPrompt: boolean
  setShowCustomPrompt: (v: boolean) => void
  onDiagnose: () => void
}

function IdleContent({ run, isFailed, customPrompt, setCustomPrompt, showCustomPrompt, setShowCustomPrompt, onDiagnose }: IdleContentProps) {
  if (!isFailed) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-6 text-center">
        <svg className="mx-auto mb-2 h-8 w-8 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        <p className="font-medium text-amber-800">当前运行状态为 <StatusBadge status={run.status} /></p>
        <p className="mt-1 text-amber-700 text-sm">自动修复仅支持失败（failed）、阻塞（blocked）或等待中（waiting）的工作流。</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white px-5 py-4">
        <h3 className="font-medium text-gray-800 text-sm">运行概览</h3>
        <div className="mt-3 grid grid-cols-3 gap-4">
          <RunStat label="状态" value={<StatusBadge status={run.status} />} />
          <RunStat label="节点" value={`${run.node_count} 个`} />
          <RunStat label="失败" value={`${run.failed_count} 个`} valueClassName="text-red-600" />
          <RunStat label="成功" value={`${run.succeeded_count} 个`} valueClassName="text-green-600" />
          <RunStat label="耗时" value={run.total_duration_ms != null ? formatDuration(run.total_duration_ms) : '—'} />
          <RunStat label="触发" value={run.triggered_by || '—'} />
        </div>
      </div>

      {/* Custom Prompt Toggle */}
      <div>
        <button
          onClick={() => setShowCustomPrompt(!showCustomPrompt)}
          className="flex items-center gap-1.5 text-gray-600 text-xs hover:text-gray-800 transition-colors"
        >
          <svg
            className={`h-3 w-3 transition-transform ${showCustomPrompt ? 'rotate-90' : ''}`}
            fill="currentColor" viewBox="0 0 20 20"
          >
            <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
          </svg>
          <span className="font-medium">自定义诊断提示</span>
          <span className="text-gray-400">（可选）</span>
        </button>
        {showCustomPrompt && (
          <textarea
            value={customPrompt}
            onChange={(e) => setCustomPrompt(e.target.value)}
            placeholder="输入额外的诊断提示，例如：重点关注 API 调用超时问题…"
            rows={3}
            maxLength={2000}
            className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        )}
      </div>

      {/* What happens */}
      <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-5 py-4">
        <h4 className="font-medium text-blue-800 text-sm mb-2">诊断流程说明</h4>
        <ol className="space-y-1.5 text-blue-700 text-xs">
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">1</span>
            AI 读取运行数据和原始工作流 YAML
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">2</span>
            分析失败节点的错误链路和根因
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">3</span>
            生成修复后的 YAML 并展示变更对比
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">4</span>
            确认后一键应用修复，并可重新运行验证
          </li>
        </ol>
      </div>
    </div>
  )
}

function RunStat({ label, value, valueClassName = '' }: { label: string; value: React.ReactNode; valueClassName?: string }) {
  return (
    <div>
      <div className="text-gray-500 text-xs">{label}</div>
      <div className={`font-medium text-sm mt-0.5 ${valueClassName}`}>{value}</div>
    </div>
  )
}

function DiagnosingContent({ elapsed }: { elapsed: number }) {
  // Progress stages based on elapsed time
  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60
  const timeStr = minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`

  // Animate through stages
  const stage = elapsed < 10 ? 1 : elapsed < 30 ? 2 : elapsed < 60 ? 3 : 4

  return (
    <div className="flex flex-col items-center py-16 text-gray-400">
      <div className="mb-4 h-12 w-12 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
      <p className="font-medium text-gray-600 text-sm">AI 正在分析运行数据…</p>
      <p className="mt-1 text-xs">已等待 {timeStr}，BaaS Bot 异步处理中</p>
      <div className="mt-6 space-y-2 text-left w-full max-w-xs">
        <DiagnoseStep label="提交诊断请求" done />
        <DiagnoseStep label="读取工作流 YAML" done={stage >= 2} active={stage === 1} />
        <DiagnoseStep label="分析错误链路" done={stage >= 3} active={stage === 2} />
        <DiagnoseStep label="生成修复建议" done={stage >= 4} active={stage === 3} />
      </div>
      {elapsed > 30 && (
        <p className="mt-4 text-xs text-amber-500">诊断可能需要 1-5 分钟，请耐心等待…</p>
      )}
    </div>
  )
}

function DiagnoseStep({ label, active, done }: { label: string; active?: boolean; done?: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-xs ${active ? 'text-blue-600 font-medium' : done ? 'text-green-600' : 'text-gray-400'}`}>
      {done && <span>✓</span>}
      {active && (
        <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {!done && !active && <span className="h-1 w-1 rounded-full bg-gray-300" />}
      <span>{label}</span>
    </div>
  )
}

interface DiagnosisResultsProps {
  result: AutoHealDiagnosisResult
  diffExpanded: boolean
  setDiffExpanded: (v: boolean) => void
  errorChainExpanded: boolean
  setErrorChainExpanded: (v: boolean) => void
  suggestionsExpanded: boolean
  setSuggestionsExpanded: (v: boolean) => void
}

function DiagnosisResults({ result, diffExpanded, setDiffExpanded, errorChainExpanded, setErrorChainExpanded, suggestionsExpanded, setSuggestionsExpanded }: DiagnosisResultsProps) {
  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-5 py-4">
        <h3 className="mb-1 font-semibold text-blue-900 text-sm">🔍 诊断摘要</h3>
        <p className="text-blue-800 text-sm leading-relaxed">{result.summary}</p>
      </div>

      {/* Error Chain */}
      {result.errorChain.length > 0 && (
        <CollapsibleCard
          title={`🐛 错误链路 (${result.errorChain.length})`}
          expanded={errorChainExpanded}
          onToggle={() => setErrorChainExpanded(!errorChainExpanded)}
        >
          <div className="space-y-2">
            {result.errorChain.map((item, i) => (
              <ErrorChainItem key={item.nodeId} item={item} index={i} />
            ))}
          </div>
        </CollapsibleCard>
      )}

      {/* Fix Suggestions */}
      {result.fixSuggestions.length > 0 && (
        <CollapsibleCard
          title={`💡 修复建议 (${result.fixSuggestions.length})`}
          expanded={suggestionsExpanded}
          onToggle={() => setSuggestionsExpanded(!suggestionsExpanded)}
        >
          <div className="space-y-2">
            {result.fixSuggestions.map((s, i) => (
              <FixSuggestionItem key={`${s.nodeId}-${s.field}`} suggestion={s} index={i} />
            ))}
          </div>
        </CollapsibleCard>
      )}

      {/* Yaml Diff */}
      {result.diff.length > 0 && result.fixedYaml && (
        <CollapsibleCard
          title={`📝 YAML 变更对比 (${result.diff.length} 处)`}
          expanded={diffExpanded}
          onToggle={() => setDiffExpanded(!diffExpanded)}
          defaultColor="indigo"
        >
          <DiffView diff={result.diff} />
          {/* Full YAML preview */}
          <details className="mt-3">
            <summary className="cursor-pointer text-gray-600 text-xs hover:text-gray-800">查看完整修复后 YAML</summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-gray-900 p-3 font-mono text-xs text-green-400 whitespace-pre-wrap">
              {result.fixedYaml}
            </pre>
          </details>
        </CollapsibleCard>
      )}

      {/* No Fix Available */}
      {!result.fixedYaml && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-4 text-center">
          <p className="font-medium text-amber-800 text-sm">AI 未能生成可自动应用的修复</p>
          <p className="mt-1 text-amber-700 text-xs">请参考上方的错误链路和修复建议手动调整工作流。</p>
        </div>
      )}

      {/* Raw Response */}
      {result.rawResponse && (
        <details>
          <summary className="cursor-pointer text-gray-400 text-xs hover:text-gray-600">查看 AI 原始回复</summary>
          <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-gray-50 p-3 font-mono text-xs text-gray-600 whitespace-pre-wrap border border-gray-200">
            {result.rawResponse}
          </pre>
        </details>
      )}
    </div>
  )
}

function ErrorChainItem({ item, index }: { item: AutoHealErrorChainItem; index: number }) {
  return (
    <div className="rounded-md border border-red-100 bg-red-50/50 px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="rounded-full bg-red-200 px-1.5 py-0.5 text-red-800 text-[10px] font-bold">{index + 1}</span>
        <span className="font-medium text-gray-900 text-sm">{item.nodeTitle || item.nodeId}</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600 text-[10px] font-mono">{item.executorType}</span>
      </div>
      {item.errorText && (
        <pre className="mt-1 max-h-24 overflow-auto rounded bg-red-100 px-2.5 py-1.5 font-mono text-xs text-red-800 whitespace-pre-wrap">
          {item.errorText.length > 500 ? item.errorText.slice(0, 500) + '…' : item.errorText}
        </pre>
      )}
      {item.analysis && (
        <p className="mt-1.5 text-gray-700 text-xs leading-relaxed">{item.analysis}</p>
      )}
    </div>
  )
}

function FixSuggestionItem({ suggestion, index }: { suggestion: AutoHealFixSuggestion; index: number }) {
  return (
    <div className="rounded-md border border-amber-100 bg-amber-50/30 px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="rounded-full bg-amber-200 px-1.5 py-0.5 text-amber-800 text-[10px] font-bold">{index + 1}</span>
        <span className="font-mono text-gray-700 text-xs">{suggestion.nodeId}.{suggestion.field}</span>
      </div>
      {suggestion.oldValue != null && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-500">原值：</span>
          <code className="rounded bg-red-100 px-1.5 py-0.5 text-red-700 font-mono text-[11px]">
            {truncate(String(suggestion.oldValue), 80)}
          </code>
        </div>
      )}
      {suggestion.newValue != null && (
        <div className="flex items-center gap-2 text-xs mt-0.5">
          <span className="text-gray-500">新值：</span>
          <code className="rounded bg-green-100 px-1.5 py-0.5 text-green-700 font-mono text-[11px]">
            {truncate(String(suggestion.newValue), 80)}
          </code>
        </div>
      )}
      <p className="mt-1 text-gray-600 text-xs">{suggestion.reason}</p>
    </div>
  )
}

function DiffView({ diff }: { diff: AutoHealDiffItem[] }) {
  return (
    <div className="space-y-1.5">
      {diff.map((item, i) => (
        <div key={i} className={`rounded-md border px-3 py-2 text-xs ${
          item.type === 'add' ? 'border-green-200 bg-green-50' :
          item.type === 'remove' ? 'border-red-200 bg-red-50' :
          'border-blue-200 bg-blue-50'
        }`}>
          <div className="flex items-center gap-2 mb-0.5">
            <span className={`rounded px-1 py-0.5 font-bold text-[10px] uppercase ${
              item.type === 'add' ? 'bg-green-200 text-green-800' :
              item.type === 'remove' ? 'bg-red-200 text-red-800' :
              'bg-blue-200 text-blue-800'
            }`}>
              {item.type === 'add' ? '新增' : item.type === 'remove' ? '删除' : '修改'}
            </span>
            <span className="font-mono text-gray-700">{item.path}</span>
          </div>
          {item.type === 'change' && (
            <div className="flex items-center gap-2 font-mono text-[11px]">
              <span className="text-red-700 line-through">{truncate(String(item.oldValue ?? ''), 60)}</span>
              <span className="text-gray-400">→</span>
              <span className="text-green-700">{truncate(String(item.newValue ?? ''), 60)}</span>
            </div>
          )}
          {item.type === 'add' && item.value && (
            <code className="text-green-700 font-mono text-[11px]">{truncate(String(item.value), 80)}</code>
          )}
          {item.type === 'remove' && item.oldValue && (
            <code className="text-red-700 font-mono text-[11px] line-through">{truncate(String(item.oldValue), 80)}</code>
          )}
        </div>
      ))}
    </div>
  )
}

function CollapsibleCard({ title, expanded, onToggle, defaultColor = 'gray', children }: {
  title: string
  expanded: boolean
  onToggle: () => void
  defaultColor?: 'gray' | 'indigo'
  children: React.ReactNode
}) {
  const borderColor = defaultColor === 'indigo' ? 'border-indigo-200' : 'border-gray-200'
  return (
    <div className={`rounded-lg border ${borderColor} bg-white shadow-sm`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-5 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <span className="font-semibold text-gray-900 text-sm">{title}</span>
        <svg
          className={`h-4 w-4 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && <div className="border-t border-gray-100 px-5 py-4">{children}</div>}
    </div>
  )
}

interface PanelFooterProps {
  phase: Phase
  isFailed: boolean
  hasFixedSpec: boolean
  onDiagnose: () => void
  onApply: () => void
  onRerun: () => void
  onReset: () => void
  isDiagnosing: boolean
  isApplying: boolean
  isRunning: boolean
}

function PanelFooter({ phase, isFailed, hasFixedSpec, onDiagnose, onApply, onRerun, onReset, isDiagnosing, isApplying, isRunning }: PanelFooterProps) {
  return (
    <div className="border-t border-gray-200 bg-gray-50 px-6 py-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          {(phase === 'diagnosed' || phase === 'applied' || phase === 'error') && (
            <button
              onClick={onReset}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              重新诊断
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Diagnose button */}
          {phase === 'idle' && isFailed && (
            <button
              onClick={onDiagnose}
              disabled={isDiagnosing}
              className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              开始诊断
            </button>
          )}

          {/* Apply button */}
          {(phase === 'diagnosed' || (phase === 'error' && hasFixedSpec)) && (
            <button
              onClick={onApply}
              disabled={isApplying || !hasFixedSpec}
              className="inline-flex items-center gap-2 rounded-md bg-orange-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              {isApplying ? '应用中…' : '应用修复'}
            </button>
          )}

          {/* Rerun button */}
          {phase === 'applied' && (
            <button
              onClick={onRerun}
              disabled={isRunning}
              className="inline-flex items-center gap-2 rounded-md bg-green-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {isRunning ? '运行中…' : '重新运行'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/** Extract a human-readable error message from API errors.
 *  Handles `ApiError` format: "API 400: {"error":"Bad Request","message":"flowId is required"}"
 */
function extractErrorMessage(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback
  const msg = err.message
  // Try to parse "API NNN: {...}" pattern from fetchJson's ApiError
  const jsonMatch = msg.match(/^API \d+:\s*(\{.*\})$/s)
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[1])
      if (parsed.message) return parsed.message
    } catch { /* not valid JSON, fall through */ }
  }
  return msg || fallback
}

function truncate(str: string, maxLen: number): string {
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}