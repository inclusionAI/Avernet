import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { api, type EvolveTask, type EvolveStep } from '../api/client'

type Stage = 'diagnose' | 'plan' | 'optimize'
type Mode = 'preprocess' | 'replace' | 'postprocess'
type FrozenBinding = { enabled: boolean; implementationId: string; displayName?: string }
type Extensions = Partial<Record<Stage, Partial<Record<Mode, FrozenBinding>>>>
type BoundStep = EvolveStep & { stageExtension?: {
  stage: Stage; mode: Mode; implementationId: string; displayName: string | null
} | null }
const stageNames = { diagnose: '诊断', plan: '目标规划', optimize: '优化' }
const modeNames = { preprocess: '前置', replace: '替换', postprocess: '后置' }
const statuses: Record<string, string> = {
  succeeded: '已完成', completed: '已完成', failed: '失败', canceled: '已取消',
  running: '运行中', dispatched: '已投递', accepted: '已接收', queued: '排队中', pending: '待执行',
  waiting_context: '等待补充信息', waiting_approval: '等待审批', waiting_acceptance: '等待验收',
}
function name(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}
function binding(step: BoundStep) {
  const value = step.stageExtension
  return step.stepType === 'stage_extension' && value
    && Object.hasOwn(stageNames, value.stage) && Object.hasOwn(modeNames, value.mode)
    && typeof value.implementationId === 'string' && value.implementationId
    ? value : null
}

