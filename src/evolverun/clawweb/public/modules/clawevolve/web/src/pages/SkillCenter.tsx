import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveSkillAsset } from '../api/client'
import { useClientUser } from '../hooks/useClientUser'
import type { TCLogBot } from '../types'

export default function SkillCenter() {
  const navigate = useNavigate()
  const { user } = useClientUser()
  const [assets, setAssets] = useState<EvolveSkillAsset[]>([])
  const [bots, setBots] = useState<TCLogBot[]>([])
  const [botId, setBotId] = useState('')
  const [skills, setSkills] = useState<Array<{ skillId: string; displayName: string }>>([])
  const [skillId, setSkillId] = useState('')
  const [registering, setRegistering] = useState(false)
  const [showRegister, setShowRegister] = useState(false)
  const [error, setError] = useState('')

  const loadAssets = async () => {
    const result = await api.evolve.listSkillAssets()
    setAssets(result.items)
  }

  useEffect(() => {
    void Promise.all([
      loadAssets(),
      user?.userId ? api.tclog.bots({ ownerId: user.userId, status: 'all' }) : Promise.resolve({ bots: [] as TCLogBot[] }),
    ]).then(([, result]) => setBots(result.bots)).catch((reason) => setError(reason instanceof Error ? reason.message : '技能中心加载失败'))
  }, [user?.userId])

  useEffect(() => {
    if (!botId) { setSkills([]); setSkillId(''); return }
    void api.evolve.listAvailableLocalSkills(botId)
      .then((result) => { setSkills(result.items); setSkillId('') })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Bot Skill 加载失败'))
  }, [botId])

  const register = async () => {
    if (!botId || !skillId) { setError('请选择 Bot 和要登记的 Skill'); return }
    setRegistering(true); setError('')
    try {
      const asset = await api.evolve.registerSkillAsset({ botId, skillId })
      await loadAssets()
      setShowRegister(false)
      navigate(`/evolve/skills/${encodeURIComponent(asset.assetId)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 登记失败')
    } finally { setRegistering(false) }
  }

  return <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div><p className="text-sm font-medium text-blue-600">Evolve · Skill 资产</p><h1 className="mt-1 text-2xl font-semibold text-gray-950">技能中心</h1><p className="mt-1.5 text-sm text-gray-500">登记 Bot 中自己上传的 Skill，查看冻结版本，并从同一资产发起 Skill 自进化。</p></div>
      <button onClick={() => setShowRegister(true)} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700">登记 Skill</button>
    </div>
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_120px_110px] gap-4 border-b border-gray-100 bg-gray-50 px-5 py-3 text-xs font-medium text-gray-500"><span>Skill</span><span>所属 Bot</span><span>当前版本</span><span className="text-right">操作</span></div>
      {assets.map((asset) => <div key={asset.assetId} className="grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_120px_110px] items-center gap-4 border-b border-gray-100 px-5 py-4 last:border-b-0"><div className="min-w-0"><p className="truncate text-sm font-semibold text-gray-900">{asset.name}</p><p className="mt-1 truncate font-mono text-[10px] text-gray-400">{asset.skillId}</p></div><span className="truncate text-sm text-gray-600">{asset.botId}</span><span className="text-sm text-gray-700">{asset.currentVersion}</span><button onClick={() => navigate(`/evolve/skills/${encodeURIComponent(asset.assetId)}`)} className="text-right text-xs font-medium text-blue-600">查看</button></div>)}
      {assets.length === 0 && <div className="py-16 text-center text-sm text-gray-400">还没有登记 Skill。</div>}
    </section>
    {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    {showRegister && <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => setShowRegister(false)}><div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-xl" onClick={(event) => event.stopPropagation()}><div className="flex items-start justify-between"><div><h2 className="text-xl font-semibold text-gray-950">登记 Bot 中已有 Skill</h2><p className="mt-1 text-sm text-gray-500">平台从 OCB 读取完整 Skill，并保存登记时的 v1 冻结版本。</p></div><button className="text-sm text-gray-400" onClick={() => setShowRegister(false)}>关闭</button></div><div className="mt-5 space-y-4"><label><span className="mb-1.5 block text-xs font-medium text-gray-600">所属 Bot</span><select value={botId} onChange={(event) => setBotId(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">请选择 Bot</option>{bots.map((bot) => <option key={`${bot.botId}:${bot.env ?? ''}`} value={bot.botId}>{bot.botName || bot.botId}</option>)}</select></label><label><span className="mb-1.5 block text-xs font-medium text-gray-600">Bot 中自己上传的 Skill</span><select value={skillId} onChange={(event) => setSkillId(event.target.value)} disabled={!botId} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50"><option value="">请选择 Skill</option>{skills.map((skill) => <option key={skill.skillId} value={skill.skillId}>{skill.displayName}</option>)}</select></label></div><div className="mt-6 flex justify-end gap-2"><button onClick={() => setShowRegister(false)} className="rounded-lg border border-gray-200 px-4 py-2 text-sm">取消</button><button disabled={registering || !botId || !skillId} onClick={() => void register()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{registering ? '登记中…' : '登记'}</button></div></div></div>}
  </div>
}
