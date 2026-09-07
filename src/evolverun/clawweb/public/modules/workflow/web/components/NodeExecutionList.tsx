import { useState, useCallback } from 'react'
import type { NodeExecution, NodeStatus, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { api } from '@avernet/clawweb-shared/web/api/client'
import { formatTimeShort, formatDuration } from '@avernet/workflow/web/utils/time'
import { isWarningsErrorText, parseWarningsErrorText, getWarningCodeLabel } from '../utils/warnings'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import NodeOutputViewer from './NodeOutputViewer'
import NodeInputPanel from './NodeInputPanel'
import RenderedNodeHero from './RenderedNodeHero'
import NodeStepTracePanel from './NodeStepTracePanel'
import AnalysisModal from './AnalysisModal'

const EXECUTOR_LABELS: Record<string, string> = {
  'embedded-agent': '代理',
  action: '动作',
  human: '人工',
  'loop-group': '循环',
  done: '完成',
  'baas-call': 'BaaS调用',
}

/** Parse runId/messageId and mode from progress_message */
function parseBaasProgress(progressMessage: string): { mode: string; runId: string } | null {
  const msgMatch = progressMessage.match(/\(messageId=([^)]+)\)/)
  if (msgMatch) return { mode: 'message', runId: msgMatch[1] }
  const runMatch = progressMessage.match(/\(runId=([^)]+)\)/)
  if (runMatch) return { mode: 'run', runId: runMatch[1] }
  return null
}

/** Get BaaS executor config from workflow spec for a given node */
function getBaasConfig(spec: WorkflowSpec | undefined, nodeId: string): {
  baseUrl: string
  mode: string
  iamToken?: string
} | null {
  if (!spec) return null
  const node = spec.nodes.find((n) => n.id === nodeId)
  if (!node || node.executor.type !== 'baas-call') return null
  const exec = node.executor as Record<string, unknown>
  return {
    baseUrl: (exec.baseUrl as string) || 'https://secbaas-prod.alipay.com',
    mode: (exec.mode as string) || 'message',
    iamToken: exec.iamToken as string | undefined,
  }
}

interface BaasPollResult {
  loading: boolean
  data?: {
    ok: boolean
    status: number
    data: {
      run_id?: string
      message_id?: string
      session_id?: string
      status?: string
      result?: { content?: string }
    } | null
    errorCode: string | number | null
    errorMessage: string | null
  }
  error?: string
}

interface NodeExecutionListProps {
  nodes: NodeExecution[]
  onSelectNode: (nodeId: string) => void
  selectedNodeId?: string
  workflowSpec?: WorkflowSpec
  onAnalyze: (node: NodeExecution) => void
}

