import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveStageMode, type EvolveStageSkill, type EvolveStageDevelopment } from '../api/client'
import { Icon, PageTitle, Status } from './evolve/common'
import { formatStepTime, primaryButton } from './evolve/helpers'
import { spaceLabel } from '../components/SpaceSelector'
import SkillListPagination, { skillListPageSize } from '../components/SkillListPagination'

const modeName: Record<EvolveStageMode, string> = {
  preprocess: '前置处理',
  postprocess: '后置处理',
  replace: '整体替换',
}

const stageTagStyle: Record<string, string> = {
  diagnose: 'border-amber-200 bg-amber-50 text-amber-700',
  plan: 'border-blue-200 bg-blue-50 text-blue-700',
  optimize: 'border-emerald-200 bg-emerald-50 text-emerald-700',
}

function timestamp(value: number | string) {
  return typeof value === 'number' ? (value < 10_000_000_000 ? value * 1000 : value) : Date.parse(value)
}

const statusName: Record<EvolveStageSkill['status'], string> = {
  validated: '静态校验通过',
  testing: '测试中',
  test_passed: '集成测试通过',
  test_failed: '集成测试失败',
  registered: '已注册',
  deleted: '已删除',
}

export default function StageSkillManagement() {
  const navigate = useNavigate()
  const [items, setItems] = useState<EvolveStageSkill[]>([])
  const [developments, setDevelopments] = useState<EvolveStageDevelopment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(1)

  const load = async () => {
    setLoading(true)
    try {
      const [result, drafts] = await Promise.all([api.evolve.listStageSkills(), api.evolve.listStageDevelopments()])
      setItems(result.items)
      setDevelopments(drafts.items)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '自定义 Stage 加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const groups = useMemo(() => {
    const result = new Map<string, EvolveStageSkill[]>()
    for (const item of items) {
      result.set(item.stageSkillId, [...(result.get(item.stageSkillId) ?? []), item])
    }
    return [...result.entries()].map(([id, versions]) => ({
      id,
      versions: versions.sort((a, b) => Number(b.version.slice(1)) - Number(a.version.slice(1))),
    }))
  }, [items])

  const drafts = developments.filter((record) => !items.some((item) => item.stageSkillId === record.stageSkillId))

  const removeDraft = async (record: EvolveStageDevelopment) => {
    if (!window.confirm(`删除未上传的开发记录“${record.displayName}”？`)) return
    try { await api.evolve.deleteStageDevelopment(record.stageSkillId); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败') }
  }

  const remove = async (implementation: EvolveStageSkill) => {
    if (!window.confirm('确认删除 ' + implementation.displayName + ' ' + implementation.version + '？已冻结任务仍可继续运行。')) return
    try {
      await api.evolve.deleteStageSkill(implementation.implementationId)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除失败')
    }
  }

  const rows = [
    ...groups.map((group) => ({ id: group.id, current: group.versions[0], draft: undefined as EvolveStageDevelopment | undefined })),
    ...drafts.map((draft) => ({ id: draft.stageSkillId, current: undefined as EvolveStageSkill | undefined, draft })),
  ].sort((a, b) => timestamp((b.current ?? b.draft!).updatedAt) - timestamp((a.current ?? a.draft!).updatedAt)).filter(({ current, draft }) => {
    const record = current ?? draft!
    const matches = [record.displayName, record.spaceName, record.ownerId, record.stageName].some((value) => value?.toLowerCase().includes(query.trim().toLowerCase()))
    return matches && (filter === 'all' || (filter === 'draft' ? !!draft : current?.status === filter || current?.integrationTestStatus === filter))
  })
  const visiblePage = Math.min(page, Math.max(1, Math.ceil(rows.length / skillListPageSize)))

  return (
    <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <PageTitle title="自定义 Stage" description="Stage 定义进化流程中的处理环节；自定义 Skill 实现其开放的处理逻辑。" action={<button onClick={() => navigate('/evolve/stage-skills/new')} className={primaryButton}><Icon name="plus" />接入自定义实现</button>} />
      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div className="flex rounded-lg bg-gray-100 p-1 text-xs">{[['all', '全部'], ['draft', '开发中'], ['registered', '已注册'], ['testing', '测试中'], ['test_failed', '测试失败']].map(([value, label]) => <button key={value} onClick={() => { setFilter(value); setPage(1) }} className={`rounded-md px-3 py-1.5 font-medium transition ${filter === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>{label}</button>)}</div>
          <input aria-label="搜索自定义 Stage" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="搜索名称、Stage 或 Owner ID" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-72" />
        </div>
        <div className="overflow-x-auto"><table className="w-full min-w-[1050px] table-fixed text-left text-sm">
          <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr><th className="w-[28%] px-5 py-3">自定义实现</th><th className="w-[120px] whitespace-nowrap px-5 py-3">用于 Stage</th><th className="w-[18%] px-5 py-3">Owner ID</th><th className="w-20 px-4 py-3">版本</th><th className="w-40 px-4 py-3">状态</th><th className="w-40 px-4 py-3">更新时间</th><th className="sticky right-0 z-10 w-[160px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th></tr></thead>
          <tbody className="divide-y divide-gray-100">{rows.slice((visiblePage - 1) * skillListPageSize, visiblePage * skillListPageSize).map(({ id, current, draft }) => {
            const record = current ?? draft!
            const open = () => navigate(current ? '/evolve/stage-skills/' + current.implementationId : '/evolve/stage-skills/new?developmentId=' + encodeURIComponent(id))
            return <tr key={id} className="group transition hover:bg-gray-50/70">
              <td className="px-5 py-4"><div className="flex items-center gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="code" /></span><div className="min-w-0"><button onClick={open} className="block max-w-full truncate text-left font-medium text-gray-900 hover:text-blue-600 hover:underline">{record.displayName}</button><p className="mt-0.5 text-xs text-gray-400">{modeName[record.mode]}</p><p className="mt-0.5 truncate text-xs text-gray-500" title={spaceLabel(record)}>{spaceLabel(record)}</p></div></div></td>
              <td className="px-5 py-4"><span className={`inline-flex whitespace-nowrap rounded-md border px-2 py-1 text-xs font-medium ${stageTagStyle[record.stage] ?? 'border-gray-200 bg-gray-50 text-gray-600'}`}>{record.stageName}</span></td>
              <td className="px-5 py-4"><span className="inline-block max-w-full truncate rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-600">{record.ownerId ?? '—'}</span></td>
              <td className="px-4 py-4">{current ? <span className="rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">{current.version}</span> : <span className="text-xs text-gray-400">—</span>}</td>
              <td className="px-4 py-4"><Status type={!current ? 'scheduled' : current.status === 'testing' ? 'running' : current.status === 'test_failed' ? 'waiting' : current.status === 'registered' || current.status === 'test_passed' ? 'done' : 'scheduled'}>{current ? statusName[current.status] : '开发中'}</Status>{current && <p className="mt-1 text-[11px] text-gray-400">{current.integrationTestStatus === 'test_passed' ? '最近测试通过' : current.integrationTestStatus === 'test_failed' ? '最近测试失败' : current.integrationTestStatus === 'testing' ? '测试中' : '尚未测试'}</p>}</td>
              <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500">{formatStepTime(record.updatedAt)}</td>
              <td className="sticky right-0 border-l border-gray-100 bg-white px-3 py-4 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] group-hover:bg-gray-50"><div className="flex items-center justify-center gap-2 whitespace-nowrap text-xs font-medium"><button className="inline-flex items-center justify-center rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-blue-700 transition hover:border-blue-200 hover:bg-blue-100" onClick={open}>{current ? '查看' : '继续开发'}</button>{current && <button className="text-blue-600 hover:text-blue-700" onClick={() => navigate('/evolve/stage-skills/new?upgrade=' + id)}>升级</button>}<button className="text-gray-500 hover:text-red-600" onClick={() => void (current ? remove(current) : removeDraft(draft!))}>删除</button></div></td>
            </tr>
          })}</tbody>
        </table></div>
        {!loading && !error && rows.length === 0 && <div className="px-5 py-16 text-center text-sm text-gray-400">{query || filter !== 'all' ? '没有匹配的自定义实现' : '还没有自定义实现，点击右上角开始接入。'}</div>}
        {loading && <div className="px-5 py-16 text-center text-sm text-gray-400">正在加载…</div>}
        <SkillListPagination total={rows.length} page={visiblePage} onChange={setPage} />
      </section>
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </div>
  )
}
