import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveSkillAsset } from '../api/client'
import { useClientUser } from '../hooks/useClientUser'
import type { DirectoryBot } from '../types'
import { Icon, PageTitle, Status } from './evolve/common'
import { formatStepTime, primaryButton } from './evolve/helpers'
import SkillTaskLaunchDialog, { type SkillTaskAction } from '../components/SkillTaskLaunchDialog'
import { spaceLabel } from '../components/SpaceSelector'
import SkillRegistrationDialog from '../components/SkillRegistrationDialog'
import SkillListPagination, { skillListPageSize } from '../components/SkillListPagination'

export default function SkillCenter() {
  const navigate = useNavigate()
  const { user } = useClientUser()
  const [launch, setLaunch] = useState<{ asset: EvolveSkillAsset; action: SkillTaskAction } | null>(null)
  const [assets, setAssets] = useState<EvolveSkillAsset[]>([])
  const [bots, setBots] = useState<DirectoryBot[]>([])
  const [showRegister, setShowRegister] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)

  const loadAssets = async () => {
    const result = await api.evolve.listSkillAssets()
    setAssets(result.items)
  }

  useEffect(() => {
    void Promise.all([
      loadAssets(),
      user?.userId ? api.bots.list({ ownerId: user.userId, status: 'all' }) : Promise.resolve({ bots: [] as DirectoryBot[] }),
    ]).then(([, result]) => setBots(result.bots)).catch((reason) => setError(reason instanceof Error ? reason.message : '技能中心加载失败')).finally(() => setLoading(false))
  }, [user?.userId])

  const filtered = assets.filter((asset) => [asset.name, asset.description, asset.spaceName, asset.ownerId, asset.botId, bots.find((bot) => bot.botId === asset.botId)?.botName].some((value) => value?.toLowerCase().includes(query.trim().toLowerCase())))
  const visiblePage = Math.min(page, Math.max(1, Math.ceil(filtered.length / skillListPageSize)))
  return <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
    <PageTitle title="技能管理" description="登记技能，查看版本内容，并发起诊断、加固或优化任务。" action={<button onClick={() => setShowRegister(true)} className={primaryButton}><Icon name="plus" />登记 Skill</button>} />
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
        <span className="rounded-md bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700">全部技能</span>
        <input aria-label="搜索技能" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1) }} placeholder="搜索技能名称、Owner ID 或 Bot ID" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80" />
      </div>
      <div className="overflow-x-auto"><table className="w-full min-w-[1140px] table-fixed text-left text-sm">
        <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr>
          <th className="w-[28%] px-5 py-3">技能名称</th><th className="w-[17%] px-4 py-3">Owner ID</th><th className="w-[21%] px-4 py-3">所属 Bot</th><th className="w-20 px-4 py-3">版本</th><th className="w-24 px-4 py-3">状态</th><th className="w-40 px-4 py-3">更新时间</th><th className="sticky right-0 z-10 w-[300px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th>
        </tr></thead>
        <tbody className="divide-y divide-gray-100">{filtered.slice((visiblePage - 1) * skillListPageSize, visiblePage * skillListPageSize).map((asset) => <tr key={asset.assetId} className="group transition hover:bg-gray-50/70">
          <td className="px-5 py-4"><div className="flex items-center gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="spark" /></span><div className="min-w-0"><button onClick={() => navigate(`/evolve/skills/${encodeURIComponent(asset.assetId)}`)} className="block max-w-full truncate text-left font-medium text-gray-900 hover:text-blue-600 hover:underline">{asset.name}</button><p className="mt-0.5 truncate text-xs text-gray-500" title={spaceLabel(asset)}>{spaceLabel(asset)}</p>{asset.description && <p title={asset.description} className="mt-0.5 truncate text-xs text-gray-400">{asset.description}</p>}</div></div></td>
          <td className="px-4 py-4"><span title={asset.ownerId ?? undefined} className="inline-block max-w-full truncate rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-600">{asset.ownerId ?? '—'}</span></td>
          <td className="px-4 py-4">{bots.find((bot) => bot.botId === asset.botId)?.botName && <p className="mb-0.5 truncate text-xs font-medium text-gray-700">{bots.find((bot) => bot.botId === asset.botId)?.botName}</p>}<p className="truncate font-mono text-[11px] text-gray-500" title={asset.botId}>{asset.botId}</p></td>
          <td className="px-4 py-4"><span className="rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">{asset.currentVersion}</span></td>
          <td className="px-4 py-4"><Status type="done">已登记</Status></td>
          <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500">{formatStepTime(asset.updatedAt)}</td>
          <td className="sticky right-0 border-l border-gray-100 bg-white px-3 py-4 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] group-hover:bg-gray-50"><div className="flex items-center justify-center gap-2 whitespace-nowrap"><button className="inline-flex items-center justify-center rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:border-amber-300 hover:bg-amber-100" onClick={() => setLaunch({ asset, action: 'diagnose' })}>诊断</button><button className="inline-flex items-center justify-center rounded-md border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 transition hover:border-violet-300 hover:bg-violet-100" onClick={() => setLaunch({ asset, action: 'hardening' })}>加固</button><button className="inline-flex items-center justify-center rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:border-emerald-300 hover:bg-emerald-100" onClick={() => setLaunch({ asset, action: 'optimize' })}>优化</button><button onClick={() => navigate(`/evolve/skills/${encodeURIComponent(asset.assetId)}`)} className="inline-flex items-center justify-center rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-50">查看</button></div></td>
        </tr>)}</tbody>
      </table></div>
      {loading && <div className="py-16 text-center text-sm text-gray-400">正在加载…</div>}
      {!loading && !error && filtered.length === 0 && <div className="py-16 text-center text-sm text-gray-400">{query ? '没有匹配的技能' : '还没有登记 Skill。'}</div>}
      <SkillListPagination total={filtered.length} page={visiblePage} onChange={setPage} />
    </section>
    {error && !showRegister && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    {launch && <SkillTaskLaunchDialog key={`${launch.asset.assetId}:${launch.action}`} asset={launch.asset} action={launch.action} returnTo="/evolve/skills" onClose={() => setLaunch(null)} />}
    {showRegister && <SkillRegistrationDialog bots={bots} botsLoading={loading} onClose={() => setShowRegister(false)} onRegistered={async asset => {
      await loadAssets()
      setShowRegister(false)
      navigate(`/evolve/skills/${encodeURIComponent(asset.assetId)}`)
    }} />}
  </div>
}
