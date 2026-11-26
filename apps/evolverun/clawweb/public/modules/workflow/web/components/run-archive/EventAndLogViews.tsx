import { useMemo, useState } from 'react'
import type { RunArchiveData } from '@avernet/clawweb-shared/web/types'
import { ActionButton, Badge, CodeBlock, Disclosure, EmptyState, inputClass } from './ArchivePrimitives'
import { formatTime, isErrorEvent, prettyJson, text } from './archive-helpers'

export function EventTimeline({ data }: { data: RunArchiveData }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const events = useMemo(() => data.flowEvents.filter(event =>
    (filter === 'all' || isErrorEvent(event))
    && prettyJson(event).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [data.flowEvents, query, filter])
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1 basis-48 text-xs text-muted-foreground"><span className="mb-1 block">搜索事件</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="事件类型、节点或详情" className={`${inputClass} w-full`} /></label>
        <label className="min-w-0 text-xs text-muted-foreground"><span className="mb-1 block">事件类型</span><select value={filter} onChange={event => setFilter(event.target.value)} className={inputClass}><option value="all">全部事件</option><option value="error">错误事件</option></select></label>
        <p role="status" className="pb-2 text-xs tabular-nums text-muted-foreground">{events.length} / {data.flowEvents.length} 条事件</p>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">按档案返回顺序展示；时间先后不代表因果。可展开查看事件原始数据。</p>
      {!data.flowEvents.length ? <EmptyState title="暂无事件记录" description="档案未采集到事件流，无法从此处还原执行经过。可继续核对节点执行与运行日志。" />
        : !events.length ? <EmptyState title="没有匹配的事件" description="请更换关键词或事件类型，筛选无结果不代表运行没有异常。"><ActionButton onClick={() => { setQuery(''); setFilter('all') }}>清除事件筛选</ActionButton></EmptyState>
          : <ol className="max-h-[36rem] min-w-0 space-y-3 overflow-y-auto" aria-label="事件流记录">
            {events.map((event, index) => (
              <li key={index} className="min-w-0 rounded-lg border border-border bg-card p-3">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-xs tabular-nums text-muted-foreground">{formatTime(event.time ?? event.timestamp ?? event.created_at)}</span>
                  <Badge className={isErrorEvent(event) ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}>{text(event.event_type, '未知事件')}</Badge>
                  {event.node_id != null && <span className="min-w-0 font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{text(event.node_id)}</span>}
                </div>
                {event.error_text != null && <p className="mt-2 text-sm text-destructive [overflow-wrap:anywhere]">{text(event.error_text)}</p>}
                <div className="mt-3"><Disclosure title={`事件详情 ${index + 1}`}><CodeBlock value={event} label={`事件 ${index + 1} 原始数据`} /></Disclosure></div>
              </li>
            ))}
          </ol>}
    </div>
  )
}

export function RunLogsView({ data }: { data: RunArchiveData }) {
  const [query, setQuery] = useState('')
  const [level, setLevel] = useState('all')
  const levels = Array.from(new Set(['error', 'warn', 'info', 'debug', 'trace', ...data.runLogs.map(log => log.level.toLowerCase())]))
  const logs = useMemo(() => data.runLogs.filter(log => {
    const logLevel = log.level.toLowerCase()
    const matchesLevel = level === 'all' || (level === 'warn+' ? logLevel === 'error' || logLevel === 'warn' : logLevel === level)
    const searchable = [log.message, log.source, log.node_id, log.level].map(value => text(value, '')).join(' ')
    return matchesLevel && searchable.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  }), [data.runLogs, query, level])
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1 basis-48 text-xs text-muted-foreground"><span className="mb-1 block">搜索运行日志</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="内容、来源或节点 ID" className={`${inputClass} w-full`} /></label>
        <label className="min-w-0 text-xs text-muted-foreground"><span className="mb-1 block">日志等级</span><select value={level} onChange={event => setLevel(event.target.value)} className={inputClass}><option value="all">全部等级</option><option value="warn+">WARN+</option>{levels.map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}</select></label>
        <p role="status" className="pb-2 text-xs tabular-nums text-muted-foreground">{logs.length} / {data.runLogs.length} 条日志</p>
      </div>
      {!data.runLogs.length ? <EmptyState title="暂无运行日志" description="档案未采集到日志；这不表示执行过程没有输出或没有异常。可通过节点和事件记录继续排查。" />
        : !logs.length ? <EmptyState title="没有匹配的运行日志" description="当前关键词或日志等级没有匹配记录，请调整筛选条件。"><ActionButton onClick={() => { setQuery(''); setLevel('all') }}>清除日志筛选</ActionButton></EmptyState>
          : <ol tabIndex={0} aria-label="运行日志记录" className="max-h-[36rem] min-w-0 max-w-full space-y-0 overflow-auto rounded-lg border border-border bg-muted/40 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            {logs.map((log, index) => (
              <li key={`${log.id}-${index}`} className="min-w-0 border-b border-border p-3 last:border-0">
                <div className="mb-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
                  <span className="tabular-nums">{formatTime(log.timestamp)}</span>
                  <span className={log.level.toLowerCase() === 'error' ? 'font-semibold text-destructive' : 'font-semibold text-foreground'}>[{log.level.toUpperCase()}]</span>
                  {log.source && <span className="[overflow-wrap:anywhere]">{log.source}</span>}
                  {log.node_id && <span className="[overflow-wrap:anywhere]">{log.node_id}</span>}
                </div>
                <p className="whitespace-pre-wrap leading-6 text-foreground [overflow-wrap:anywhere]">{log.message}</p>
              </li>
            ))}
          </ol>}
    </div>
  )
}