export default function NodeExecutionList({ nodes, onSelectNode, selectedNodeId, workflowSpec, onAnalyze }: NodeExecutionListProps) {
  if (nodes.length === 0) {
    return <p className="py-8 text-center text-gray-400 text-sm">暂无节点执行记录</p>
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              节点
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              状态
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              执行器
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              触发者
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              阶段
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              耗时
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              Token
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              开始时间
            </th>
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
              完成时间
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {nodes.map((node) => (
            <NodeRow
              key={`${node.flow_id}-${node.node_id}`}
              node={node}
              isSelected={node.node_id === selectedNodeId}
              onSelect={() => onSelectNode(node.node_id)}
              workflowSpec={workflowSpec}
              onAnalyze={onAnalyze}
              nodes={nodes}
              flowId={node.flow_id}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function NodeRow({
  node,
  isSelected,
  onSelect,
  workflowSpec,
  onAnalyze,
  nodes,
  flowId,
}: {
  node: NodeExecution
  isSelected: boolean
  onSelect: () => void
  workflowSpec?: WorkflowSpec
  onAnalyze: (node: NodeExecution) => void
  nodes: NodeExecution[]
  flowId: string
}) {
  const tokenUsage = parseTokenUsage(node.token_usage_json)
  const hasError = node.status === 'failed' && node.error_text
  const hasWarnings = node.status === 'succeeded' && isWarningsErrorText(node.error_text)
  const warnings = hasWarnings ? parseWarningsErrorText(node.error_text) : []
  const isBaasRunning = node.executor_type === 'baas-call' && node.status === 'running'
  const [baasResult, setBaasResult] = useState<BaasPollResult | null>(null)

  const handlePollBaas = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!node.progress_message) return

    const parsed = parseBaasProgress(node.progress_message)
    if (!parsed) return

    const config = getBaasConfig(workflowSpec, node.node_id)
    if (!config) return

    setBaasResult({ loading: true })
    try {
      const result = await api.baas.pollStatus({
        baseUrl: config.baseUrl,
        mode: config.mode,
        runId: parsed.runId,
        iamToken: config.iamToken,
      })
      setBaasResult({ loading: false, data: result })
    } catch (err) {
      setBaasResult({ loading: false, error: err instanceof Error ? err.message : String(err) })
    }
  }, [node.progress_message, node.node_id, workflowSpec])

  return (
    <>
      <tr
        onClick={onSelect}
        className={`cursor-pointer transition-colors hover:bg-blue-50 ${
          isSelected ? 'bg-blue-50 ring-1 ring-inset ring-blue-200' : ''
        } ${hasError ? 'bg-red-50/50' : hasWarnings ? 'bg-amber-50/50' : ''}`}
      >
        <td className="px-4 py-3">
          <div className="font-medium text-gray-900 text-sm">{node.node_title || node.node_id}</div>
          <div className="font-mono text-gray-400 text-xs">{node.node_id}</div>
          {node.progress_message && (
            <div className="mt-0.5 text-gray-400 text-xs">{node.progress_message}</div>
          )}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <StatusBadge status={node.status as NodeStatus} />
            {hasWarnings && (
              <span
                className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                title={warnings.map(w => `[${w.code}] ${w.message}`).join('\n')}
              >
                <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
                </svg>
                {warnings.length}条警告
              </span>
            )}
            {isBaasRunning && (
              <button
                onClick={handlePollBaas}
                disabled={baasResult?.loading}
                className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-blue-700 text-xs transition-colors hover:bg-blue-100 disabled:opacity-50"
                title="查询BaaS调用状态"
              >
                {baasResult?.loading ? (
                  <>
                    <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    查询中
                  </>
                ) : '刷新状态'}
              </button>
            )}
          </div>
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-600 text-sm">
          {EXECUTOR_LABELS[node.executor_type] ?? node.executor_type}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {node.triggered_by ? (
            <span className="font-mono text-xs">{node.triggered_by}</span>
          ) : (
            <span className="text-gray-300">&mdash;</span>
          )}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {node.phase ?? '—'}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {formatDuration(node.duration_ms)}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {tokenUsage ? `${tokenUsage.total.toLocaleString()}` : '—'}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {formatTimeShort(node.started_at)}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {formatTimeShort(node.completed_at)}
        </td>
      </tr>
      {baasResult && !baasResult.loading && (
        <tr className="bg-gray-50">
          <td colSpan={9} className="px-4 py-2">
            <BaasPollResultDisplay result={baasResult} />
          </td>
        </tr>
      )}
      {isSelected && (
        <tr className="bg-gray-50/80">
          <td colSpan={9} className="px-4 py-3">
            <NodeDetailPanel
              node={node}
              nodes={nodes}
              workflowSpec={workflowSpec}
              flowId={flowId}
              onAnalyze={onAnalyze}
            />
          </td>
        </tr>
      )}
    </>
  )
}

function BaasPollResultDisplay({ result }: { result: BaasPollResult }) {
  if (result.error) {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-red-700 text-xs">
        <span className="font-medium">查询失败：</span>{result.error}
      </div>
    )
  }

  if (!result.data) return null

  const { data } = result

  if (!data.ok) {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-red-700 text-xs">
        <span className="font-medium">BaaS返回错误：</span>
        {data.errorMessage ?? `HTTP ${data.status}`}
        {data.errorCode && <span className="ml-2 text-red-500">({data.errorCode})</span>}
      </div>
    )
  }

  const baasStatus = data.data?.status ?? 'UNKNOWN'
  const content = data.data?.result?.content

  return (
    <div className="space-y-1 rounded-md bg-blue-50 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-medium text-gray-700">BaaS状态：</span>
        <BaasStatusBadge status={baasStatus} />
      </div>
      {data.data?.session_id && (
        <div className="text-gray-500">
          <span className="font-medium">Session：</span>
          <span className="font-mono">{data.data.session_id}</span>
        </div>
      )}
      {content && (
        <div className="mt-1">
          <span className="font-medium text-gray-700">返回内容：</span>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-gray-600">
            {content.length > 2000 ? content.slice(0, 2000) + '…' : content}
          </pre>
        </div>
      )}
    </div>
  )
}

function BaasStatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase()
  let colorClass: string
  switch (upper) {
    case 'COMPLETED':
      colorClass = 'bg-green-100 text-green-800'
      break
    case 'FAILED':
      colorClass = 'bg-red-100 text-red-800'
      break
    case 'RUNNING':
      colorClass = 'bg-yellow-100 text-yellow-800'
      break
    case 'PENDING':
      colorClass = 'bg-gray-100 text-gray-700'
      break
    default:
      colorClass = 'bg-gray-100 text-gray-600'
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${colorClass}`}>
      {upper}
    </span>
  )
}

interface TokenUsage {
  input: number
  output: number
  total: number
}

function parseTokenUsage(json: string | null): TokenUsage | null {
  if (!json) return null
  try {
    const parsed = JSON.parse(json)
    const input = typeof parsed.input === 'number' ? parsed.input : 0
    const output = typeof parsed.output === 'number' ? parsed.output : 0
    return { input, output, total: input + output }
  } catch {
    return null
  }
}

/** Full detail panel shown inline below a selected node row */
function NodeDetailPanel({
  node,
  nodes,
  workflowSpec,
  flowId,
  onAnalyze,
}: {
  node: NodeExecution
  nodes: NodeExecution[]
  workflowSpec?: WorkflowSpec
  flowId: string
  onAnalyze: (node: NodeExecution) => void
}) {
  const [analyzing, setAnalyzing] = useState(false)
  const isWarning = node.error_text ? isWarningsErrorText(node.error_text) : false
  const warnings = isWarning ? parseWarningsErrorText(node.error_text) : []

  let systemContext: Record<string, unknown> | null = null
  if (node.system_context_json) {
    try {
      systemContext = JSON.parse(node.system_context_json)
    } catch {
      systemContext = null
    }
  }

  return (
    <div className="space-y-3 text-left">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">节点详情</span>
        <div className="flex items-center gap-2">
          {(node.embedded_session_key || node.session_id) && (
            <button
              onClick={() => {
                setAnalyzing(true)
                onAnalyze(node)
              }}
              className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-blue-700 text-xs hover:bg-blue-100"
            >
              节点分析
            </button>
          )}
          {analyzing && <AnalysisModal node={node} onClose={() => setAnalyzing(false)} />}
        </div>
      </div>

      <RenderedNodeHero node={node} workflowSpec={workflowSpec} nodes={nodes} />
      <NodeInputPanel nodeId={node.node_id} inputJson={node.input_json} nodes={nodes} />
      <NodeOutputViewer
        nodeId={node.node_id}
        label="输出"
        data={node.output_json}
        isTruncated={(node.output_json?.length ?? 0) > 10240}
        onLoadFull={async () => ''}
      />

      {isWarning && warnings.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-amber-700 uppercase tracking-wide flex items-center gap-1">
            <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
            </svg>
            执行警告
          </h4>
          <div className="space-y-1.5 rounded-md border border-amber-200 bg-amber-50 p-2.5">
            {warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className="shrink-0 rounded bg-amber-200 px-1.5 py-0.5 font-mono text-amber-900 text-[10px] leading-none">
                  {getWarningCodeLabel(w.code)}
                </span>
                <span className="text-amber-800">{w.message}</span>
              </div>
            ))}
            <p className="mt-1 text-amber-600 text-[10px]">
              节点执行成功，但存在上述警告。可能影响输出质量。
            </p>
          </div>
        </div>
      )}

      {node.error_text && !isWarning && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-red-700 uppercase tracking-wide">错误信息</h4>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-red-50 p-2.5 font-mono text-xs text-red-800 border border-red-100">
            {node.error_text}
          </pre>
        </div>
      )}

      {systemContext && (
        <details className="overflow-hidden rounded-md border border-gray-200 bg-white">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide hover:bg-gray-50">
            系统上下文
          </summary>
          <div className="space-y-3 border-t border-gray-100 p-3">
            <SystemContextItems data={systemContext} />
          </div>
        </details>
      )}
      {node.system_context_json && !systemContext && (
        <div>
          <h4 className="mb-1 text-xs font-semibold text-gray-600 uppercase tracking-wide">系统上下文 (原始)</h4>
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-gray-50 p-2.5 font-mono text-xs text-gray-700 border border-gray-200">
            {node.system_context_json}
          </pre>
        </div>
      )}

      <DebugInfoBlock node={node} />

      {node.token_usage_json && <TokenUsageDisplay json={node.token_usage_json} />}

      {node.executor_type === 'embedded-agent' && (
        <div className="border-t border-gray-100 pt-3">
          <NodeStepTracePanel flowId={flowId} nodeId={node.node_id} attempt={node.attempt} />
        </div>
      )}
    </div>
  )
}

/** Render system context as structured key-value pairs with nested support */
function SystemContextItems({ data, depth = 0 }: { data: Record<string, unknown>; depth?: number }) {
  const entries = Object.entries(data).filter(([, v]) => v !== undefined)

  // Semantic label mapping for common system context keys
  const LABELS: Record<string, string> = {
    triggerRule: '触发规则',
    phase: '阶段',
    retry: '重试配置',
    knowledgeBaseId: '知识库ID',
    outputContractValidated: '输出契约校验',
    outputContractIssues: '输出契约问题数',
    failureReason: '失败原因',
    willRetry: '将重试',
    retryAttempt: '重试次数',
    maxRetries: '最大重试',
    retryOn: '重试触发条件',
    backoffMs: '退避间隔(ms)',
    reason: '原因',
    originalStatus: '原始状态',
    runHooks: '执行钩子',
    maxAttempts: '最大尝试',
    on: '触发条件',
  }

  const FAILURE_REASON_LABELS: Record<string, string> = {
    'executor-failed': '执行器失败',
    'output-contract-failed': '输出契约校验失败',
    'trigger_rule_not_satisfied': '触发规则未满足',
    'manual_skip': '人工跳过',
    'bcs_approval_callback': 'BCS审批回调',
  }

  return (
    <div className={depth > 0 ? 'ml-3 border-l-2 border-gray-200 pl-2' : 'space-y-1.5'}>
      {entries.map(([key, value]) => {
        const label = LABELS[key] ?? key

        if (value === null || value === undefined) return null

        if (typeof value === 'object' && !Array.isArray(value) && value !== null) {
          return (
            <div key={key}>
              <span className="text-xs font-medium text-gray-600">{label}:</span>
              <SystemContextItems data={value as Record<string, unknown>} depth={depth + 1} />
            </div>
          )
        }

        let displayValue: React.ReactNode = String(value)
        if (key === 'failureReason' && typeof value === 'string') {
          displayValue = FAILURE_REASON_LABELS[value] ?? value
        }
        if (typeof value === 'boolean') {
          displayValue = value ? '✓ 是' : '✗ 否'
        }

        return (
          <div key={key} className="flex items-baseline gap-2 text-xs">
            <span className="shrink-0 font-medium text-gray-500">{label}:</span>
            <span className={`font-mono ${
              key === 'failureReason' ? 'text-red-600' :
              key === 'willRetry' ? (value ? 'text-amber-600' : 'text-red-600') :
              'text-gray-800'
            }`}>
              {displayValue}
            </span>
          </div>
        )
      })}
    </div>
  )
}
function TokenUsageDisplay({ json }: { json: string }) {
  let parsed: unknown
  try {
    parsed = JSON.parse(json)
  } catch {
    return null
  }
  if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const tokenUsage = parsed as Record<string, unknown>
  const input = typeof tokenUsage.input === 'number' ? tokenUsage.input : 0
  const output = typeof tokenUsage.output === 'number' ? tokenUsage.output : 0
  return (
    <div className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">
      <span className="font-medium">Tokens：</span>{' '}
      <span className="text-blue-600">{input.toLocaleString()} 输入</span> /{' '}
      <span className="text-green-600">{output.toLocaleString()} 输出</span> /{' '}
      <span className="font-medium">{(input + output).toLocaleString()} 总计</span>
    </div>
  )
}

function DebugInfoBlock({ node }: { node: NodeExecution }) {
  const rows: { label: string; value: string }[] = []
  const push = (label: string, value: unknown): void => {
    if (value == null) return
    const str = String(value)
    if (str.trim() === '') return
    rows.push({ label, value: str })
  }
  push('触发来源', node.triggered_by)
  push('分支', node.branch_id)
  push('进度', node.progress_message)
  push('会话 key', node.session_key)
  push('会话 id', node.session_id)
  push('内嵌会话 key', node.embedded_session_key)

  if (rows.length === 0) return null
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">运行元数据</div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-md border border-gray-200 bg-gray-50 p-2.5 text-xs">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-gray-400">{r.label}</dt>
            <dd className="break-all font-mono text-gray-700">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
