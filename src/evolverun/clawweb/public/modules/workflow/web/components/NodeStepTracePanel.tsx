import { useState } from 'react'
import { useNodeStepTraces, useHallucinationChecks } from '@avernet/workflow/web/api/hooks'
import type { NodeStepTraceStep, HallucinationCheckItem } from '@avernet/clawweb-shared/web/types'

interface NodeStepTracePanelProps {
  flowId: string
  nodeId: string
  attempt?: number
}

/**
 * Displays embedded-agent node execution steps as a timeline.
 * Shows tool_call inputs, tool_result outputs, and assistant_text content.
 */
export default function NodeStepTracePanel({ flowId, nodeId, attempt = 1 }: NodeStepTracePanelProps) {
  const { data, isLoading, isError } = useNodeStepTraces(flowId, nodeId, attempt)
  const { data: hallucinationData } = useHallucinationChecks(flowId, nodeId, attempt)

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-gray-400 text-xs">
        <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        加载步骤追踪...
      </div>
    )
  }

  if (isError || !data || data.steps.length === 0) {
    return (
      <div className="py-4 text-center text-gray-400 text-xs">
        暂无步骤追踪数据
      </div>
    )
  }

  return (
    <div>
      {/* Header with summary stats */}
      <div className="mb-3 flex items-center gap-3 text-xs text-gray-500">
        <span className="font-medium text-gray-700">步骤追踪</span>
        {data.skillName && (
          <span className="rounded-full bg-purple-100 px-2 py-0.5 font-mono text-purple-700 text-[10px]">
            {data.skillName}
          </span>
        )}
        <span>{data.totalSteps} 步</span>
        <span className="text-blue-600">{data.toolCallCount} 次工具调用</span>
        {data.toolErrorCount > 0 && (
          <span className="text-red-600">{data.toolErrorCount} 次错误</span>
        )}
        {/* Hallucination risk badge */}
        {hallucinationData && hallucinationData.riskLevel !== 'none' && (
          <RiskBadge level={hallucinationData.riskLevel} score={hallucinationData.riskScore} />
        )}
      </div>

      {/* Step timeline */}
      <div className="space-y-0">
        {data.steps.map((step) => (
          <StepItem key={`${step.stepType}-${step.stepSeq}`} step={step} />
        ))}
      </div>

      {/* Hallucination Check Results */}
      {hallucinationData && hallucinationData.totalChecks > 0 && (
        <HallucinationCheckSection data={hallucinationData} />
      )}
    </div>
  )
}

// ── Step Item ──────────────────────────────────────────────────────

function StepItem({ step }: { step: NodeStepTraceStep }) {
  if (step.stepType === 'progress') return <ProgressStep step={step} />
  if (step.stepType === 'tool_call') return <ToolCallStep step={step} />
  if (step.stepType === 'tool_result') return <ToolResultStep step={step} />
  return <AssistantTextStep step={step} />
}

// ── Progress Step ────────────────────────────────────────────────────

/** Map progress sub-type to a human-readable label and icon style. */
function getProgressStyle(toolName: string | null): { label: string; dotClass: string; textClass: string } {
  switch (toolName) {
    case 'skill_invoked':
      return { label: '技能调用', dotClass: 'bg-purple-100', textClass: 'text-purple-700' }
    case 'tool_completed':
      return { label: '工具完成', dotClass: 'bg-green-100', textClass: 'text-green-700' }
    case 'assistant_started':
      return { label: 'AI 开始', dotClass: 'bg-blue-100', textClass: 'text-blue-700' }
    case 'assistant_text':
      return { label: 'AI 输出', dotClass: 'bg-gray-100', textClass: 'text-gray-600' }
    default:
      return { label: '进度', dotClass: 'bg-gray-100', textClass: 'text-gray-600' }
  }
}

