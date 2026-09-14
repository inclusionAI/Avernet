import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveSkillAsset, type EvolveSkillTaskDefaults } from '../api/client'
import { createRequestId } from '../utils/request-id'

export type SkillTaskAction = 'diagnose' | 'optimize'
const actionName = { diagnose: '诊断', optimize: '优化' } as const
const defaultModel = 'GLM-5.2'

function dateValue(offsetDays = 0): string {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export default function SkillTaskLaunchDialog({ asset, action, onClose }: {
  asset: EvolveSkillAsset
  action: SkillTaskAction
  onClose: () => void
}) {
  const navigate = useNavigate()
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const submitting = useRef(false)
  const [requestId] = useState(() => createRequestId('skill-task'))
  const [dates] = useState(() => ({ startDate: dateValue(-3), endDate: dateValue() }))
  const [defaults, setDefaults] = useState<EvolveSkillTaskDefaults | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setDefaults(null)
    setError('')
    void api.evolve.getSkillTaskDefaults(asset.assetId).then((result) => {
      if (!active) return
      if (result.assetId !== asset.assetId || !result.botId || !result.userId) {
        throw new Error('任务默认配置与当前 Skill 不匹配，请刷新重试')
      }
      setDefaults(result)
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : '任务默认配置加载失败')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [asset.assetId, attempt])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    return () => { previous?.focus() }
  }, [])

  const preset = defaults?.[action]
  const unavailableReason = action === 'optimize' && defaults
    ? defaults.optimize.unavailableReason || (
      defaults.optimize.stageExtensions?.diagnose?.preprocess?.enabled === true
        && defaults.optimize.stageExtensions.diagnose.preprocess.implementationId?.trim()
        ? '' : '当前没有有权使用且已通过集成测试的 97 加固 Stage，暂不可启动优化。'
    ) : ''
  const canLaunch = Boolean(defaults && preset?.goal.trim() && !unavailableReason && !loading && !busy)

  const confirm = async () => {
    if (!canLaunch || !defaults || !preset || submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      const common = {
        taskName: `${actionName[action]} · ${asset.name}`.slice(0, 128),
        userId: defaults.userId,
        botId: defaults.botId,
        targetSkillAssetId: asset.assetId,
        goal: preset.goal,
        diagnoseIntent: preset.goal,
        judgeBackend: 'subagent' as const,
        model: defaultModel,
        maxSessions: 10,
        sessionSource: 'local' as const,
        ...dates,
        runtimeMaintenance: false,
      }
      const result = await api.evolve.createTask(action === 'diagnose'
        ? { ...common, taskType: 'diagnose', stageSelection: { diagnose: true, plan: true, optimize: false } }
        : { ...common, taskType: 'full', inputMode: 'diagnose_goal', maxRounds: 3,
            stageSelection: { diagnose: true, plan: true, optimize: true },
            stageExtensions: defaults.optimize.stageExtensions! }, requestId)
      onClose()
      navigate(`/evolve/runs/${encodeURIComponent(result.task_id)}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务启动失败')
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  const advanced = () => {
    if (!defaults || busy) return
    const query = new URLSearchParams({ target: 'skill',
      type: action === 'diagnose' ? 'diagnose' : 'full', assetId: asset.assetId,
      skillAction: action })
    onClose()
    navigate(`/evolve/new?${query}`)
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => { if (!busy) onClose() }}>
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
      className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-2xl bg-white p-6 shadow-xl outline-none"
      onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
        if (event.key === 'Escape' && !busy) { event.stopPropagation(); onClose() }
        if (event.key !== 'Tab') return
        const buttons = panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
        const first = buttons?.[0]
        const last = buttons?.[buttons.length - 1]
        if (!first || !last) { event.preventDefault(); return }
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }}>
      <h2 id={titleId} className="text-xl font-semibold text-gray-950">确认{actionName[action]} Skill</h2>
      <p className="mt-3 text-sm font-medium text-gray-800">{asset.name}</p>
      {loading && <p role="status" className="mt-4 text-sm text-gray-500">正在加载任务默认配置…</p>}
      {defaults && <>
        <dl className="mt-4 space-y-2 text-sm text-gray-600">
          <div><dt className="inline">所属 Bot：</dt><dd className="inline font-mono">{defaults.botId}</dd></div>
          <div><dt className="inline">Owner ID：</dt><dd className="inline font-mono">{defaults.userId}</dd></div>
          <div><dt className="inline">模型：</dt><dd className="inline">{defaultModel}（平台默认，可在高级表单修改）</dd></div>
        </dl>
        <p className="mt-4 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm leading-6 text-gray-700">{preset?.goal}</p>
        <p className="mt-3 text-xs leading-5 text-gray-500">{action === 'optimize'
          ? '运行完整诊断、规划和优化流程；启动前须绑定有权使用且通过集成测试的 97 加固诊断前置 Stage，最多优化 3 轮。'
          : '诊断当前登记的 Skill，并生成后续规划，不启动优化轮次。'}本次不会清理历史会话或重启 Gateway。</p>
        {unavailableReason && <p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">不可启动优化：{unavailableReason}</p>}
        {!preset?.goal.trim() && <p role="alert" className="mt-3 text-sm text-red-700">任务默认目标为空，暂不可启动。</p>}
      </>}
      {error && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}
        {!defaults && <button type="button" className="ml-2 underline" onClick={() => setAttempt((value) => value + 1)}>重试</button>}
      </div>}
      <div className="mt-6 flex flex-wrap justify-end gap-2">
        <button type="button" disabled={!defaults || busy} onClick={advanced} className="mr-auto text-sm text-blue-600 disabled:opacity-40">自定义 / 高级</button>
        <button type="button" disabled={busy} onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm disabled:opacity-40">取消</button>
        <button type="button" disabled={!canLaunch} title={unavailableReason || undefined} onClick={() => void confirm()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{busy ? '正在启动…' : `确认${actionName[action]}`}</button>
      </div>
    </div>
  </div>
}
