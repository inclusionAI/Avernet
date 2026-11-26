import { useId, useState } from 'react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import RunDagView, { type RunDagViewProps } from './RunDagView'

export interface RunDagSectionProps extends Omit<RunDagViewProps, 'specNodes' | 'expanded'> {
  specNodes?: WorkflowNode[]
  readOnly?: boolean
  snapshotError?: boolean
  definitionError?: boolean
  onRetryDefinition?: () => void
}

const LEGEND = [
  { label: '成功', color: 'bg-success' },
  { label: '运行中', color: 'bg-primary' },
  { label: '失败 / 中止', color: 'bg-destructive' },
  { label: '等待 / 阻塞', color: 'bg-warning' },
  { label: '待执行 / 跳过 / 未记录', color: 'bg-muted-foreground' },
]

export default function RunDagSection({
  specNodes,
  nodeStatusMap,
  onNodeClick,
  selectedNodeId,
  readOnly = false,
  snapshotError = false,
  definitionError = false,
  onRetryDefinition,
}: RunDagSectionProps) {
  const headingId = useId()
  const canvasId = useId()
  const [expanded, setExpanded] = useState(false)
  // Historical snapshots and live definitions have independent error boundaries.
  // Never show stale nodes (or use execution records as a replacement topology).
  const snapshotUnavailable = readOnly && snapshotError
  const definitionUnavailable = !readOnly && definitionError
  const unavailable = snapshotUnavailable || definitionUnavailable
  const visibleNodes = unavailable ? undefined : specNodes
  const canRenderGraph = !unavailable && visibleNodes !== undefined
  const selectedNode = visibleNodes?.find(node => node.id === selectedNodeId)

  return (
    <section className="min-w-0 overflow-hidden rounded-2xl border border-border bg-card text-card-foreground shadow-sm" aria-labelledby={headingId}>
      <header className="flex flex-wrap items-start justify-between gap-3 px-4 py-4 sm:px-5">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex flex-wrap items-center gap-2">
            <svg className="h-4 w-4 shrink-0 text-primary" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <rect x="1.5" y="7" width="5" height="6" rx="1.5" />
              <rect x="13.5" y="1.5" width="5" height="6" rx="1.5" />
              <rect x="13.5" y="12.5" width="5" height="6" rx="1.5" />
              <path d="M6.5 10H10V4.5h3.5M10 10v5.5h3.5" />
            </svg>
            <h2 id={headingId} className="text-sm font-semibold">运行 DAG</h2>
            {visibleNodes !== undefined && (
              <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                {visibleNodes.length} 个节点
              </span>
            )}
            {readOnly && <span className="text-[10px] text-muted-foreground">历史快照 · 只读</span>}
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            查看全局执行路径，点击节点定位下方执行详情。
          </p>
        </div>
        <button
          type="button"
          aria-label={expanded ? '收起 DAG 画布' : '展开 DAG 画布'}
          aria-expanded={expanded}
          aria-controls={canvasId}
          disabled={!canRenderGraph || visibleNodes.length === 0}
          onClick={() => setExpanded(value => !value)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d={expanded ? 'M1 5h4V1m10 4h-4V1M1 11h4v4m10-4h-4v4' : 'M5 1H1v4m10-4h4v4M1 11v4h4m6 0h4v-4'} />
          </svg>
          {expanded ? '收起' : '展开'}
        </button>
      </header>

      <div id={canvasId} className="border-y border-border">
        {snapshotUnavailable ? (
          <p role="alert" className="flex min-h-40 items-center justify-center bg-muted/20 px-5 py-8 text-sm text-muted-foreground">
            无法确认本次运行的历史 DAG：可信运行快照不可用或无权访问。不会使用当前工作流定义替代。
          </p>
        ) : definitionUnavailable ? (
          <div className="flex min-h-40 flex-col items-center justify-center gap-3 bg-muted/20 px-5 py-8 text-center">
            <p role="alert" className="text-sm text-muted-foreground">
              工作流节点定义加载失败，暂无法展示 DAG。
            </p>
            {onRetryDefinition && (
              <button
                type="button"
                onClick={onRetryDefinition}
                className="rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                重新加载节点定义
              </button>
            )}
          </div>
        ) : visibleNodes === undefined ? (
          <p role="status" className="flex min-h-40 items-center justify-center bg-muted/20 px-5 py-8 text-sm text-muted-foreground">
            {readOnly ? '正在加载本次运行的静态 DAG 快照…' : '正在加载工作流节点定义…'}
          </p>
        ) : (
          <RunDagView
            specNodes={visibleNodes}
            nodeStatusMap={nodeStatusMap}
            onNodeClick={onNodeClick}
            selectedNodeId={selectedNodeId}
            expanded={expanded}
          />
        )}
      </div>

      <footer className="space-y-2 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[10px] text-muted-foreground">
          <ul className="flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="节点状态图例">
            {LEGEND.map(item => (
              <li key={item.label} className="inline-flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${item.color}`} aria-hidden="true" />
                {item.label}
              </li>
            ))}
          </ul>
          <span aria-live="polite" className={selectedNode ? 'font-medium text-primary' : ''}>
            {selectedNode ? `已选择：${selectedNode.title || selectedNode.id}` : '选择节点，快速定位执行记录 ↓'}
          </span>
        </div>
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          {readOnly
            ? '静态 DAG，来自运行登记时绑定的快照，不含动态节点；尚未验证其与引擎实际执行配置一致。'
            : '仅展示工作流定义中的节点；无执行记录的节点保留定义，不推断执行结果。'}
        </p>
      </footer>
    </section>
  )
}
