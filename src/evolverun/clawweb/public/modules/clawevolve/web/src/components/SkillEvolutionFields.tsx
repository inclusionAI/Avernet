import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type EvolveSkillAsset,
  type EvolveStageCatalog,
  type EvolveStageMode,
  type EvolveStageSkill,
} from '../api/client'

export type StageExtensionDraft = Partial<Record<
  'diagnose' | 'plan' | 'optimize',
  Partial<Record<EvolveStageMode, { enabled: boolean; implementationId: string }>>
>>

export type StageSelectionDraft = Record<'diagnose' | 'plan' | 'optimize', boolean>

const modeCopy: Record<EvolveStageMode, { name: string; description: string }> = {
  preprocess: { name: '前置处理', description: '先运行你的 Skill，再继续平台原有处理' },
  postprocess: { name: '后置处理', description: '平台原有处理完成后，再运行你的 Skill' },
  replace: { name: '整体替换', description: '由你的 Skill 完成这一整个 Stage' },
}

export default function SkillEvolutionFields({
  botId,
  includeTargetSkill,
  assetId,
  onAssetIdChange,
  extensions,
  onExtensionsChange,
  stageSelection,
  onStageSelectionChange,
  fullTask,
}: {
  botId: string
  includeTargetSkill: boolean
  assetId: string
  onAssetIdChange: (value: string) => void
  extensions: StageExtensionDraft
  onExtensionsChange: (value: StageExtensionDraft) => void
  stageSelection: StageSelectionDraft
  onStageSelectionChange: (value: StageSelectionDraft) => void
  fullTask: boolean
}) {
  const [assets, setAssets] = useState<EvolveSkillAsset[]>([])
  const [catalog, setCatalog] = useState<EvolveStageCatalog | null>(null)
  const [implementations, setImplementations] = useState<EvolveStageSkill[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([
      includeTargetSkill ? api.evolve.listSkillAssets() : Promise.resolve({ items: [] }),
      api.evolve.stageCatalog(),
      api.evolve.listStageSkills(),
    ]).then(([assetResult, stageCatalog, implementationResult]) => {
      if (!active) return
      const botAssets = assetResult.items.filter((item) => item.botId === botId)
      setAssets(botAssets)
      setCatalog(stageCatalog)
      setImplementations(implementationResult.items.filter((item) => item.status === 'registered'))
      if (botId && assetId && !botAssets.some((item) => item.assetId === assetId)) onAssetIdChange('')
      setError('')
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Skill 进化配置加载失败')
    })
    return () => { active = false }
  }, [botId, includeTargetSkill])

  const stageSkills = useMemo(() => {
    const result = new Map<string, EvolveStageSkill[]>()
    for (const item of implementations) {
      const key = `${item.stage}:${item.mode}`
      result.set(key, [...(result.get(key) ?? []), item])
    }
    return result
  }, [implementations])

  const updateBinding = (
    stage: 'diagnose' | 'plan' | 'optimize',
    mode: EvolveStageMode,
    value: { enabled: boolean; implementationId: string },
  ) => {
    onExtensionsChange({
      ...extensions,
      [stage]: {
        ...extensions[stage],
        [mode]: value,
      },
    })
  }

  const disableExtensions = (
    value: StageExtensionDraft,
    stage: 'diagnose' | 'plan' | 'optimize',
  ): StageExtensionDraft => ({
    ...value,
    [stage]: Object.fromEntries(Object.entries(value[stage] ?? {}).map(([mode, binding]) => [
      mode,
      { ...binding, enabled: false },
    ])) as StageExtensionDraft[typeof stage],
  })

  const updateStage = (stage: 'diagnose' | 'plan' | 'optimize', enabled: boolean) => {
    let nextSelection = { ...stageSelection, [stage]: enabled }
    let nextExtensions = extensions
    if (!enabled) nextExtensions = disableExtensions(nextExtensions, stage)
    if (stage === 'plan' && !enabled) {
      nextSelection = { ...nextSelection, optimize: false }
      nextExtensions = disableExtensions(nextExtensions, 'optimize')
    }
    if (stage === 'optimize' && enabled) nextSelection = { ...nextSelection, plan: true }
    if (!Object.values(nextSelection).some(Boolean)) return
    onStageSelectionChange(nextSelection)
    onExtensionsChange(nextExtensions)
  }

  return (
    <section className="border-t border-gray-100 pt-6">
      {includeTargetSkill && <>
        <h2 className="text-sm font-semibold text-gray-900">待进化 Skill</h2>
        <p className="mt-1 text-xs leading-5 text-gray-500">
          选择已在技能中心登记的 Skill。任务开始时平台会从 OCB 读取最新内容，并为本次任务创建独立候选版本。
        </p>
        <div className="mt-3 flex items-center gap-3">
          <select
            value={assetId}
            onChange={(event) => onAssetIdChange(event.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500"
          >
            <option value="">{botId ? '请选择待进化 Skill' : '请先选择目标 Bot'}</option>
            {assets.map((asset) => (
              <option key={asset.assetId} value={asset.assetId}>
                {asset.name} · {asset.currentVersion}
              </option>
            ))}
          </select>
          <Link to="/evolve/skills" className="shrink-0 text-xs font-medium text-blue-600 hover:text-blue-700">
            登记新的 Skill ↗
          </Link>
        </div>
        {botId && assets.length === 0 && !error && (
          <p className="mt-2 text-xs text-amber-700">该 Bot 暂无已登记 Skill，请先到技能中心登记。</p>
        )}
      </>}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      <div className={`${includeTargetSkill ? 'mt-6' : ''} flex items-center justify-between gap-4`}>
        <div>
          <h2 className="text-sm font-semibold text-gray-900">自定义 Stage 处理</h2>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            默认沿用平台处理；需要时可在指定位置启用已注册的 Skill 版本。本次选择会随任务冻结。
          </p>
        </div>
        <Link to="/evolve/stage-skills" className="shrink-0 text-xs font-medium text-blue-600 hover:text-blue-700">
          管理 Stage Skill ↗
        </Link>
      </div>
      <div className="mt-3 space-y-3">
        {catalog?.stages.filter((stage) => fullTask || stage.stage !== 'optimize').map((stage) => (
          <div key={stage.stage} className="rounded-xl border border-gray-200 bg-gray-50/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-gray-900">{stage.name}</p>
                <p className="mt-1 text-xs leading-5 text-gray-500">{stage.description}</p>
              </div>
              <label className="flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={stageSelection[stage.stage as keyof StageSelectionDraft]}
                  disabled={!fullTask && stage.stage === 'diagnose'}
                  onChange={(event) => updateStage(
                    stage.stage as keyof StageSelectionDraft,
                    event.target.checked,
                  )}
                  className="h-4 w-4 rounded border-gray-300 text-blue-600"
                />
                执行此 Stage
              </label>
            </div>
            <div className="mt-3 grid gap-2 lg:grid-cols-3">
              {stage.extensionModes.map((mode) => {
                const stageKey = stage.stage as 'diagnose' | 'plan' | 'optimize'
                const binding = extensions[stageKey]?.[mode]
                const options = stageSkills.get(`${stage.stage}:${mode}`) ?? []
                const selectedId = binding?.implementationId || options[0]?.implementationId || ''
                const isStageEnabled = stageSelection[stageKey]
                return (
                  <div key={mode} className="rounded-lg border border-gray-200 bg-white p-3">
                    <label className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={binding?.enabled === true}
                        disabled={!isStageEnabled || options.length === 0}
                        onChange={(event) => updateBinding(stageKey, mode, {
                          enabled: event.target.checked,
                          implementationId: selectedId,
                        })}
                        className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
                      />
                      <span>
                        <span className="block text-xs font-semibold text-gray-800">{modeCopy[mode].name}</span>
                        <span className="mt-0.5 block text-[10px] leading-4 text-gray-400">
                          {modeCopy[mode].description}
                        </span>
                      </span>
                    </label>
                    {options.length > 0 ? (
                      <select
                        value={selectedId}
                        disabled={!isStageEnabled || !binding?.enabled}
                        onChange={(event) => updateBinding(stageKey, mode, {
                          enabled: true,
                          implementationId: event.target.value,
                        })}
                        className="mt-2 w-full rounded-md border border-gray-200 bg-white px-2 py-1.5 text-xs outline-none disabled:bg-gray-50 disabled:text-gray-400"
                      >
                        {options.map((item) => (
                          <option key={item.implementationId} value={item.implementationId}>
                            {item.displayName} · {item.version}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <p className="mt-2 text-[10px] text-gray-400">暂无可用实现</p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
