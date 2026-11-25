import { useEffect, useId, useRef, useState } from 'react'
import { useLangfuseTraces, useAnalyzeTrace } from '@avernet/workflow/web/api/hooks'
import type { AnalysisResult, NodeExecution } from '@avernet/clawweb-shared/web/types'
import { TraceDataPanel } from './analysis-trace-reader/TraceDataPanel'

type Phase = 'fetching' | 'analyzing' | 'done' | 'error'

interface AnalysisModalProps {
  node: NodeExecution
  onClose: () => void
}

export default function AnalysisModal({ node, onClose }: AnalysisModalProps) {
  const [phase, setPhase] = useState<Phase>('fetching')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [focusReading, setFocusReading] = useState(false)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const analysisId = useId()

  // Use embedded_session_key for Langfuse correlation — this is the node's
  // real session key in Langfuse (derived as parentKey:embedded:nodeId:flowId).
  // Fall back to session_id only for legacy data without embedded_session_key.
  const langfuseSessionId = node.embedded_session_key ?? node.session_id

  const tracesQuery = useLangfuseTraces({
    sessionId: langfuseSessionId!,
    from: node.started_at ?? undefined,
    to: node.completed_at ?? undefined,
  })

  const analysisMutation = useAnalyzeTrace()

  useEffect(() => {
    if (tracesQuery.status === 'error') {
      setPhase('error')
      setErrorMessage(tracesQuery.error instanceof Error ? tracesQuery.error.message : '获取 Langfuse 数据失败')
    } else if (tracesQuery.data && phase === 'fetching') {
      setPhase('analyzing')
      analysisMutation.mutate(
        {
          traceData: tracesQuery.data,
          nodeTitle: node.node_title ?? node.node_id,
          nodeId: node.node_id,
          nodeInput: node.input_json ?? undefined,
          nodeOutput: node.output_json ?? undefined,
          nodeError: node.error_text ?? undefined,
        },
        {
          onSuccess: () => setPhase('done'),
          onError: (err) => {
            setPhase('error')
            setErrorMessage(err instanceof Error ? err.message : '分析失败')
          },
        },
      )
    }
  }, [tracesQuery.status, tracesQuery.data, phase])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const opener = document.activeElement
    if (typeof dialog.showModal === 'function') dialog.showModal()
    else dialog.open = true
    return () => {
      if (typeof dialog.close === 'function') dialog.close()
      else dialog.open = false
      // React can remove the dialog before close() restores native focus.
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true })
    }
  }, [])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="fixed inset-0 m-0 h-full max-h-none w-full max-w-none overflow-hidden border-0 bg-background p-0 text-foreground backdrop:bg-foreground/50"
      onClick={(event) => event.stopPropagation()}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <div className="flex h-full flex-col">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-lg font-semibold [overflow-wrap:anywhere]">
              节点分析：{node.node_title || node.node_id}
            </h2>
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={langfuseSessionId ?? node.node_id}>
              {langfuseSessionId ?? node.node_id}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-md border border-border p-2 text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
            aria-label="关闭"
          >
            <svg aria-hidden="true" className="size-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-muted/50 px-4 py-2 text-xs sm:gap-3 sm:px-6">
          <PhaseStep label="获取追踪数据" active={phase === 'fetching'} done={!!tracesQuery.data} />
          <PhaseArrow />
          <PhaseStep label="AI 分析" active={phase === 'analyzing'} done={phase === 'done'} />
          <PhaseArrow />
          <PhaseStep label="完成" active={false} done={phase === 'done'} />
          {phase === 'error' && <span className="ml-auto text-destructive">失败</span>}
        </div>

        <div className={`grid min-h-0 flex-1 grid-cols-1 overflow-y-auto overscroll-contain lg:overflow-hidden ${focusReading ? '' : 'lg:grid-cols-[minmax(0,7fr)_minmax(0,3fr)]'}`}>
          <section aria-label="Langfuse 追踪数据" className="min-w-0 px-4 pt-4 pb-6 sm:px-6 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Langfuse 追踪数据</h3>
              <button
                type="button"
                aria-controls={analysisId}
                aria-expanded={!focusReading}
                onClick={() => setFocusReading((value) => !value)}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
              >
                <svg aria-hidden="true" className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d={focusReading ? 'M4 10h6V4m0 6L4 4m16 10h-6v6m0-6 6 6' : 'M14 10l6-6m-6 0h6v6M10 14l-6 6m6 0H4v-6'} />
                </svg>
                {focusReading ? '显示 AI 分析' : '专注阅读'}
              </button>
            </div>
            {tracesQuery.isLoading ? (
              <LoadingSpinner message="获取追踪数据..." />
            ) : tracesQuery.data ? (
              <TraceDataPanel data={tracesQuery.data} />
            ) : phase === 'error' ? (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {errorMessage ?? '无法获取追踪数据'}
              </div>
            ) : null}
          </section>

          <aside
            id={analysisId}
            aria-label="AI 分析结果"
            hidden={focusReading}
            className="min-w-0 border-t border-border bg-muted/35 p-4 [overflow-wrap:anywhere] sm:px-6 lg:min-h-0 lg:overflow-y-auto lg:overscroll-contain lg:border-t-0 lg:border-l"
          >
            <h3 className="mb-3 text-sm font-semibold">AI 分析结果</h3>
            {phase === 'fetching' ? (
              <LoadingSpinner message="等待追踪数据..." />
            ) : phase === 'analyzing' ? (
              <LoadingSpinner message="AI 分析中，请稍候..." />
            ) : phase === 'error' && !analysisMutation.data ? (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                <span className="font-medium">分析失败：</span>{errorMessage}
              </div>
            ) : analysisMutation.data ? (
              <AnalysisResultPanel result={analysisMutation.data} />
            ) : null}
          </aside>
        </div>
      </div>
    </dialog>
  )
}