export default function StageWorkflow({ task, selectedStepId, onSelect, renderDetails }: {
  task: EvolveTask
  selectedStepId: string | null
  onSelect: (stepId: string | null) => void
  renderDetails: (step: EvolveStep) => ReactNode
}) {
  const steps: BoundStep[] = (task.steps ?? []).filter((step) => step.taskId === task.task_id)
    .slice().sort((a, b) => a.stepNo - b.stepNo)
  const extensions = (task.config.stageExtensions ?? {}) as Extensions
  const flow = task.config.flow as { stages?: Partial<Record<Stage, boolean>> } | undefined
  const selection = flow?.stages ?? task.config.stageSelection as Partial<Record<Stage, boolean>> | undefined
  const defaults: Stage[] = ['optimize', 'bench_optimize'].includes(task.task_type) ? ['optimize']
    : task.task_type === 'diagnose' ? ['diagnose', 'plan']
      : task.config.inputMode === 'direct_goal' ? ['plan', 'optimize'] : ['diagnose', 'plan', 'optimize']
  const stages = (['diagnose', 'plan', 'optimize'] as const).filter((stage) =>
    (selection ? selection[stage] === true : defaults.includes(stage))
      || steps.some((step) => binding(step)?.stage === stage || step.stepType === stage))
  const selected = steps.find((step) => step.stepId === selectedStepId)
  // Old tasks may not have frozen names. This read is for labels only, never status or Step binding.
  const missingNames = JSON.stringify(Object.entries(extensions).flatMap(([stage, modes]) =>
    Object.entries(modes ?? {}).flatMap(([mode, value]) => value?.enabled && !name(value.displayName)
      ? [{ stage, mode, implementationId: value.implementationId }] : [])))
  const [resolvedNames, setResolvedNames] = useState<{ taskId: string; names: Record<string, string> }>({ taskId: '', names: {} })
  useEffect(() => {
    let active = true
    const entries = JSON.parse(missingNames) as Array<{ stage: string; mode: string; implementationId: string }>
    if (!entries.length) return
    void Promise.all(entries.map(async (entry) => {
      try {
        const value = await api.evolve.getStageSkill(entry.implementationId)
        return value.implementationId === entry.implementationId && value.stage === entry.stage && value.mode === entry.mode && name(value.displayName)
          ? [entry.implementationId, value.displayName] as const : null
      } catch { return null }
    })).then((values) => {
      if (active) setResolvedNames({ taskId: task.task_id, names: Object.fromEntries(values.filter((value) => value !== null)) })
    })
    return () => { active = false }
  }, [missingNames, task.task_id])
  const extensionName = (stage: Stage, mode: Mode, implementationId: string, step?: BoundStep) => {
    const frozen = extensions[stage]?.[mode]
    const receipt = step?.output?.implementation as { implementationId?: string; displayName?: string } | undefined
    return (frozen?.implementationId === implementationId ? name(frozen.displayName) : undefined)
      ?? name(step?.stageExtension?.displayName)
      ?? (receipt?.implementationId === implementationId ? name(receipt.displayName) : undefined)
      ?? (resolvedNames.taskId === task.task_id ? resolvedNames.names[implementationId] : undefined)
      ?? `名称不可用 · ${implementationId}`
  }
  const card = (step: BoundStep) => {
    const extension = binding(step)
    const title = extension ? extensionName(extension.stage, extension.mode, extension.implementationId, step)
      : stageNames[step.stepType as Stage] ?? step.stepType
    const done = ['succeeded', 'completed'].includes(step.status)
    return <button type="button" key={step.stepId} aria-pressed={selectedStepId === step.stepId}
      onClick={() => onSelect(step.stepId)}
      className={`w-52 shrink-0 rounded-xl border p-4 text-left transition hover:shadow-md ${
        done ? 'border-emerald-200 bg-emerald-50/50' : step.status === 'failed' ? 'border-red-200 bg-red-50/50' : 'border-blue-200 bg-white'
      } ${selectedStepId === step.stepId ? 'ring-2 ring-blue-500/30' : ''}`}>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="rounded bg-blue-50 px-2 py-1 text-blue-700">{extension ? modeNames[extension.mode] : step.stepType === 'stage_extension' ? '绑定信息缺失' : '默认处理'}</span>
        <span>{statuses[step.status] ?? step.status}</span>
      </div>
      <p className="mt-3 break-words text-sm font-semibold text-gray-900">{title}</p>
      {extension && <p className="mt-1 text-xs text-gray-500">{stageNames[extension.stage]} Stage</p>}
      <p className="mt-3 break-all font-mono text-[10px] text-gray-400">{step.stepId}</p>
    </button>
  }
  const sequence = (items: BoundStep[]) => <div className="flex min-w-max items-center gap-3">
    {items.map((step, index) => <Fragment key={step.stepId}>{index > 0 && <span aria-hidden="true" className="text-gray-300">→</span>}{card(step)}</Fragment>)}
  </div>
  const stageSequence = (stage: Stage, items: BoundStep[]) => {
    const nodes: ReactNode[] = []
    const pending = (title: string, label: string, key: string) => <button type="button" disabled key={key}
      className="w-52 shrink-0 rounded-xl border border-dashed border-gray-200 bg-white p-4 text-left text-xs text-gray-500">
      <span>{label}</span><p className="mt-3 break-words font-semibold">{title}</p>
      <p className="mt-2">{label !== '默认处理' && unbound.some((step) => !binding(step) || binding(step)?.stage === stage)
        ? '状态未知（未关联）' : '尚未创建'}</p>
    </button>
    for (const mode of ['preprocess', 'replace', 'postprocess'] as const) {
      const actual = items.filter((step) => binding(step)?.mode === mode || (mode === 'replace' && step.stepType === stage))
      nodes.push(...actual.map(card))
      const frozen = extensions[stage]?.[mode]
      if (!actual.length && frozen?.enabled) nodes.push(pending(extensionName(stage, mode, frozen.implementationId), modeNames[mode], mode))
      else if (!actual.length && mode === 'replace') nodes.push(pending(stageNames[stage], '默认处理', 'builtin'))
    }
    return <div className="overflow-x-auto pb-1"><div className="flex min-w-max items-center gap-3">
      {nodes.map((node, index) => <Fragment key={index}>{index > 0 && <span aria-hidden="true" className="text-gray-300">→</span>}{node}</Fragment>)}
    </div></div>
  }
  const unbound = steps.filter((step) => step.stepType === 'stage_extension' && !binding(step)
    || (step.stepType === 'optimize' || binding(step)?.stage === 'optimize')
      && !(typeof step.roundNo === 'number' && Number.isInteger(step.roundNo) && step.roundNo > 0))
  const internal = steps.filter((step) => ['skill_prepare', 'skill_init', 'skill_finalize', 'bench_plan'].includes(step.stepType))
  return <div>
    <div className="space-y-4 p-5">
      {stages.map((stage) => {
        const items = steps.filter((step) => step.stepType === stage || binding(step)?.stage === stage)
        const rounds = [...new Set(items.map((step) => step.roundNo).filter((round): round is number =>
          typeof round === 'number' && Number.isInteger(round) && round > 0))].sort((a, b) => a - b)
        return <section key={stage} aria-label={`${stageNames[stage]}工作流`} className="rounded-xl border border-gray-100 bg-gray-50/50 p-4">
        <h3 className="mb-3 text-sm font-semibold text-gray-900">{stageNames[stage]}{stage === 'optimize' ? ' Loop' : ' Stage'}</h3>
        {stage !== 'optimize' ? stageSequence(stage, items) : <div className="space-y-3">
          {rounds.map((round) => <div key={round} role="group" aria-label={`第 ${round} 轮优化`} className="rounded-lg border border-emerald-100 bg-emerald-50/30 p-3">
            <h4 className="mb-3 text-xs font-semibold text-emerald-800">第 {round} 轮优化</h4>
            {stageSequence(stage, items.filter((step) => step.roundNo === round))}
          </div>)}
          {rounds.length === 0 && <p className="text-xs text-gray-500">尚无可关联的优化轮次</p>}
        </div>}
      </section>})}
      {unbound.length > 0 && <section aria-label="未关联的执行步骤" className="rounded-xl border border-amber-200 p-4">
        <h3 className="mb-2 text-sm font-semibold">未关联的执行步骤</h3>
        <p className="mb-3 text-xs text-gray-500">缺少 Stage 绑定或轮次信息；保留实际状态，不推测节点归属。</p>
        <div className="overflow-x-auto">{sequence(unbound)}</div>
      </section>}
      {internal.length > 0 && <section aria-label="内部节点" className="rounded-xl border border-gray-100 p-4">
        <h3 className="mb-3 text-xs font-semibold">内部节点</h3><div className="overflow-x-auto">{sequence(internal)}</div>
      </section>}
    </div>
    {selected && <section aria-label="选中节点详情">
      <button type="button" className="mx-5 mb-2 text-xs text-blue-600" onClick={() => onSelect(null)}>收起节点详情</button>
      {renderDetails(selected)}
    </section>}
  </div>
}
