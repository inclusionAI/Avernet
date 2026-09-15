import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type EvolveSkillEvent } from '../api/client'
import { Icon, PageTitle } from '../pages/evolve/common'
import { formatStepTime } from '../pages/evolve/helpers'
import SkillListPagination, { skillListPageSize } from './SkillListPagination'
import TestBenchComparison from './TestBenchComparison'

const eventNames = { registered: '登记', diagnosis: '诊断', optimization: '优化' } as const
const statusNames = {
  running: '运行中', waiting_user_input: '等待用户输入', waiting_acceptance: '等待版本确认',
  completed: '已完成', failed: '失败', canceled: '已取消',
} as const
const eventTones = {
  registered: 'border-blue-100 bg-blue-50 text-blue-700',
  diagnosis: 'border-amber-200 bg-amber-50 text-amber-700',
  optimization: 'border-emerald-200 bg-emerald-50 text-emerald-700',
} as const
const statusTones = {
  running: 'bg-blue-50 text-blue-700', waiting_user_input: 'bg-amber-100 text-amber-900',
  waiting_acceptance: 'bg-violet-50 text-violet-700', completed: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700', canceled: 'bg-gray-100 text-gray-600',
} as const

function skillDetailPath(event: EvolveSkillEvent, backTo: string): string {
  const query = new URLSearchParams()
  if (event.taskId) { query.set('selected', `task:${event.taskId}`); query.set('view', 'task') }
  else if (event.versionTo) { query.set('selected', `version:${event.versionTo.versionId}`); query.set('view', 'content') }
  query.set('backTo', backTo)
  return `/evolve/skills/${encodeURIComponent(event.assetId)}${query.size ? `?${query}` : ''}`
}

function taskDetailPath(taskId: string, returnTo: string): string {
  return `/evolve/runs/${encodeURIComponent(taskId)}?${new URLSearchParams({ returnTo })}`
}

function eventDetailPath(event: EvolveSkillEvent, returnTo: string): string {
  return event.taskId ? taskDetailPath(event.taskId, returnTo) : skillDetailPath(event, returnTo)
}

function eventVersion(event: EvolveSkillEvent): string {
  if (event.versionTo && event.versionFrom) return `${event.versionFrom.version} → ${event.versionTo.version}`
  if (event.versionTo) return event.versionTo.version
  if (event.versionFrom) return event.type === 'optimization' ? `基于 ${event.versionFrom.version}` : event.versionFrom.version
  return '—'
}

