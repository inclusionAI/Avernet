import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type EvolveSkillAsset,
  type EvolveStageCatalog,
  type EvolveStageMode,
  type EvolveStageSkill,
} from '../api/client'

type ConfigurableStage = 'diagnose' | 'hardening' | 'plan' | 'optimize'

export type StageExtensionDraft = Partial<Record<
  ConfigurableStage,
  Partial<Record<EvolveStageMode, { enabled: boolean; implementationId: string }>>
>>

export type StageSelectionDraft = Record<ConfigurableStage, boolean>

const modeCopy: Record<EvolveStageMode, { name: string; description: string }> = {
  preprocess: { name: '前置处理', description: '先运行你的 Skill，再继续平台原有处理' },
  postprocess: { name: '后置处理', description: '平台原有处理完成后，再运行你的 Skill' },
  replace: { name: '整体替换', description: '由你的 Skill 完成这一整个 Stage' },
}

export default function SkillEvolutionFields({
  botId,
  includeTargetSkill,
  targetLocked = false,
  assetId,
  onAssetIdChange,
  extensions,
  onExtensionsChange,
  stageSelection,
  onStageSelectionChange,
  fullTask,
  flowKey,
  inputMode,
  hasGoal,
  section = 'all',
}: {
  botId: string
  includeTargetSkill: boolean
  targetLocked?: boolean
  assetId: string
  onAssetIdChange: (value: string) => void
  extensions: StageExtensionDraft
  onExtensionsChange: (value: StageExtensionDraft) => void
  stageSelection: StageSelectionDraft
  onStageSelectionChange: (value: StageSelectionDraft) => void
  fullTask: boolean
  flowKey: 'bot_evolution' | 'skill_evolution' | 'skill_hardening'
  inputMode: 'diagnose_goal' | 'direct_goal'
  hasGoal: boolean
  section?: 'all' | 'target' | 'extensions'
}) {
  const [assets, setAssets] = useState<EvolveSkillAsset[]>([])
  const [catalog, setCatalog] = useState<EvolveStageCatalog | null>(null)
  const [implementations, setImplementations] = useState<EvolveStageSkill[]>([])
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [customizing, setCustomizing] = useState<Partial<Record<ConfigurableStage, boolean>>>({})
  const [pickerStage, setPickerStage] = useState<ConfigurableStage | null>(null)
  const [stageSearch, setStageSearch] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([
      includeTargetSkill && section !== 'extensions' ? api.evolve.listSkillAssets() : Promise.resolve({ items: [] }),
      section !== 'target' ? api.evolve.stageCatalog({
        taskType: flowKey === 'skill_hardening' ? 'hardening' : fullTask ? 'full' : 'diagnose',
        inputMode,
        hasGoal,
      }) : Promise.resolve(null),
      section !== 'target' ? api.evolve.listStageSkills() : Promise.resolve({ items: [] }),
    ]).then(([assetResult, stageCatalog, implementationResult]) => {
      if (!active) return
      const botAssets = assetResult.items.filter((item) => item.botId === botId)
      setAssets(botAssets)
      if (stageCatalog) setCatalog(stageCatalog)
      setImplementations(implementationResult.items.filter((item) => item.status === 'registered'))
      if (includeTargetSkill && !targetLocked && section !== 'extensions'
        && botId && assetId && !botAssets.some((item) => item.assetId === assetId)) onAssetIdChange('')
      setError('')
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Skill 进化配置加载失败')
    })
    return () => { active = false }
  }, [botId, includeTargetSkill, targetLocked, section, fullTask, flowKey, inputMode, hasGoal])

  const flowDescription = catalog?.flows.find((flow) => flow.key === flowKey)
  const stagePolicy = (stage: ConfigurableStage) =>
    flowDescription?.stages.find((item) => item.key === stage)

  const stageSkills = useMemo(() => {
    const result = new Map<string, EvolveStageSkill[]>()
    for (const item of implementations) {
      const key = item.stage
      result.set(key, [...(result.get(key) ?? []), item])
    }
    return result
  }, [implementations])

  const updateBinding = (
    stage: ConfigurableStage,
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

  const activeBindings = (stage: ConfigurableStage) =>
    Object.entries(extensions[stage] ?? {})
      .filter((entry): entry is [EvolveStageMode, { enabled: boolean; implementationId: string }] => entry[1]?.enabled === true)

  const chooseImplementation = (
    stage: ConfigurableStage,
    implementation: EvolveStageSkill,
  ) => {
    const existing = activeBindings(stage)
    if (implementation.mode === 'replace' && existing.some(([mode]) => mode !== 'replace')) return
    if (implementation.mode !== 'replace' && existing.some(([mode]) => mode === 'replace')) return
    updateBinding(stage, implementation.mode, {
      enabled: true,
      implementationId: implementation.implementationId,
    })
    setCustomizing((value) => ({ ...value, [stage]: true }))
    setPickerStage(null)
  }

  const removeBinding = (stage: ConfigurableStage, mode: EvolveStageMode) => {
    const binding = extensions[stage]?.[mode]
    updateBinding(stage, mode, { enabled: false, implementationId: binding?.implementationId ?? '' })
  }

  const toggleCustom = (stage: ConfigurableStage, enabled: boolean) => {
    setCustomizing((value) => ({ ...value, [stage]: enabled }))
    if (enabled) setPickerStage(stage)
    else {
      onExtensionsChange(disableExtensions(extensions, stage))
      if (pickerStage === stage) setPickerStage(null)
    }
  }

  const disableExtensions = (
    value: StageExtensionDraft,
    stage: ConfigurableStage,
  ): StageExtensionDraft => ({
    ...value,
    [stage]: Object.fromEntries(Object.entries(value[stage] ?? {}).map(([mode, binding]) => [
      mode,
      { ...binding, enabled: false },
    ])) as StageExtensionDraft[typeof stage],
  })

  const updateStage = (stage: ConfigurableStage, enabled: boolean) => {
    const policy = stagePolicy(stage)
    if (!policy?.canDisable && enabled !== policy?.enabled) return
    const nextSelection = { ...stageSelection, [stage]: enabled }
    let nextExtensions = extensions
    if (!enabled) {
      nextExtensions = disableExtensions(nextExtensions, stage)
      setCustomizing((value) => ({ ...value, [stage]: false }))
      if (pickerStage === stage) setPickerStage(null)
    }
    if (!Object.values(nextSelection).some(Boolean)) return
    onStageSelectionChange(nextSelection)
    onExtensionsChange(nextExtensions)
  }

  const selectedCount = Object.values(extensions).reduce((count, stageValue) =>
    count + Object.values(stageValue ?? {}).filter((binding) => binding?.enabled).length, 0)
  const targetLabel = flowKey === 'skill_hardening' ? '待加固 Skill' : '待进化 Skill'
  const visibleStages = catalog?.stages.filter((stage) => {
    if (flowKey === 'skill_hardening') return stage.stage === 'hardening'
    if (stage.stage === 'hardening') return false
    return fullTask || stage.stage !== 'optimize'
  })

  return <>
    {section !== 'extensions' && includeTargetSkill && <section className="border-t border-gray-100 pt-6">
      <h2 className="text-sm font-semibold text-gray-900">{targetLabel}</h2>
      <p className="mt-1 text-xs leading-5 text-gray-500">选择已在技能中心登记的 Skill。任务开始时平台会从 OCB 读取最新内容，并为本次任务创建独立候选版本。</p>
      <div className="mt-3 flex items-center gap-3">
        <select aria-label={targetLabel} disabled={targetLocked} value={assetId} onChange={(event) => onAssetIdChange(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500 disabled:bg-gray-50">
          <option value="">{botId ? `请选择${targetLabel}` : '请先选择目标 Bot'}</option>
          {assets.map((asset) => <option key={asset.assetId} value={asset.assetId}>{asset.name} · {asset.currentVersion}</option>)}
        </select>
        <Link to="/evolve/skills" className="shrink-0 text-xs font-medium text-blue-600 hover:text-blue-700">登记新的 Skill ↗</Link>
      </div>
      {botId && assets.length === 0 && !error && <p className="mt-2 text-xs text-amber-700">该 Bot 暂无已登记 Skill，请先到技能中心登记。</p>}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </section>}

    {section !== 'target' && <section className="border-t border-gray-100 pt-6">
      <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-start justify-between gap-4 text-left">
        <span><span className="block text-sm font-semibold text-gray-900">自定义 Stage 处理</span><span className="mt-1 block text-xs leading-5 text-gray-500">默认使用平台处理；需要时可为已启用的 Stage 接入已注册实现。</span></span>
        <span className="shrink-0 text-xs font-medium text-gray-500">{selectedCount ? `已选择 ${selectedCount} 个实现 · ` : ''}{expanded ? '收起' : '展开'}</span>
      </button>
      {!expanded && selectedCount > 0 && <p className="mt-2 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700">已配置的自定义实现会随本次任务冻结。</p>}
      {expanded && <div className="mt-4 space-y-3">
        <div className="flex justify-end"><Link to="/evolve/stage-skills" className="text-xs font-medium text-blue-600 hover:text-blue-700">管理自定义 Stage ↗</Link></div>
        {visibleStages?.map((stage) => {
          const stageKey = stage.stage as ConfigurableStage
          const selected = activeBindings(stageKey)
          const options = (stageSkills.get(stage.stage) ?? [])
            .filter((item) => stage.extensionModes.includes(item.mode))
            .filter((item) => flowKey === 'bot_evolution' || item.integrationTestStatus === 'test_passed')
          const customEnabled = customizing[stageKey] === true || selected.length > 0
          const hasReplace = selected.some(([mode]) => mode === 'replace')
          const selectedModes = new Set(selected.map(([mode]) => mode))
          const selectable = options
            .filter((item) => !selectedModes.has(item.mode))
            .filter((item) => hasReplace ? false : item.mode !== 'replace' || selected.length === 0)
            .filter((item) => `${item.displayName} ${item.spaceName ?? '私有'} ${item.stageSkillId}`.toLocaleLowerCase().includes(stageSearch.trim().toLocaleLowerCase()))
          return <div key={stage.stage} className="rounded-xl border border-gray-200 bg-gray-50/40 p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1"><p className="text-sm font-semibold text-gray-900">{stage.name}</p><p className="mt-1 text-xs leading-5 text-gray-500">{stage.description}</p></div>
              <div className="flex items-center gap-4">
                {stagePolicy(stageKey)?.canDisable
                  ? <label className="flex items-center gap-2 text-xs text-gray-700"><input type="checkbox" checked={stageSelection[stageKey]} onChange={(event) => updateStage(stageKey, event.target.checked)} className="h-4 w-4 rounded border-gray-300 accent-blue-600" />启用</label>
                  : <span className="rounded-md bg-blue-50 px-2 py-1 text-xs text-blue-700">必选</span>}
                {stageSelection[stageKey] && <label className="flex items-center gap-2 text-xs text-gray-700"><input type="checkbox" checked={customEnabled} onChange={(event) => toggleCustom(stageKey, event.target.checked)} className="h-4 w-4 rounded border-gray-300 accent-blue-600" />自定义</label>}
              </div>
            </div>
            {!stagePolicy(stageKey)?.canDisable && stagePolicy(stageKey)?.disabledReason && <p className="mt-2 text-[11px] text-gray-400">{stagePolicy(stageKey)?.disabledReason}</p>}
            {customEnabled && stageSelection[stageKey] && <div className="mt-3 flex flex-wrap items-center gap-2">
              {selected.map(([mode, binding]) => {
                const implementation = implementations.find((item) => item.implementationId === binding.implementationId)
                return <span key={mode} className="inline-flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800"><span className="font-medium">{modeCopy[mode].name}</span><span>{implementation?.displayName ?? binding.implementationId} · {implementation?.version ?? ''}</span><button type="button" onClick={() => removeBinding(stageKey, mode)} className="text-blue-400 hover:text-red-600">×</button></span>
              })}
              <button type="button" onClick={() => setPickerStage(pickerStage === stageKey ? null : stageKey)} className="rounded-lg border border-dashed border-blue-300 px-3 py-2 text-xs font-medium text-blue-600">＋ 选择实现</button>
            </div>}
            {pickerStage === stageKey && customEnabled && <div className="mt-3 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
              <div className="border-b border-gray-100 p-3"><input aria-label={`搜索${stage.name}自定义实现`} value={stageSearch} onChange={(event) => setStageSearch(event.target.value)} placeholder="搜索名称、空间或 Skill ID" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-xs" /></div>
              {selectable.length > 0 ? selectable.map((item) => <button type="button" key={item.implementationId} onClick={() => chooseImplementation(stageKey, item)} className="flex w-full items-center justify-between gap-4 border-b border-gray-100 px-4 py-3 text-left last:border-b-0 hover:bg-blue-50"><span><span className="block text-xs font-medium text-gray-900">{item.displayName} · {item.version}</span><span className="mt-0.5 block text-[10px] text-gray-400">{modeCopy[item.mode].name} · {modeCopy[item.mode].description}</span></span><span className="text-xs text-blue-600">选择</span></button>) : <div className="px-4 py-4 text-xs text-gray-500"><p>{options.length === 0 ? '暂无可用的自定义实现' : '当前组合下没有可添加的实现'}</p>{hasReplace && <p className="mt-1">整体替换不能与前置、后置处理同时使用。</p>}<Link to="/evolve/stage-skills/new" className="mt-2 inline-block font-medium text-blue-600 hover:text-blue-700">去接入自定义实现 ↗</Link></div>}
            </div>}
          </div>
        })}
        {flowKey !== 'bot_evolution' && implementations.some((item) =>
          item.status === 'registered' && item.integrationTestStatus !== 'test_passed')
          && <p className="text-xs text-gray-500">Skill 自进化只展示已经通过真实集成测试的自定义实现。</p>}
        {error && <p className="text-xs text-red-600">{error}</p>}
      </div>}
    </section>}
  </>
}
