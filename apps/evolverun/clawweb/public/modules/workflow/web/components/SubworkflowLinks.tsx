import { createContext, useContext, useEffect, useId, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useObservedAuthEpoch } from '../hooks/useObservedAuthEpoch'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ChildRunDrawer from './ChildRunDrawer'

const POLL_INTERVAL_MS = 15_000
const MAX_DEPTH = 8

type Child = { parentNodeId?: unknown; childFlowId?: unknown; parentAttempt?: unknown }
type Response = { items: Child[]; completeness: 'best_effort' }

async function loadChildren(flowId: string, signal: AbortSignal): Promise<Response> {
  const result = await fetchJson<Response>(`/api/runs/${encodeURIComponent(flowId)}/subworkflows`, { signal })
  if (!result || !Array.isArray(result.items) || result.completeness !== 'best_effort') throw new Error('Invalid response')
  return result
}

type ChildrenQuery = ReturnType<typeof useChildrenQuery>
const ChildrenContext = createContext<ChildrenQuery | null>(null)

function useChildrenQuery(flowId: string, epoch: number) {
  // Isolate each mounted run view from previously authorized data in another session.
  const observerId = useId()
  const client = useQueryClient()
  const queryKey = ['subworkflow-node-links', observerId, flowId, epoch] as const
  useEffect(() => () => { void client.cancelQueries({ queryKey, exact: true }); client.removeQueries({ queryKey, exact: true }) }, [client, observerId, flowId, epoch])
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => loadChildren(flowId, signal),
    enabled: !!flowId, retry: false, gcTime: 0,
    refetchInterval: POLL_INTERVAL_MS, refetchIntervalInBackground: false,
  })
}

/** One authorized request and poller shared by all subworkflow nodes in this run. */
export function SubworkflowProvider({ flowId, children }: { flowId: string; children: ReactNode }) {
  const epoch = useObservedAuthEpoch()
  return epoch === null
    ? <ChildrenContext.Provider value={null}>{children}</ChildrenContext.Provider>
    : <ActiveSubworkflowProvider key={epoch} flowId={flowId} epoch={epoch}>{children}</ActiveSubworkflowProvider>
}

function ActiveSubworkflowProvider({ flowId, epoch, children }: { flowId: string; epoch: number; children: ReactNode }) {
  const query = useChildrenQuery(flowId, epoch)
  return <ChildrenContext.Provider value={query}>{children}</ChildrenContext.Provider>
}

/** Only the authorized observability route may supply child identifiers. */
export default function SubworkflowLinks({ nodeId, ancestors, renderRun }: {
  nodeId: string; ancestors: string[]; renderRun: (flowId: string) => ReactNode
}) {
  const query = useContext(ChildrenContext)
  if (!query) return <p role="status" className="mt-1 text-xs text-muted-foreground">正在验证身份…</p>
  return <NodeLinks query={query} match={item => item.parentNodeId === nodeId} ancestors={ancestors} renderRun={renderRun} />
}

export function UnattributedSubworkflowLinks({ nodeIds, ancestors, renderRun }: {
  nodeIds: string[]; ancestors: string[]; renderRun: (flowId: string) => ReactNode
}) {
  const query = useContext(ChildrenContext)
  if (!query) return null
  return <NodeLinks query={query} match={item => typeof item.parentNodeId !== 'string' || !nodeIds.includes(item.parentNodeId)} ancestors={ancestors} renderRun={renderRun} unattributed />
}

function NodeLinks({ query, match, ancestors, renderRun, unattributed = false }: {
  query: ChildrenQuery; match: (item: Child) => boolean; ancestors: string[]; renderRun: (flowId: string) => ReactNode; unattributed?: boolean
}) {
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => {
    if (query.isError || (query.data && selected && !query.data.items.some(item => item && match(item) && item.childFlowId === selected))) setSelected(null)
  }, [query.isError, query.data, selected, match])
  if (unattributed && (query.isError || !query.data)) return null
  if (query.isError) return <div className="mt-1 text-xs text-destructive" onClick={e => e.stopPropagation()}>
    <span role="alert">子运行加载失败。</span>{' '}
    <button type="button" className="underline" onClick={() => void query.refetch()}>重试</button>
  </div>
  if (query.isLoading || !query.data) return <p role="status" className="mt-1 text-xs text-muted-foreground">正在加载子运行…</p>

  const children = new Map<string, { id: string; attempt: number | null }>()
  for (const item of query.data.items) {
    if (!item || !match(item) || typeof item.childFlowId !== 'string' || !item.childFlowId.trim()) continue
    const attempt = typeof item.parentAttempt === 'number' && Number.isSafeInteger(item.parentAttempt) && item.parentAttempt > 0 ? item.parentAttempt : null
    const id = item.childFlowId
    children.set(`${id}\u0000${attempt ?? 'unknown'}`, { id, attempt })
  }
  if (unattributed && children.size === 0) return null
  const canOpen = selected !== null && [...children.values()].some(child => child.id === selected) && !ancestors.includes(selected) && ancestors.length < MAX_DEPTH
  return <div className="mt-1 text-xs" onClick={e => e.stopPropagation()}>
    <p className="text-muted-foreground">{unattributed ? '无归属子运行（仅显示可访问记录，结果可能不完整）' : '子运行（仅显示可访问记录，结果可能不完整）'}</p>
    {children.size === 0 ? <p className="text-muted-foreground">暂无可显示的子运行。</p> : <ul>
      {[...children.values()].map(({ id, attempt }) => <li key={`${id}\u0000${attempt ?? 'unknown'}`}>
        <span className="text-muted-foreground">{attempt === null ? '尝试次数未知' : `第 ${attempt} 次尝试`}： </span>
        {ancestors.includes(id) || ancestors.length >= MAX_DEPTH ? <span>{id}（无法继续展开）</span> : <a
          href={`/runs/${encodeURIComponent(id)}`}
          className="break-all text-primary underline"
          onClick={event => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); setSelected(id) } }}
        >{id}</a>}
      </li>)}
    </ul>}
    {canOpen && <ChildRunDrawer key={selected} onClose={() => setSelected(null)}>{renderRun(selected)}</ChildRunDrawer>}
  </div>
}