function PhaseStep({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <span className={`flex items-center gap-1 ${active ? 'font-medium text-primary' : done ? 'text-success' : 'text-muted-foreground'}`}>
      {done && <span>&#10003;</span>}
      {active && (
        <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {label}
    </span>
  )
}

function PhaseArrow() {
  return <span aria-hidden="true" className="text-muted-foreground">&rarr;</span>
}

function LoadingSpinner({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
      <div className="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-border border-t-primary" />
      <span className="text-sm">{message}</span>
    </div>
  )
}

function AnalysisResultPanel({ result }: { result: AnalysisResult }) {
  return (
    <div className="space-y-4">
      {result.summary && (
        <div className="rounded-md border border-primary/20 bg-primary/5 px-4 py-3">
          <div className="mb-1 text-xs font-medium text-primary">摘要</div>
          <div className="text-sm text-foreground">{result.summary}</div>
        </div>
      )}

      {result.nodeSlice && (
        <div className="rounded-md bg-purple-50 px-4 py-3 border border-purple-100">
          <div className="font-medium text-purple-800 text-xs mb-2">节点切片分析</div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-purple-600 text-xs">输入完整度</span>
              <div className="flex items-center gap-1">
                <div className="h-1.5 flex-1 rounded-full bg-purple-200">
                  <div
                    className="h-1.5 rounded-full bg-purple-600"
                    style={{ width: `${result.nodeSlice.inputCompleteness}%` }}
                  />
                </div>
                <span className="font-medium text-purple-700 text-xs">{result.nodeSlice.inputCompleteness}%</span>
              </div>
            </div>
            <div>
              <span className="text-purple-600 text-xs">输出格式合规</span>
              <div className={`font-medium text-xs ${result.nodeSlice.outputFormatCompliant ? 'text-green-600' : 'text-red-600'}`}>
                {result.nodeSlice.outputFormatCompliant ? '✓ 合规' : '✗ 不合规'}
              </div>
            </div>
          </div>
          {result.nodeSlice.anomalies.length > 0 && (
            <div className="mt-2">
              <div className="text-purple-600 text-xs mb-1">异常信号</div>
              <ul className="space-y-1">
                {result.nodeSlice.anomalies.map((a, i) => (
                  <li key={i} className="flex items-start gap-1 text-purple-800 text-xs">
                    <span className="mt-0.5">⚠</span>
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {result.performance && (
        <div className="rounded-md bg-green-50 px-4 py-3 border border-green-100">
          <div className="font-medium text-green-800 text-xs mb-2">性能分析</div>
          <div className="space-y-2 text-sm">
            {result.performance.bottleneckObservation && (
              <div>
                <span className="text-green-600 text-xs">瓶颈观察</span>
                <div className="text-green-900 text-xs">{result.performance.bottleneckObservation}</div>
              </div>
            )}
            {result.performance.latencyBreakdown && (
              <div>
                <span className="text-green-600 text-xs">延迟分布</span>
                <div className="text-green-900 text-xs">{result.performance.latencyBreakdown}</div>
              </div>
            )}
            {result.performance.tokenEfficiency && (
              <div>
                <span className="text-green-600 text-xs">Token 效率</span>
                <div className="text-green-900 text-xs">{result.performance.tokenEfficiency}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {result.llmQuality && (
        <div className="rounded-md bg-orange-50 px-4 py-3 border border-orange-100">
          <div className="font-medium text-orange-800 text-xs mb-2">LLM 输出质量</div>
          <div className="flex gap-4 text-sm">
            <div>
              <span className="text-orange-600 text-xs">格式合规</span>
              <div className={`font-medium text-xs ${result.llmQuality.formatCompliant ? 'text-green-600' : 'text-red-600'}`}>
                {result.llmQuality.formatCompliant ? '✓' : '✗'}
              </div>
            </div>
            <div>
              <span className="text-orange-600 text-xs">幻觉风险</span>
              <div className={`font-medium text-xs ${result.llmQuality.hallucinationRisk ? 'text-red-600' : 'text-green-600'}`}>
                {result.llmQuality.hallucinationRisk ? '⚠ 有风险' : '✓ 无风险'}
              </div>
            </div>
            <div>
              <span className="text-orange-600 text-xs">工具调用</span>
              <div className="font-medium text-orange-700 text-xs">{result.llmQuality.toolCallCount} 次</div>
            </div>
          </div>
        </div>
      )}

      {result.analysis && (
        <div className="rounded-md border border-border bg-card px-4 py-3">
          <div className="mb-2 text-xs font-medium text-muted-foreground">详细分析</div>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-card-foreground">{result.analysis}</div>
        </div>
      )}

      {result.suggestions.length > 0 && (
        <div className="rounded-md bg-amber-50 px-4 py-3 border border-amber-100">
          <div className="font-medium text-amber-800 text-xs mb-2">改进建议</div>
          <ul className="space-y-2">
            {result.suggestions.map((suggestion, i) => (
              <li key={i} className="flex items-start gap-2 text-amber-900 text-sm">
                <span className="mt-0.5 flex-shrink-0 rounded-full bg-amber-200 px-1.5 py-0.5 text-amber-700 text-xs font-medium">
                  {i + 1}
                </span>
                <span>{suggestion}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}