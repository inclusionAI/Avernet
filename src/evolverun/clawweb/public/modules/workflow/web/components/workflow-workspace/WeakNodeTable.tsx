import { useMemo, useState } from 'react'
import { useWorkflowNodeStats } from '../../api/hooks'
import type { NodeStat } from '@avernet/clawweb-shared/web/types'

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

function percent(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

type SortKey = 'failureRate' | 'executions' | 'duration'

function sortNodes(nodes: NodeStat[], key: SortKey): NodeStat[] {
  return [...nodes].sort((a, b) => {
    if (key === 'failureRate') return b.failureRate - a.failureRate || b.totalExecutions - a.totalExecutions
    if (key === 'executions') return b.totalExecutions - a.totalExecutions || b.failureRate - a.failureRate
    return b.p95DurationMs - a.p95DurationMs || b.failureRate - a.failureRate
  })
}

export function WeakNodeTable({
  workflowId,
  onNodeClick,
}: {
  workflowId: string
  onNodeClick?: (nodeId: string) => void
}) {
  const { data, isLoading } = useWorkflowNodeStats(workflowId, 7)
  const [sort, setSort] = useState<SortKey>('failureRate')
  const nodes = useMemo(() => sortNodes(data?.nodes ?? [], sort), [data?.nodes, sort])

  if (isLoading) {
    return <div className="p-4 text-xs text-gray-500">加载节点分析…</div>
  }

  if (!data || nodes.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4 text-xs text-gray-500">
        近 7 天暂无节点运行数据。
      </div>
    )
  }

  const headerCell = (label: string, key: SortKey) => (
    <button
      onClick={() => setSort(key)}
      className={`flex items-center gap-1 px-4 py-2 text-left text-xs font-medium uppercase tracking-wider ${sort === key ? 'text-blue-600' : 'text-gray-500'}`}
    >
      {label}
      {sort === key && <span className="text-[10px]">↓</span>}
    </button>
  )

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">节点分析</h3>
          <p className="text-xs text-gray-500">按失败率 / 执行次数 / 耗时定位最该修节点 · 近 7 天</p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">节点</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">执行器</th>
              {headerCell('失败率', 'failureRate')}
              {headerCell('执行次数', 'executions')}
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">失败次数</th>
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">平均重试</th>
              {headerCell('P95 耗时', 'duration')}
              <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">最近错误</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {nodes.map((node) => (
              <tr key={node.nodeId} className="hover:bg-gray-50">
                <td className="px-4 py-2">
                  <button
                    onClick={() => onNodeClick?.(node.nodeId)}
                    className="text-left text-xs font-medium text-blue-600 hover:text-blue-700 hover:underline"
                    title={node.nodeId}
                  >
                    {node.nodeTitle || node.nodeId}
                  </button>
                </td>
                <td className="px-4 py-2 text-xs text-gray-600">{node.executorType ?? '-'}</td>
                <td className="px-4 py-2">
                  <span
                    className={`text-xs font-medium ${
                      node.failureRate >= 0.5 ? 'text-red-600' : node.failureRate >= 0.2 ? 'text-orange-500' : 'text-gray-600'
                    }`}
                  >
                    {percent(node.failureRate)}
                  </span>
                </td>
                <td className="px-4 py-2 text-xs text-gray-600">{node.totalExecutions}</td>
                <td className="px-4 py-2 text-xs text-gray-600">{node.failedCount}</td>
                <td className="px-4 py-2 text-xs text-gray-600">{Number(node.avgRetryCount).toFixed(2)}</td>
                <td className="px-4 py-2 text-xs text-gray-600">{formatDuration(node.p95DurationMs)}</td>
                <td
                  className="max-w-[200px] truncate px-4 py-2 text-xs text-gray-500"
                  title={node.lastErrorText ?? undefined}
                >
                  {node.lastErrorText ?? '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
