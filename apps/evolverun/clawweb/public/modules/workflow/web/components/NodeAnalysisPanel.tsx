import { useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkflowNodeStats } from '../api/hooks'
import type { NodeStat } from '@avernet/clawweb-shared/web/types'

type SortKey = 'failureRate' | 'p95DurationMs' | 'totalExecutions'

function formatDuration(ms: number): string {
  if (ms === 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

function failureRateStyle(rate: number): { text: string; dot: string } {
  if (rate > 0.10) return { text: 'text-red-600 font-semibold', dot: 'bg-red-500' }
  if (rate > 0.05) return { text: 'text-orange-500 font-semibold', dot: 'bg-orange-400' }
  if (rate > 0) return { text: 'text-green-600 font-medium', dot: 'bg-green-400' }
  return { text: 'text-gray-400', dot: 'bg-gray-300' }
}

const SORT_OPTIONS: [SortKey, string][] = [
  ['failureRate', '失败率'],
  ['p95DurationMs', '耗时'],
  ['totalExecutions', '执行次数'],
]

export function NodeAnalysisPanel({ workflowId, highlightNodeId }: { workflowId: string; highlightNodeId?: string | null }) {
  const { data: stats, isLoading, error } = useWorkflowNodeStats(workflowId)
  const [sortKey, setSortKey] = useState<SortKey>('failureRate')
  const [expandedNode, setExpandedNode] = useState<string | null>(null)
  const [activeHighlight, setActiveHighlight] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (highlightNodeId) {
      setActiveHighlight(highlightNodeId)
      const timer = setTimeout(() => setActiveHighlight(null), 3000)
      return () => clearTimeout(timer)
    }
  }, [highlightNodeId])

  const sortedNodes = useMemo(() => {
    if (!stats?.nodes) return []
    return [...stats.nodes].sort((a, b) => {
      if (sortKey === 'failureRate') return b.failureRate - a.failureRate
      if (sortKey === 'p95DurationMs') return b.p95DurationMs - a.p95DurationMs
      return b.totalExecutions - a.totalExecutions
    })
  }, [stats, sortKey])

  const maxP95 = useMemo(() => stats?.nodes ? Math.max(...stats.nodes.map((n) => n.p95DurationMs), 1) : 1, [stats])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-200 border-t-blue-500" />
      </div>
    )
  }

  if (error) return <div className="p-4 text-sm text-red-500">加载失败: {error.message}</div>
  if (!stats || stats.nodes.length === 0) return <div className="py-16 text-center text-gray-400 text-sm">暂无节点统计数据</div>

  return (
    <div>
      {/* 工具栏 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-gray-400 mr-1">排序</span>
          {SORT_OPTIONS.map(([key, label]) => (
            <button
              key={key}
              onClick={() => setSortKey(key)}
              className={`px-2.5 py-1 text-xs rounded-md transition-all ${sortKey === key ? 'bg-blue-600 text-white shadow-sm' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-xs text-gray-400">{stats.totalRuns} 次运行 · {stats.nodes.length} 节点</span>
      </div>

      {/* 表格 */}
      <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {[
                ['节点', 'text-left'],
                ['执行器', 'text-left'],
                ['执行', 'text-right'],
                ['失败率', 'text-right'],
                ['重试', 'text-right'],
                ['P50', 'text-right'],
                ['P95 耗时', 'text-left'],
                ['Token', 'text-right'],
                ['错误分类', 'text-left'],
                ['', 'text-left'],
              ].map(([label, align], i) => (
                <th key={i} className={`px-3 py-2.5 text-xs font-medium uppercase tracking-wider text-gray-400 ${align}`}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 bg-white">
            {sortedNodes.map((node) => (
              <NodeRow
                key={node.nodeId}
                node={node}
                workflowId={workflowId}
                maxP95={maxP95}
                isExpanded={expandedNode === node.nodeId}
                isHighlighted={activeHighlight === node.nodeId}
                onToggle={() => setExpandedNode(expandedNode === node.nodeId ? null : node.nodeId)}
                onViewArchive={async (wfId, nodeId) => {
                  try {
                    const res = await fetch(`/api/run-archives/by-node/${encodeURIComponent(wfId)}/${encodeURIComponent(nodeId)}/recent-failure`)
                    if (res.ok) {
                      const json = await res.json()
                      if (json.flowId) { navigate(`/runs/${json.flowId}`); return }
                    }
                  } catch { /* ignore */ }
                  alert('未找到该节点的失败运行记录')
                }}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function NodeRow({ node, workflowId, maxP95, isExpanded, isHighlighted, onToggle, onViewArchive }: {
  node: NodeStat
  workflowId: string
  maxP95: number
  isExpanded: boolean
  isHighlighted: boolean
  onToggle: () => void
  onViewArchive: (workflowId: string, nodeId: string) => void
}) {
  const barWidth = maxP95 > 0 ? Math.round((node.p95DurationMs / maxP95) * 100) : 0
  const fr = failureRateStyle(node.failureRate)
  const errorSummary = node.errorCategories.map((e) => `${e.category}(${e.count})`).join(', ')

  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors ${isHighlighted ? 'bg-yellow-50' : 'hover:bg-gray-50'}`}
      >
        <td className="px-3 py-2 text-xs font-medium text-gray-900">{node.nodeId}</td>
        <td className="px-3 py-2 text-xs text-gray-400">{node.executorType ?? '—'}</td>
        <td className="px-3 py-2 text-xs text-right text-gray-500 tabular-nums">{node.totalExecutions}</td>
        <td className="px-3 py-2 text-xs text-right">
          {node.totalExecutions > 0 ? (
            <span className={`inline-flex items-center gap-1 ${fr.text}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${fr.dot}`} />
              {(node.failureRate * 100).toFixed(1)}%
              <span className="text-gray-300 font-normal">({node.failedCount})</span>
            </span>
          ) : <span className="text-gray-300">—</span>}
        </td>
        <td className="px-3 py-2 text-xs text-right text-gray-500 tabular-nums">{node.avgRetryCount.toFixed(1)}</td>
        <td className="px-3 py-2 text-xs text-right text-gray-400 tabular-nums">{formatDuration(node.p50DurationMs)}</td>
        <td className="px-3 py-2 text-xs">
          <div className="flex items-center gap-2">
            <div className="flex-1 max-w-[60px] h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-gradient-to-r from-blue-400 to-blue-500 rounded-full transition-all" style={{ width: `${barWidth}%` }} />
            </div>
            <span className="text-gray-500 tabular-nums whitespace-nowrap">{formatDuration(node.p95DurationMs)}</span>
          </div>
        </td>
        <td className="px-3 py-2 text-xs text-right text-gray-400 tabular-nums">{node.avgTokens != null ? node.avgTokens.toLocaleString() : '—'}</td>
        <td className="px-3 py-2 text-xs text-gray-400 max-w-[180px] truncate" title={errorSummary}>{errorSummary || '—'}</td>
        <td className="px-3 py-2 text-xs">
          {node.failureRate > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); onViewArchive(workflowId, node.nodeId) }}
              className="text-blue-500 hover:text-blue-600 hover:underline"
            >
              查看档案
            </button>
          )}
        </td>
      </tr>
      {isExpanded && (
        <tr className="bg-gray-50/50">
          <td colSpan={10} className="px-6 py-4">
            <div className="space-y-3">
              {/* 建议条 */}
              <div className="space-y-1.5">
                {node.failureRate > 0.10 && (
                  <div className="flex items-center gap-2 text-xs bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
                    <span className="text-red-700 font-medium">失败率 {(node.failureRate * 100).toFixed(1)}%，建议优化 prompt 或加 fallback</span>
                    <div className="ml-auto flex items-center gap-2">
                      <a href={`/editor?workflowId=${workflowId}`} target="_blank" className="text-blue-500 hover:underline">编辑 YAML</a>
                      <span className="text-gray-300">|</span>
                      <button onClick={(e) => { e.stopPropagation(); onViewArchive(workflowId, node.nodeId) }} className="text-purple-500 hover:underline">AI 诊断</button>
                    </div>
                  </div>
                )}
                {node.p95DurationMs > 30000 && (
                  <div className="flex items-center gap-2 text-xs bg-orange-50 border border-orange-200 rounded-lg px-3 py-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-orange-400" />
                    <span className="text-orange-700 font-medium">P95 耗时 {formatDuration(node.p95DurationMs)}，建议拆分或调整超时</span>
                    <a href={`/editor?workflowId=${workflowId}`} target="_blank" className="ml-auto text-blue-500 hover:underline">编辑 YAML</a>
                  </div>
                )}
                {node.avgRetryCount > 1.5 && (
                  <div className="flex items-center gap-2 text-xs bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-yellow-400" />
                    <span className="text-yellow-700 font-medium">平均重试 {node.avgRetryCount.toFixed(1)} 次，建议调整 retry 策略</span>
                  </div>
                )}
              </div>

              {/* 错误分类详情 */}
              <div>
                <div className="text-xs font-semibold text-gray-500 mb-2">错误分类详情</div>
                {node.errorCategories.length === 0 ? (
                  <div className="text-xs text-gray-400">无错误记录</div>
                ) : (
                  <div className="space-y-1.5">
                    {node.errorCategories.map((cat, i) => (
                      <div key={i} className="flex items-start gap-3 text-xs bg-white rounded px-3 py-1.5">
                        <span className="inline-flex items-center gap-1 font-medium text-gray-700 w-28">
                          <span className="h-1.5 w-1.5 rounded-full bg-red-300" />
                          {cat.category}
                        </span>
                        <span className="text-gray-400 w-10 tabular-nums">{cat.count}次</span>
                        <span className="text-gray-400 flex-1 break-all font-mono text-[11px]">{cat.sampleError?.slice(0, 150) ?? '—'}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {/* 最近错误 */}
              {node.lastErrorText && (
                <div className="pt-2 border-t border-gray-200">
                  <div className="text-xs font-semibold text-gray-500 mb-1">最近错误</div>
                  <div className="text-xs text-red-500 break-all font-mono bg-red-50 rounded px-3 py-2">{node.lastErrorText}</div>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}