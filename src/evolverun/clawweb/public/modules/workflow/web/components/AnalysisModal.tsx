import { useState, useEffect, useCallback } from 'react'
import { useLangfuseTraces, useAnalyzeTrace } from '@avernet/workflow/web/api/hooks'
import type { NodeExecution, LangfuseTrace, LangfuseObservation, AnalysisResult } from '@avernet/clawweb-shared/web/types'
import { formatTimeShort } from '@avernet/workflow/web/utils/time'

type Phase = 'fetching' | 'analyzing' | 'done' | 'error'

interface AnalysisModalProps {
  node: NodeExecution
  onClose: () => void
}

export default function AnalysisModal({ node, onClose }: AnalysisModalProps) {
  const [phase, setPhase] = useState<Phase>('fetching')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

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
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex bg-black/50" onClick={onClose}>
      <div
        className="flex h-full w-full flex-col bg-white"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-200 px-6 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold text-gray-900">
              节点分析：{node.node_title || node.node_id}
            </h2>
            <p className="truncate font-mono text-gray-400 text-xs">
              {langfuseSessionId ?? node.node_id}
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-4 rounded-md border border-gray-200 bg-white p-1.5 text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-700"
            title="关闭"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Phase indicator */}
        <div className="flex items-center gap-3 border-b border-gray-100 bg-gray-50 px-6 py-2 text-xs">
          <PhaseStep label="获取追踪数据" active={phase === 'fetching'} done={phase !== 'fetching' && phase !== 'error' || !!(tracesQuery.data)} />
          <PhaseArrow />
          <PhaseStep label="AI 分析" active={phase === 'analyzing'} done={phase === 'done'} />
          <PhaseArrow />
          <PhaseStep label="完成" active={phase === 'done'} done={phase === 'done'} />

          {phase === 'error' && (
            <span className="ml-auto text-red-600">失败</span>
          )}
        </div>

        {/* Two-panel content */}
        <div className="flex flex-1 overflow-hidden">
          {/* Left panel: Raw trace data */}
          <div className="w-1/2 overflow-auto border-r border-gray-200 p-4">
            <h3 className="mb-3 font-medium text-gray-700 text-sm">Langfuse 追踪数据</h3>
            {tracesQuery.isLoading ? (
              <LoadingSpinner message="获取追踪数据..." />
            ) : tracesQuery.data ? (
              <TraceDataPanel data={tracesQuery.data} />
            ) : phase === 'error' && !tracesQuery.data ? (
              <div className="rounded-md bg-red-50 px-3 py-2 text-red-700 text-xs">
                {errorMessage ?? '无法获取追踪数据'}
              </div>
            ) : null}
          </div>

          {/* Right panel: LLM analysis */}
          <div className="w-1/2 overflow-auto p-4">
            <h3 className="mb-3 font-medium text-gray-700 text-sm">AI 分析结果</h3>
            {phase === 'fetching' ? (
              <LoadingSpinner message="等待追踪数据..." />
            ) : phase === 'analyzing' ? (
              <LoadingSpinner message="AI 分析中，请稍候..." />
            ) : phase === 'error' && !analysisMutation.data ? (
              <div className="rounded-md bg-red-50 px-3 py-2 text-red-700 text-xs">
                <span className="font-medium">分析失败：</span>{errorMessage}
              </div>
            ) : analysisMutation.data ? (
              <AnalysisResultPanel result={analysisMutation.data} />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function PhaseStep({ label, active, done }: { label: string; active: boolean; done: boolean }) {
  return (
    <span className={`flex items-center gap-1 ${active ? 'font-medium text-blue-600' : done ? 'text-green-600' : 'text-gray-400'}`}>
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
  return <span className="text-gray-300">&rarr;</span>
}

function LoadingSpinner({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-gray-400">
      <div className="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      <span className="text-sm">{message}</span>
    </div>
  )
}

function TraceDataPanel({ data }: { data: { data: LangfuseTrace[]; meta?: { totalItems?: number } } }) {
  const traces = data.data ?? []
  if (traces.length === 0) {
    return <p className="text-gray-400 text-sm">未找到追踪数据</p>
  }

  return (
    <div className="space-y-3">
      {data.meta?.totalItems != null && (
        <p className="text-gray-400 text-xs">共 {data.meta.totalItems} 条追踪</p>
      )}
      {traces.map((trace) => (
        <TraceCard key={trace.id} trace={trace} />
      ))}
    </div>
  )
}

function TraceCard({ trace }: { trace: LangfuseTrace }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-2 text-left transition-colors hover:bg-gray-50"
      >
        <div className="min-w-0 flex-1">
          <div className="font-medium text-gray-800 text-sm truncate">{trace.name || trace.id}</div>
          <div className="text-gray-400 text-xs">
            {formatTimeShort(trace.startTime)}
            {trace.latencyMs != null && <span className="ml-2">{(trace.latencyMs / 1000).toFixed(1)}s</span>}
          </div>
        </div>
        <svg
          className={`h-4 w-4 flex-shrink-0 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-gray-100 px-3 py-2 space-y-2">
          {trace.metadata && (
            <JsonSection title="Metadata" data={trace.metadata} />
          )}
          {trace.input != null && (
            <JsonSection title="Input" data={trace.input} />
          )}
          {trace.output != null && (
            <JsonSection title="Output" data={trace.output} />
          )}
          {trace.observations && trace.observations.length > 0 && (
            <ObservationsSection observations={trace.observations} />
          )}
          {trace.scores && trace.scores.length > 0 && (
            <div>
              <div className="font-medium text-gray-600 text-xs mb-1">Scores</div>
              <div className="flex flex-wrap gap-2">
                {trace.scores.map((score) => (
                  <span key={score.id} className="rounded-full bg-gray-100 px-2 py-0.5 text-gray-600 text-xs">
                    {score.name}: {score.value}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ObservationsSection({ observations }: { observations: LangfuseObservation[] }) {
  return (
    <div>
      <div className="font-medium text-gray-600 text-xs mb-1">Observations ({observations.length})</div>
      <div className="space-y-1">
        {observations.map((obs) => (
          <ObservationCard key={obs.id} obs={obs} />
        ))}
      </div>
    </div>
  )
}

function ObservationCard({ obs }: { obs: LangfuseObservation }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded border border-gray-100 bg-gray-50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-2 py-1.5 text-left transition-colors hover:bg-gray-100"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2 text-xs">
          <span className={`rounded px-1 py-0.5 font-medium ${obs.type === 'generation' ? 'bg-purple-100 text-purple-700' : obs.type === 'span' ? 'bg-blue-100 text-blue-700' : 'bg-green-100 text-green-700'}`}>
            {obs.type}
          </span>
          <span className="text-gray-700 font-medium truncate">{obs.name || obs.id}</span>
          {obs.model && <span className="text-gray-400 font-mono shrink-0">{obs.model}</span>}
          {obs.latencyMs != null && <span className="text-gray-400 shrink-0">{(obs.latencyMs / 1000).toFixed(1)}s</span>}
          {obs.totalTokens != null && <span className="text-gray-400 shrink-0">{obs.totalTokens} tokens</span>}
        </div>
        <svg
          className={`h-3.5 w-3.5 flex-shrink-0 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-gray-200 px-2 py-2 space-y-2 text-xs">
          {/* Metadata row */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <div>
              <span className="text-gray-400">ID: </span>
              <span className="font-mono text-gray-600 break-all">{obs.id}</span>
            </div>
            {obs.startTime && (
              <div>
                <span className="text-gray-400">开始: </span>
                <span className="text-gray-600">{formatTimeShort(obs.startTime)}</span>
              </div>
            )}
            {obs.endTime && (
              <div>
                <span className="text-gray-400">结束: </span>
                <span className="text-gray-600">{formatTimeShort(obs.endTime)}</span>
              </div>
            )}
            {obs.latencyMs != null && (
              <div>
                <span className="text-gray-400">耗时: </span>
                <span className="text-gray-600">{obs.latencyMs >= 1000 ? `${(obs.latencyMs / 1000).toFixed(2)}s` : `${obs.latencyMs.toFixed(0)}ms`}</span>
              </div>
            )}
            {obs.model && (
              <div>
                <span className="text-gray-400">模型: </span>
                <span className="font-mono text-gray-600">{obs.model}</span>
              </div>
            )}
            {(obs.promptTokens != null || obs.completionTokens != null || obs.totalTokens != null) && (
              <div className="col-span-2">
                <span className="text-gray-400">Token: </span>
                <span className="text-gray-600">
                  {[
                    obs.promptTokens != null && `输入 ${obs.promptTokens}`,
                    obs.completionTokens != null && `输出 ${obs.completionTokens}`,
                    obs.totalTokens != null && `合计 ${obs.totalTokens}`,
                  ].filter(Boolean).join(' / ')}
                </span>
              </div>
            )}
          </div>

          {obs.statusMessage && (
            <div className="rounded bg-red-50 px-2 py-1 text-red-600 text-xs">
              <span className="font-medium">状态消息: </span>{obs.statusMessage}
            </div>
          )}

          {obs.input != null && (
            <JsonSection title="Input" data={obs.input} />
          )}

          {obs.output != null && (
            <JsonSection title="Output" data={obs.output} />
          )}
        </div>
      )}
    </div>
  )
}

function JsonSection({ title, data }: { title: string; data: unknown }) {
  const [expanded, setExpanded] = useState(false)
  const formatted = useCallback(() => {
    try {
      return JSON.stringify(data, null, 2)
    } catch {
      return String(data)
    }
  }, [data])

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-gray-600 text-xs hover:text-gray-800"
      >
        <svg
          className={`h-3 w-3 transition-transform ${expanded ? 'rotate-90' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="font-medium">{title}</span>
      </button>
      {expanded && (
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-gray-800 p-2 font-mono text-xs text-green-400">
          {formatted().length > 10000 ? formatted().slice(0, 10000) + '\n... (truncated)' : formatted()}
        </pre>
      )}
    </div>
  )
}

function AnalysisResultPanel({ result }: { result: AnalysisResult }) {
  return (
    <div className="space-y-4">
      {result.summary && (
        <div className="rounded-md bg-blue-50 px-4 py-3 border border-blue-100">
          <div className="font-medium text-blue-800 text-xs mb-1">摘要</div>
          <div className="text-blue-900 text-sm">{result.summary}</div>
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
        <div className="rounded-md bg-white px-4 py-3 border border-gray-200">
          <div className="font-medium text-gray-700 text-xs mb-2">详细分析</div>
          <div className="text-gray-800 text-sm whitespace-pre-wrap leading-relaxed">{result.analysis}</div>
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