function ProgressStep({ step }: { step: NodeStepTraceStep }) {
  const { label, dotClass, textClass } = getProgressStyle(step.toolName)
  const message = step.textContent ?? ''

  return (
    <div className="group relative flex gap-3 pb-2">
      {/* Timeline dot — smaller than tool/assistant dots to de-emphasize */}
      <div className="flex flex-col items-center">
        <div className={`mt-2 h-3 w-3 shrink-0 rounded-full ${dotClass}`} />
        <div className="w-px flex-1 bg-gray-100" />
      </div>

      {/* Content — compact inline style */}
      <div className="min-w-0 flex-1 pb-0.5">
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-medium ${textClass}`}>
            {label}
          </span>
          <span className="font-mono text-gray-200 text-[9px]">#{step.stepSeq}</span>
        </div>
        {message && (
          <p className="mt-0.5 text-gray-500 text-xs leading-snug">
            {message}
          </p>
        )}
      </div>
    </div>
  )
}

function ToolCallStep({ step }: { step: NodeStepTraceStep }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="group relative flex gap-3 pb-3">
      {/* Timeline dot + line */}
      <div className="flex flex-col items-center">
        <div className="mt-1.5 h-5 w-5 shrink-0 rounded-full bg-blue-100 flex items-center justify-center">
          <svg className="h-3 w-3 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        </div>
        <div className="w-px flex-1 bg-gray-200" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-blue-700">
            ▸ {step.toolName || 'tool'}
          </span>
          <span className="font-mono text-gray-300 text-[10px]">#{step.stepSeq}</span>
        </div>

        {step.toolInputJson && (
          <div className="mt-1">
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-[10px] text-gray-400 hover:text-gray-600 transition-colors"
            >
              {expanded ? '收起输入' : '查看输入'}
            </button>
            {expanded && (
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-blue-50 p-2 font-mono text-[11px] text-gray-700 border border-blue-100">
                {formatJson(step.toolInputJson)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ToolResultStep({ step }: { step: NodeStepTraceStep }) {
  const [expanded, setExpanded] = useState(false)

  const bgClass = step.isError ? 'bg-red-100' : 'bg-green-100'
  const iconClass = step.isError ? 'text-red-600' : 'text-green-600'

  return (
    <div className="group relative flex gap-3 pb-3">
      <div className="flex flex-col items-center">
        <div className={`mt-1.5 h-5 w-5 shrink-0 rounded-full ${bgClass} flex items-center justify-center`}>
          {step.isError ? (
            <svg className={`h-3 w-3 ${iconClass}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className={`h-3 w-3 ${iconClass}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          )}
        </div>
        <div className="w-px flex-1 bg-gray-200" />
      </div>

      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-medium ${step.isError ? 'text-red-700' : 'text-green-700'}`}>
            {step.isError ? '✕ 错误' : '✓ 结果'}
          </span>
          <span className="font-mono text-gray-300 text-[10px]">#{step.stepSeq}</span>
          {step.toolName && (
            <span className="text-gray-400 text-[10px]">{step.toolName}</span>
          )}
        </div>

        {step.toolOutputText && (
          <div className="mt-1">
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-[10px] text-gray-400 hover:text-gray-600 transition-colors"
            >
              {expanded ? '收起输出' : '查看输出'}
            </button>
            {expanded && (
              <pre className={`mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md p-2 font-mono text-[11px] border ${
                step.isError
                  ? 'bg-red-50 text-red-800 border-red-100'
                  : 'bg-green-50 text-gray-700 border-green-100'
              }`}>
                {step.toolOutputText}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function AssistantTextStep({ step }: { step: NodeStepTraceStep }) {
  const [expanded, setExpanded] = useState(false)
  const text = step.textContent ?? ''
  const isLong = text.length > 300

  return (
    <div className="group relative flex gap-3 pb-3">
      <div className="flex flex-col items-center">
        <div className="mt-1.5 h-5 w-5 shrink-0 rounded-full bg-gray-100 flex items-center justify-center">
          <svg className="h-3 w-3 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
          </svg>
        </div>
      </div>

      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-600">AI 回复</span>
          <span className="font-mono text-gray-300 text-[10px]">#{step.stepSeq}</span>
        </div>
        <div className="mt-1 rounded-md bg-gray-50 p-2 text-xs text-gray-700 border border-gray-100">
          {isLong && !expanded ? (
            <>
              <span className="whitespace-pre-wrap">{text.slice(0, 300)}…</span>
              <button
                onClick={() => setExpanded(true)}
                className="ml-1 text-blue-500 hover:text-blue-700 text-[10px]"
              >
                展开全文
              </button>
            </>
          ) : (
            <>
              <span className="whitespace-pre-wrap">{text}</span>
              {isLong && expanded && (
                <button
                  onClick={() => setExpanded(false)}
                  className="ml-1 text-blue-500 hover:text-blue-700 text-[10px]"
                >
                  收起
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Hallucination Check UI ───────────────────────────────────────────

const RISK_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  low: { bg: 'bg-yellow-100', text: 'text-yellow-800', label: '低风险' },
  medium: { bg: 'bg-orange-100', text: 'text-orange-800', label: '中风险' },
  high: { bg: 'bg-red-100', text: 'text-red-800', label: '高风险' },
}

function RiskBadge({ level, score }: { level: string; score: number }) {
  const style = RISK_STYLES[level] ?? RISK_STYLES.low
  return (
    <span className={`rounded-full px-2 py-0.5 font-medium text-[10px] ${style.bg} ${style.text}`}>
      🛡️ {style.label} ({score})
    </span>
  )
}

function HallucinationCheckSection({ data }: { data: { riskLevel: string; riskScore: number; failedChecks: number; totalChecks: number; checks: HallucinationCheckItem[] } }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="mt-4 border-t border-gray-200 pt-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs font-medium text-gray-600 hover:text-gray-800 transition-colors"
      >
        <svg className={`h-3 w-3 transition-transform ${expanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span>幻觉检测</span>
        {data.riskLevel !== 'none' && <RiskBadge level={data.riskLevel} score={data.riskScore} />}
        {data.failedChecks === 0 && (
          <span className="text-green-600 text-[10px]">全部通过</span>
        )}
      </button>

      {expanded && (
        <div className="mt-2 space-y-1.5">
          {data.checks.map((check) => (
            <CheckItem key={check.checkType} check={check} />
          ))}
        </div>
      )}
    </div>
  )
}

const CHECK_TYPE_LABELS: Record<string, string> = {
  error_ignoring: '错误忽略',
  ungrounded_claim: '无依据声明',
  fabricated_output: '数据编造',
  hallucinated_tool: '工具编造',
  contradiction: '内容矛盾',
}

const SEVERITY_DOT: Record<string, string> = {
  low: 'bg-yellow-400',
  medium: 'bg-orange-400',
  high: 'bg-red-400',
}

function CheckItem({ check }: { check: HallucinationCheckItem }) {
  return (
    <div className="flex items-start gap-2 rounded-md bg-gray-50 px-2.5 py-1.5 text-xs border border-gray-100">
      {/* Pass/fail icon */}
      {check.passed ? (
        <svg className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <svg className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`inline-block h-2 w-2 rounded-full ${SEVERITY_DOT[check.severity] ?? 'bg-gray-400'}`} />
          <span className="font-medium text-gray-700">
            {CHECK_TYPE_LABELS[check.checkType] ?? check.checkType}
          </span>
          <span className="text-gray-300 text-[10px]">{check.severity}</span>
        </div>
        {check.description && (
          <p className="mt-0.5 text-gray-600">{check.description}</p>
        )}
        {!check.passed && check.evidence && (
          <p className="mt-0.5 font-mono text-[10px] text-gray-400 break-all">
            {check.evidence}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Helpers ────────────────────────────────────────────────────────

function formatJson(input: string): string {
  try {
    return JSON.stringify(JSON.parse(input), null, 2)
  } catch {
    return input
  }
}