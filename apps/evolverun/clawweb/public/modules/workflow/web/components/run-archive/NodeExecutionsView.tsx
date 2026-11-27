import { Fragment, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { ActionButton, CodeBlock, Disclosure, EmptyState, inputClass, StatusBadge } from './ArchivePrimitives'
import { finiteNumber, formatDuration, formatTime, nodeStatus, parseTimestamp, prettyJson, STATUS_ORDER, statusLabel, text } from './archive-helpers'

type Execution = RunArchiveData['nodeExecutions'][number]

function executionIdentity(node: Execution): string | null {
  if ((typeof node.id === 'number' && Number.isFinite(node.id))
    || (typeof node.id === 'string' && node.id.trim())) {
    return `id:${JSON.stringify(node.id)}`
  }
  const nodeId = text(node.node_id, '')
  const attempt = finiteNumber(node.attempt)
  const startedAt = parseTimestamp(node.started_at)?.getTime() ?? null
  // Insufficient or duplicate identity must not carry expansion to a fresh record.
  if (!nodeId || (attempt === null && startedAt === null)) return null
  return `execution:${JSON.stringify([
    node.flow_id ?? null, nodeId, attempt, startedAt, node.executor_type ?? null,
  ])}`
}

export default function NodeExecutionsView({ data }: { data: RunArchiveData }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [expanded, setExpanded] = useState<string | null>(null)
  const objectIds = useRef(new WeakMap<Execution, number>())
  const nextObjectId = useRef(0)
  const rows = useMemo(() => {
    const identities = data.nodeExecutions.map(executionIdentity)
    const counts = new Map<string, number>()
    identities.forEach(identity => {
      if (identity) counts.set(identity, (counts.get(identity) ?? 0) + 1)
    })
    const occurrences = new Map<string, number>()
    return data.nodeExecutions.map((node, index) => {
      const identity = identities[index]
      if (identity && counts.get(identity) === 1) return { node, key: identity }
      // Ambiguous rows keep identity only while this exact object survives.
      // Refetched copies receive new keys and safely collapse, even after reorder.
      if (!objectIds.current.has(node)) objectIds.current.set(node, ++nextObjectId.current)
      const objectKey = `ambiguous:${objectIds.current.get(node)}`
      const occurrence = occurrences.get(objectKey) ?? 0
      occurrences.set(objectKey, occurrence + 1)
      return { node, key: `${objectKey}:${occurrence}` }
    })
  }, [data.nodeExecutions])
  useEffect(() => {
    if (expanded !== null && !rows.some(row => row.key === expanded)) setExpanded(null)
  }, [rows, expanded])
  const nodes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return rows.filter(({ node }) =>
      (filter === 'all' || nodeStatus(node.status) === filter)
      && [node.node_id, node.node_title, node.executor_type, node.error_text]
        .map(value => text(value, '')).join(' ').toLocaleLowerCase().includes(needle))
  }, [rows, filter, query])
  const reset = () => { setQuery(''); setFilter('all') }

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1 basis-48 text-xs text-muted-foreground">
          <span className="mb-1 block">搜索节点</span>
          <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="节点 ID、名称、执行器或错误" className={`${inputClass} w-full`} />
        </label>
        <label className="min-w-0 text-xs text-muted-foreground">
          <span className="mb-1 block">节点状态</span>
          <select value={filter} onChange={event => setFilter(event.target.value)} className={inputClass}>
            <option value="all">全部状态（{data.nodeExecutions.length}）</option>
            {STATUS_ORDER.map(status => <option key={status} value={status}>{statusLabel(status)}（{data.nodeExecutions.filter(node => nodeStatus(node.status) === status).length}）</option>)}
          </select>
        </label>
        <p role="status" className="pb-2 text-xs tabular-nums text-muted-foreground">{nodes.length} / {data.nodeExecutions.length} 条执行记录</p>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">保留所有已采集执行次数；未采集的节点不补为跳过。使用节点按钮展开输入、输出与原始记录。</p>
      {!data.nodeExecutions.length ? (
        <EmptyState title="暂无节点执行记录" description="档案没有采集到节点执行数据。不能据此推断节点已成功、已跳过或从未执行。" />
      ) : !nodes.length ? (
        <EmptyState title="没有匹配的节点" description="当前关键词或状态没有匹配到执行记录，请调整筛选条件。">
          <ActionButton onClick={reset}>清除节点筛选</ActionButton>
        </EmptyState>
      ) : (
        <div className="max-h-[40rem] min-w-0 max-w-full overflow-auto rounded-lg border border-border" tabIndex={0} aria-label="节点执行列表">
          <table className="w-full min-w-[38rem] text-left text-sm">
            <caption className="sr-only">已采集的节点执行记录</caption>
            <thead className="bg-muted text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-3 font-medium">节点 / 执行次数</th>
                <th scope="col" className="px-3 py-3 font-medium">状态</th>
                <th scope="col" className="px-3 py-3 font-medium">执行器</th>
                <th scope="col" className="px-3 py-3 font-medium">耗时</th>
                <th scope="col" className="px-3 py-3 font-medium">开始时间</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map(({ node, key }) => (
                <NodeExecutionRows
                  key={key}
                  node={node}
                  isExpanded={expanded === key}
                  onToggle={() => setExpanded(expanded === key ? null : key)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function NodeExecutionRows({ node, isExpanded, onToggle }: {
  node: Execution
  isExpanded: boolean
  onToggle: () => void
}) {
  const detailId = useId()
  const nodeId = text(node.node_id)
  const attempt = text(node.attempt)
  return (
    <Fragment>
      <tr className="border-t border-border align-top hover:bg-muted/50">
        <td className="max-w-64 px-3 py-3">
          <button
            type="button"
            aria-label={`查看节点 ${nodeId} 第 ${attempt} 次执行`}
            aria-expanded={isExpanded}
            aria-controls={detailId}
            onClick={onToggle}
            className="flex max-w-full items-start gap-2 rounded text-left text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <span aria-hidden="true" className="shrink-0">{isExpanded ? '▾' : '▸'}</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">
              <span className="block font-medium">{text(node.node_title, nodeId)}</span>
              <span className="mt-1 block font-mono text-xs text-muted-foreground">
                {nodeId} · 第 {attempt} 次
              </span>
            </span>
          </button>
        </td>
        <td className="px-3 py-3"><StatusBadge status={node.status} /></td>
        <td className="max-w-40 px-3 py-3 text-xs text-muted-foreground [overflow-wrap:anywhere]">
          {text(node.executor_type)}
        </td>
        <td className="whitespace-nowrap px-3 py-3 text-xs tabular-nums text-muted-foreground">
          {formatDuration(node.duration_ms)}
        </td>
        <td className="px-3 py-3 text-xs tabular-nums text-muted-foreground">
          {formatTime(node.started_at)}
        </td>
      </tr>
      <tr id={detailId} hidden={!isExpanded} className="border-t border-border bg-muted/20">
        <td colSpan={5} className="p-4">
          {isExpanded && (
            <div className="min-w-0 space-y-3">
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">
                <span>结束时间：{formatTime(node.completed_at)}</span>
                <span className="[overflow-wrap:anywhere]">
                  Session：{text(node.embedded_session_key ?? node.session_key)}
                </span>
              </div>
              {node.error_text != null && (
                <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive [overflow-wrap:anywhere]">
                  {text(node.error_text)}
                </p>
              )}
              <div className="grid min-w-0 gap-3 xl:grid-cols-2">
                <section className="min-w-0">
                  <h4 className="mb-2 text-xs font-semibold text-foreground">Input</h4>
                  <CodeBlock value={node.input_json} label={`节点 ${nodeId} Input`} />
                </section>
                <section className="min-w-0">
                  <h4 className="mb-2 text-xs font-semibold text-foreground">Output</h4>
                  <CodeBlock value={node.output_json} label={`节点 ${nodeId} Output`} />
                </section>
              </div>
              <Disclosure title="完整执行记录">
                <CodeBlock value={prettyJson(node)} label={`节点 ${nodeId} 完整执行记录`} />
              </Disclosure>
            </div>
          )}
        </td>
      </tr>
    </Fragment>
  )
}
