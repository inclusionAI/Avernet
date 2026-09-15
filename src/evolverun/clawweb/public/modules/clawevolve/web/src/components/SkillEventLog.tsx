import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type EvolveSkillEvent } from '../api/client'
import { Icon, PageTitle } from '../pages/evolve/common'
import { formatStepTime } from '../pages/evolve/helpers'
import SkillListPagination, { skillListPageSize } from './SkillListPagination'
import TestBenchComparison from './TestBenchComparison'

const eventNames: Record<string, string> = { registered: '登记 Skill', diagnosis_started: '发起技能诊断', diagnosis_finished: '技能诊断结束', evolution_started: '发起技能进化', evolution_finished: '技能进化结束', candidate_accepted: '接受候选版本', candidate_rejected: '拒绝候选版本', version_applied: '应用技能版本', version_apply_failed: '版本应用失败' }
const resultNames: Record<string, string> = { succeeded: '成功', pending: '已发起', retry_pending: '已重试', waiting_acceptance: '等待版本确认', completed: '已完成', not_improved: '未提升', candidate_not_accepted: '候选未获采纳', no_cases: '无诊断案例', failed: '失败', canceled: '已停止', dispatch_failed: '投递失败', accepted: '已接受', rejected: '已拒绝', conflict: '版本冲突' }
const eventTones: Record<string, string> = {
  registered: 'border-blue-100 bg-blue-50 text-blue-700',
  diagnosis_started: 'border-amber-100 bg-amber-50 text-amber-700', diagnosis_finished: 'border-amber-100 bg-amber-50 text-amber-700',
  evolution_started: 'border-emerald-100 bg-emerald-50 text-emerald-700', evolution_finished: 'border-emerald-100 bg-emerald-50 text-emerald-700',
  candidate_accepted: 'border-violet-100 bg-violet-50 text-violet-700', candidate_rejected: 'border-violet-100 bg-violet-50 text-violet-700',
  version_applied: 'border-indigo-100 bg-indigo-50 text-indigo-700', version_apply_failed: 'border-red-100 bg-red-50 text-red-700',
}
const resultTones: Record<string, string> = {
  succeeded: 'bg-emerald-50 text-emerald-700', completed: 'bg-emerald-50 text-emerald-700', accepted: 'bg-emerald-50 text-emerald-700',
  pending: 'bg-blue-50 text-blue-700', retry_pending: 'bg-blue-50 text-blue-700', waiting_acceptance: 'bg-blue-50 text-blue-700',
  no_cases: 'bg-amber-50 text-amber-700', not_improved: 'bg-amber-50 text-amber-700', conflict: 'bg-amber-50 text-amber-700',
  failed: 'bg-red-50 text-red-700', dispatch_failed: 'bg-red-50 text-red-700', rejected: 'bg-red-50 text-red-700',
  candidate_not_accepted: 'bg-gray-100 text-gray-600', canceled: 'bg-gray-100 text-gray-600',
}

export default function SkillEventLog() {
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
  const filtered = items.filter((item) => (!type || item.type === type) && [item.name, item.ownerId, item.botId].some((value) => value?.toLowerCase().includes(query.trim().toLowerCase())))
  const visiblePage = Math.min(page, Math.max(1, Math.ceil(filtered.length / skillListPageSize)))
  return <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
    <PageTitle title="技能事件日志" description="查看技能登记、进化和版本操作记录。" />
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
        <select aria-label="事件类型" value={type} onChange={(event) => { setType(event.target.value); setPage(1) }} className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600"><option value="">全部事件类型</option>{[...new Set(items.map((item) => item.type))].map((value) => <option key={value} value={value}>{eventNames[value] ?? value}</option>)}</select>
        <input aria-label="搜索技能事件" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="搜索技能名称、Owner ID 或 Bot ID" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80" />
      </div>
      <div className="overflow-x-auto"><table className="w-full min-w-[1300px] table-fixed text-left text-sm">
        <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr><th className="w-[22%] px-5 py-3">技能名称</th><th className="w-36 px-4 py-3">事件类型</th><th className="w-[13%] px-4 py-3">Owner ID</th><th className="w-[15%] px-4 py-3">所属 Bot</th><th className="w-20 px-4 py-3">版本</th><th className="w-56 px-4 py-3">Test Bench</th><th className="w-40 px-4 py-3">发生时间</th><th className="sticky right-0 z-10 w-[90px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th></tr></thead>
        <tbody className="divide-y divide-gray-100">{filtered.slice((visiblePage - 1) * skillListPageSize, visiblePage * skillListPageSize).map((item) => <tr key={item.eventId} className="group transition hover:bg-gray-50/70">
          <td className="px-5 py-4"><div className="flex items-center gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="spark" /></span><div className="min-w-0"><Link className="block truncate font-medium text-gray-900 hover:text-blue-600 hover:underline" to={`/evolve/skills/${encodeURIComponent(item.assetId)}`}>{item.name}</Link>{item.description && <p className="mt-0.5 truncate text-xs text-gray-400" title={item.description}>{item.description}</p>}</div></div></td>
          <td className="px-4 py-4"><div className="flex flex-col items-start gap-1.5"><span className={`inline-flex rounded-full border px-2 py-1 text-[11px] font-medium ${eventTones[item.type] ?? 'border-gray-200 bg-gray-50 text-gray-600'}`}>{eventNames[item.type] ?? item.type}</span><span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${resultTones[item.result] ?? 'bg-gray-100 text-gray-600'}`}>{resultNames[item.result] ?? item.result}</span><span className="max-w-full truncate text-[10px] text-gray-400" title={item.actorId ?? undefined}>发起人 · <span>{item.actorType === 'system' ? '系统' : item.actorId}</span></span></div></td>
          <td className="px-4 py-4"><span className="inline-block max-w-full truncate rounded-md border border-gray-100 bg-gray-50 px-2 py-1 font-mono text-[10px] text-gray-600">{item.ownerId ?? '—'}</span></td>
          <td className="px-4 py-4"><span className="inline-block max-w-full truncate rounded-md border border-gray-100 bg-gray-50 px-2 py-1 font-mono text-[10px] text-gray-600" title={item.botId}>{item.botId}</span></td>
          <td className="px-4 py-4"><span className="inline-flex rounded-md border border-blue-100 bg-blue-50 px-2 py-1 font-mono text-[10px] font-medium text-blue-700">{item.version ?? '—'}</span></td>
          <td className="px-4 py-4"><TestBenchComparison comparison={item.testBench?.scoreComparison} emptyLabel={item.testBench ? '未评测' : '未记录评测关联'} /></td>
          <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500">{formatStepTime(item.createdAt)}</td>
          <td className="sticky right-0 border-l border-gray-100 bg-white px-3 py-4 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] group-hover:bg-gray-50"><Link className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:border-blue-200 hover:bg-blue-100" to={item.taskId ? `/evolve/runs/${encodeURIComponent(item.taskId)}` : `/evolve/skills/${encodeURIComponent(item.assetId)}`}>查看</Link></td>
        </tr>)}</tbody>
      </table></div>
    {loading && <p className="p-10 text-center text-sm text-gray-400">正在加载…</p>}
    {!loading && !error && filtered.length === 0 && <p className="py-16 text-center text-sm text-gray-400">{items.length ? '没有匹配的事件记录' : '暂无技能事件记录。'}</p>}
    {error && <p className="p-5 text-sm text-red-600">{error}</p>}
      <SkillListPagination total={filtered.length} page={visiblePage} onChange={setPage} />
    </section>
  </div>
}
