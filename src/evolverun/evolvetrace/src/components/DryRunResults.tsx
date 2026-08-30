import type { DryRunResult as DryRunResultType, NodeStatus } from '../types'
import StatusBadge from './StatusBadge'

const STATUS_ORDER: Record<NodeStatus, number> = {
  pending: 0,
  waiting: 1,
  running: 2,
  postActionsRunning: 3,
  succeeded: 4,
  failed: 5,
  skipped: 6,
  blocked: 7,
}

interface DryRunResultsProps {
  result: DryRunResultType
}

export default function DryRunResults({ result }: DryRunResultsProps) {
  const entries = Object.entries(result.nodeStates).sort(
    (a, b) => (STATUS_ORDER[a[1].status as NodeStatus] ?? 99) - (STATUS_ORDER[b[1].status as NodeStatus] ?? 99),
  )

  const nodeReports = result.nodeReports ?? []

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-700">试运行结果</h3>

      {/* Summary */}
      <div className="flex gap-4">
        <div className="rounded-md bg-green-50 px-3 py-1.5 text-center">
          <div className="text-lg font-bold text-status-succeeded">
            {entries.filter(([, s]) => s.status === 'succeeded').length}
          </div>
          <div className="text-xs text-green-600">已成功</div>
        </div>
        <div className="rounded-md bg-red-50 px-3 py-1.5 text-center">
          <div className="text-lg font-bold text-status-failed">
            {entries.filter(([, s]) => s.status === 'failed').length}
          </div>
          <div className="text-xs text-red-600">已失败</div>
        </div>
        <div className="rounded-md bg-gray-50 px-3 py-1.5 text-center">
          <div className="text-lg font-bold text-gray-500">
            {entries.filter(([, s]) => s.status === 'skipped').length}
          </div>
          <div className="text-xs text-gray-500">已跳过</div>
        </div>
      </div>

      {/* Node results */}
      <div className="space-y-2">
        {entries.map(([nodeId, state]) => {
          const report = nodeReports.find((r) => r.nodeId === nodeId)
          return (
            <div
              key={nodeId}
              className={`rounded-md border px-3 py-2 ${
                state.status === 'failed'
                  ? 'border-red-200 bg-red-50'
                  : state.status === 'succeeded'
                    ? 'border-green-200 bg-green-50'
                    : 'border-gray-200 bg-gray-50'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-medium text-gray-800">{nodeId}</span>
                  <StatusBadge status={state.status as NodeStatus} />
                  {report && (
                    <span className="text-gray-400 text-xs">来自 {report.mockSource}</span>
                  )}
                </div>
                {state.durationMs !== undefined && state.durationMs !== null && (
                  <span className="text-gray-400 text-xs">{state.durationMs}ms</span>
                )}
              </div>

              {/* Output */}
              {state.output !== undefined && state.output !== null && (
                <pre className="mt-1 max-h-32 overflow-auto rounded bg-white p-2 font-mono text-xs text-gray-700">
                  {typeof state.output === 'string'
                    ? state.output
                    : JSON.stringify(state.output, null, 2)}
                </pre>
              )}

              {/* Error */}
              {state.error && (
                <p className="mt-1 text-red-600 text-xs">{state.error}</p>
              )}
            </div>
          )
        })}
      </div>

      {/* Assertion results */}
      {result.assertionResults && result.assertionResults.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium text-gray-500">断言</h4>
          <div className="space-y-1">
            {result.assertionResults.map((assertion, i) => (
              <div
                key={i}
                className={`flex items-center gap-2 rounded px-2 py-1 text-xs ${
                  assertion.passed ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
                }`}
              >
                <span className="font-medium">{assertion.passed ? '通过' : '失败'}</span>
                <span>{assertion.description}</span>
                {!assertion.passed && assertion.expected !== undefined && (
                  <span className="text-gray-400">
                    (期望: {JSON.stringify(assertion.expected)}, 实际: {JSON.stringify(assertion.actual)})
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}