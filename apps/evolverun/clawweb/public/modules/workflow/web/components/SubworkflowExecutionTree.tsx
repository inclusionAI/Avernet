import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'

// A UI guard only; it does not describe the engine's maximum nesting depth.
const MAX_DEPTH = 8
const POLL_INTERVAL_MS = 15_000

type SubworkflowNode = { nodeId?: string | null; nodeTitle?: string | null; status?: string | null; attempt?: number | null }
type Subworkflow = {
  parentNodeId?: string | null
  childFlowId: string
  childWorkflowId?: string | null
  status?: string | null
  startedAt?: string | number | null
  completedAt?: string | number | null
  nodes?: SubworkflowNode[] | null
}
type Response = { items: Subworkflow[]; completeness: 'best_effort' }

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中', queued: '排队中', running: '运行中', succeeded: '成功',
  failed: '失败', cancelled: '已取消', canceled: '已取消', paused: '已暂停',
  skipped: '已跳过', waiting: '等待中', postActionsRunning: '后处理运行中',
  rejected: '已拒绝', blocked: '已阻塞', completed: '已完成',
}

function label(status: unknown): string {
  return typeof status === 'string' && Object.hasOwn(STATUS_LABELS, status) ? STATUS_LABELS[status] : '未知'
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

async function loadChildren(flowId: string, signal: AbortSignal): Promise<Response> {
  const response = await fetchJson<Response>(`/api/runs/${encodeURIComponent(flowId)}/subworkflows`, { signal })
  if (!response || !Array.isArray(response.items) || response.completeness !== 'best_effort') {
    throw new Error('Invalid subworkflow response')
  }
  return response
}

function Children({ flowId, ancestors, depth }: { flowId: string; ancestors: string[]; depth: number }) {
  const query = useQuery({
    queryKey: ['subworkflow-execution-tree', flowId],
    queryFn: ({ signal }) => loadChildren(flowId, signal),
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    retry: false,
    gcTime: 0,
  })
  // Do not continue displaying stale child records after a failed authorization/refetch.
  if (query.isError) return <div className="text-sm text-destructive"><p role="alert">子运行加载失败，结果未知。</p><button type="button" className="underline" onClick={() => void query.refetch()}>重试 {flowId}</button></div>
  if (query.isLoading || !query.data) return <p role="status" className="text-sm text-muted-foreground">正在加载子运行…</p>

  const children = new Map<string, Subworkflow>()
  let invalid = false
  for (const item of query.data.items) {
    const id = item && text(item.childFlowId)
    if (!id) { invalid = true; continue }
    if (!children.has(id)) children.set(id, item)
  }
  return <div className="space-y-2">
    <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()} className="text-sm text-primary underline disabled:opacity-50">刷新 {flowId} 的子运行</button>
    {query.isFetching && <span role="status" className="ml-2 text-sm text-muted-foreground">刷新中…</span>}
    {invalid && <p className="text-sm text-muted-foreground">部分子运行记录无法识别，结果可能不完整。</p>}
    {children.size === 0 ? <p className="text-sm text-muted-foreground">当前没有可显示的子运行记录。</p> : <ul className="space-y-2">
      {[...children.values()].map(child => <ChildRun key={child.childFlowId} child={child} ancestors={ancestors} depth={depth} />)}
    </ul>}
  </div>
}

function ChildRun({ child, ancestors, depth }: { child: Subworkflow; ancestors: string[]; depth: number }) {
  const [expanded, setExpanded] = useState(false)
  const cyclic = ancestors.includes(child.childFlowId)
  const tooDeep = depth >= MAX_DEPTH
  return <li className="rounded-lg border border-border p-3">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="font-medium text-primary underline">{expanded ? '收起' : '展开'}子运行 {child.childFlowId}</button>
      <span>状态：{label(child.status)}</span>
    </div>
    <p className="mt-1 text-xs text-muted-foreground">工作流：{text(child.childWorkflowId) ?? '未知'} · 父节点：{text(child.parentNodeId) ?? '未知'}</p>
    {expanded && <div className="mt-3 space-y-3">
      <div>
        <h4 className="text-sm font-medium">子运行节点</h4>
        {!Array.isArray(child.nodes) ? <p className="text-sm text-muted-foreground">节点记录未知。</p> : child.nodes.length === 0 ? <p className="text-sm text-muted-foreground">当前没有可显示的节点记录。</p> : <ul className="mt-1 space-y-1 text-sm">{child.nodes.map((node, index) => {
          const nodeId = text(node?.nodeId)
          const title = text(node?.nodeTitle)
          return <li key={`${nodeId ?? 'unknown'}-${node?.attempt ?? 'unknown'}-${index}`}>{title ?? nodeId ?? '未知节点'}{title && nodeId ? `（${nodeId}）` : ''}：{label(node?.status)}{typeof node?.attempt === 'number' && Number.isFinite(node.attempt) ? ` · 第 ${node.attempt} 次` : ''}</li>
        })}</ul>}
      </div>
      {cyclic ? <p role="status" className="text-sm text-muted-foreground">检测到循环引用，停止展开。</p> : tooDeep ? <p role="status" className="text-sm text-muted-foreground">已达到显示深度限制，停止展开。</p> : <div className="border-l border-border pl-3">
        <h4 className="mb-2 text-sm font-medium">下一级子运行</h4>
        <Children flowId={child.childFlowId} ancestors={[...ancestors, child.childFlowId]} depth={depth + 1} />
      </div>}
    </div>}
  </li>
}

/** An authorized, best-effort view; it does not prove lineage completeness. */
export default function SubworkflowExecutionTree({ flowId }: { flowId: string }) {
  return <section aria-label="子工作流执行树" className="space-y-3">
    <h3 className="text-base font-semibold">子工作流执行树</h3>
    <p className="text-sm text-muted-foreground">仅展示可访问的记录；结果为尽力提供，可能不完整。</p>
    {flowId ? <Children key={flowId} flowId={flowId} ancestors={[flowId]} depth={1} /> : <p className="text-sm text-muted-foreground">运行标识未知。</p>}
  </section>
}
