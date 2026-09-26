import { useEffect, useId, useRef, useState } from 'react'
import { api, type EvolveSkillAsset } from '../api/client'
import EvolveBotPicker from './EvolveBotPicker'
import { evolveBotOptionKey, type EvolveBotPickerOption } from './evolveBotIdentity'
import SpaceSelector from './SpaceSelector'

function registrationError(reason: unknown, fallback: string): string {
  const error = reason as { body?: unknown; status?: unknown }
  if (typeof error?.body === 'string') {
    try {
      const body = JSON.parse(error.body)
      if (typeof body.error === 'string' && typeof body.code === 'string'
        && /^(HOST_|BOT_|SKILL_)/.test(body.code)) return body.error
      if (typeof body.error === 'string' && Number(error.status) >= 400 && Number(error.status) < 500) return body.error
    } catch { /* Keep unstructured server responses out of the registration form. */ }
    return fallback
  }
  return reason instanceof Error && !/^API \d+:/.test(reason.message) ? reason.message : fallback
}

export default function SkillRegistrationDialog({ bots, botsLoading = false, onClose, onRegistered }: {
  bots: EvolveBotPickerOption[]
  botsLoading?: boolean
  onClose: () => void
  onRegistered: (asset: EvolveSkillAsset) => void | Promise<void>
}) {
  const titleId = useId()
  const submitting = useRef(false)
  const [botKey, setBotKey] = useState('')
  const [botQuery, setBotQuery] = useState('')
  const [skills, setSkills] = useState<Array<{ skillId: string; displayName: string }>>([])
  const [skillId, setSkillId] = useState('')
  const [spaceId, setSpaceId] = useState('')
  const [loading, setLoading] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [submitError, setSubmitError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const bot = bots.find(item => evolveBotOptionKey(item) === botKey)
  const botId = bot?.botId ?? ''
  const visibleBots = bots.filter(item => evolveBotOptionKey(item) === botKey ||
    [item.botName, item.botId, item.ownerId, item.env].some(value => value?.toLowerCase().includes(botQuery.trim().toLowerCase())))

  useEffect(() => {
    let active = true
    setSkills([]); setSkillId(''); setLoadError(''); setSubmitError('')
    setLoading(Boolean(botId))
    if (botId) void api.evolve.listAvailableLocalSkills(botId)
      .then(result => { if (active) setSkills(result.items) })
      .catch(reason => { if (active) setLoadError(registrationError(reason, 'Skill 列表读取失败，请稍后重试。')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [botId, botKey, attempt])

  const register = async () => {
    if (submitting.current || !botId || !skillId || loading || loadError) return
    submitting.current = true; setRegistering(true); setSubmitError('')
    try {
      const asset = await api.evolve.registerSkillAsset({ botId, skillId, ...(spaceId ? { spaceId } : {}) })
      await onRegistered(asset)
    } catch (reason) {
      setSubmitError(registrationError(reason, 'Skill 登记失败，请稍后重试。'))
    } finally { submitting.current = false; setRegistering(false) }
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => { if (!registering) onClose() }}>
    <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl" onClick={event => event.stopPropagation()}>
      <div className="flex items-start justify-between gap-4">
        <h2 id={titleId} className="text-xl font-semibold text-gray-950">登记 Bot 中已有 Skill</h2>
        <button disabled={registering} className="text-sm text-gray-400 disabled:opacity-40" onClick={onClose}>关闭</button>
      </div>
      <div className="mt-5 space-y-4">
        <SpaceSelector value={spaceId} onChange={setSpaceId} disabled={registering} />
        <section aria-label="所属 Bot">
          <p className="mb-1.5 text-xs font-medium text-gray-600">所属 Bot</p>
          <input aria-label="搜索 Bot" value={botQuery} disabled={registering} onChange={event => setBotQuery(event.target.value)} placeholder="搜索 Bot 名称、ID、Owner 或环境" className="mb-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500" />
          <EvolveBotPicker bots={visibleBots} value={botKey} disabled={botsLoading || registering} disableUnsupported={false} inlineOptions
            emptyText={botsLoading ? '正在加载 Bot…' : botQuery ? '没有匹配的 Bot' : '当前没有可用 Bot'}
            onChange={key => { if (key === botKey) return; setBotKey(key); setSkills([]); setSkillId(''); setLoadError(''); setSubmitError(''); setLoading(true) }} />
          {bot && <div className="mt-2 flex flex-wrap gap-2 text-xs text-gray-600">
            <span className="rounded bg-blue-50 px-2 py-1 text-blue-700">{bot.env || '环境未知'}</span>
            <span className="rounded bg-gray-100 px-2 py-1">{bot.hasServiceBot ? '已有服务 Bot' : bot.botType === 'service' ? '服务型 Bot' : '普通 Bot'}</span>
            {bot.accessType === 'collaborator' && <span className="rounded bg-violet-50 px-2 py-1 text-violet-700">协作 Bot</span>}
          </div>}
        </section>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-gray-600">Bot 中自己上传的 Skill</span>
          <select value={skillId} onChange={event => { setSkillId(event.target.value); setSubmitError('') }} disabled={!botId || loading || registering || Boolean(loadError)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
            <option value="">{loading ? '正在读取 Skill…' : '请选择 Skill'}</option>
            {skills.map(skill => <option key={skill.skillId} value={skill.skillId}>{skill.displayName}</option>)}
          </select>
        </label>
        {loading && <p role="status" className="text-xs text-gray-500">正在读取 Bot 的 Skill…</p>}
        {botId && !loading && !loadError && !skills.length && <p className="text-xs text-gray-500">该 Bot 暂无可登记的本地 Skill。</p>}
        {loadError && <div role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}<button disabled={registering} onClick={() => setAttempt(value => value + 1)} className="ml-2 underline">重试读取 Skill</button></div>}
        {submitError && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</p>}
      </div>
      <div className="mt-6 flex justify-end gap-2">
        <button disabled={registering} onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm disabled:opacity-40">取消</button>
        <button disabled={registering || loading || Boolean(loadError) || !botId || !skillId} onClick={() => void register()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{registering ? '登记中…' : '登记'}</button>
      </div>
    </div>
  </div>
}