export default function SkillEventLog() {
  const location = useLocation()
  const [items, setItems] = useState<EvolveSkillEvent[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [type, setType] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => {
    let active = true
    api.evolve.listSkillEvents().then((result) => { if (active) setItems(result.items) })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : '事件日志加载失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = items.filter((item) => (!type || item.type === type) && [item.name, item.ownerId, item.botId, item.summary, item.taskId].some((value) => value?.toLowerCase().includes(normalizedQuery)))
  const visiblePage = Math.min(page, Math.max(1, Math.ceil(filtered.length / skillListPageSize)))
  const returnTo = `${location.pathname}${location.search}`
  return <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
    <PageTitle title="技能事件日志" description="按任务查看技能登记、诊断和优化结果。一次业务任务只记录一条。" />
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
        <select aria-label="事件类型" value={type} onChange={(event) => { setType(event.target.value); setPage(1) }} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600"><option value="">全部事件类型</option><option value="registered">登记</option><option value="diagnosis">诊断</option><option value="optimization">优化</option></select>
        <input aria-label="搜索技能事件" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="搜索技能、任务、Owner 或 Bot" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80" />
      </div>
      <div className="overflow-x-auto"><table className="w-full min-w-[1280px] table-fixed text-left text-sm">
        <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr><th className="w-[22%] px-5 py-3">技能名称</th><th className="w-28 px-4 py-3">记录类型</th><th className="w-[22%] px-4 py-3">状态与结果</th><th className="w-[15%] px-4 py-3">Owner / Bot</th><th className="w-28 px-4 py-3">版本</th><th className="w-52 px-4 py-3">Test Bench</th><th className="w-32 px-4 py-3">更新时间</th><th className="sticky right-0 z-10 w-[150px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th></tr></thead>
        <tbody className="divide-y divide-gray-100">{filtered.slice((visiblePage - 1) * skillListPageSize, visiblePage * skillListPageSize).map((item) => {
          const waiting = item.status === 'waiting_user_input'
          return <tr key={item.eventId} className={`group transition ${waiting ? 'bg-amber-50/50 hover:bg-amber-50' : 'hover:bg-gray-50/70'}`}>
            <td className="px-5 py-4"><div className="flex items-center gap-3"><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${waiting ? 'bg-amber-100 text-amber-700' : 'bg-blue-50 text-blue-600'}`}><Icon name={waiting ? 'clock' : 'spark'} /></span><div className="min-w-0"><Link className="block truncate font-medium text-gray-900 hover:text-blue-600 hover:underline" to={skillDetailPath(item, returnTo)}>{item.name}</Link>{item.description && <p className="mt-0.5 truncate text-xs text-gray-400" title={item.description}>{item.description}</p>}{item.taskId && <p className="mt-1 truncate font-mono text-[10px] text-gray-400">{item.taskId}</p>}</div></div></td>
            <td className="px-4 py-4"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${eventTones[item.type]}`}>{eventNames[item.type]}</span></td>
            <td className="px-4 py-4"><span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${statusTones[item.status]}`}>{statusNames[item.status]}</span>{item.summary && <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600" title={item.summary}>{item.summary}</p>}{item.outcome && <p className="mt-1 truncate text-[10px] text-gray-400" title={item.outcome}>{item.outcome}</p>}</td>
            <td className="px-4 py-4"><p className="truncate font-mono text-[10px] text-gray-600" title={item.ownerId ?? undefined}>{item.ownerId ?? '—'}</p><p className="mt-1 truncate font-mono text-[10px] text-gray-400" title={item.botId}>{item.botId}</p><p className="mt-1 truncate text-[10px] text-gray-400">{item.actorType === 'system' ? '系统发起' : `发起人 ${item.actorId ?? '—'}`}</p></td>
            <td className="px-4 py-4"><span className="inline-flex rounded-md border border-blue-100 bg-blue-50 px-2 py-1 font-mono text-[10px] font-medium text-blue-700">{eventVersion(item)}</span></td>
            <td className="px-4 py-4"><TestBenchComparison comparison={item.testBench?.scoreComparison} emptyLabel={item.testBench ? '未评测' : '未记录评测关联'} /></td>
            <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500"><p>{formatStepTime(item.updatedAt)}</p>{item.completedAt && <p className="mt-1 text-[10px] text-gray-400">完成 {formatStepTime(item.completedAt)}</p>}</td>
            <td className={`sticky right-0 border-l border-gray-100 px-3 py-4 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] ${waiting ? 'bg-amber-50 group-hover:bg-amber-50' : 'bg-white group-hover:bg-gray-50'}`}><div className="flex items-center justify-center gap-2">{waiting && item.taskId && <Link className="inline-flex items-center justify-center whitespace-nowrap rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700" to={taskDetailPath(item.taskId, returnTo)}>去处理</Link>}<Link className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100" to={eventDetailPath(item, returnTo)}>查看</Link></div></td>
          </tr>
        })}</tbody>
      </table></div>
      {loading && <p className="p-10 text-center text-sm text-gray-400">正在加载…</p>}
      {!loading && !error && filtered.length === 0 && <p className="py-16 text-center text-sm text-gray-400">{items.length ? '没有匹配的事件记录' : '暂无技能事件记录。'}</p>}
      {error && <p className="p-5 text-sm text-red-600">{error}</p>}
      <SkillListPagination total={filtered.length} page={visiblePage} onChange={setPage} />
    </section>
  </div>
}
