import { useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { EvolveTask, EvolveStep } from '../api/client'
import StageWorkflow from './StageWorkflow'

export function skillHardeningImplementation(task: EvolveTask): string | null {
  const value = task.config.presentation as { kind?: unknown; spaceId?: unknown; hardeningImplementationId?: unknown } | undefined
  return value?.kind === 'skill_hardening' && typeof value.spaceId === 'string' && value.spaceId.trim()
    && typeof value.hardeningImplementationId === 'string' && value.hardeningImplementationId.trim()
    ? value.hardeningImplementationId : null
}

export default function SkillHardeningDetail({ task, implementationId, renderStatus, renderInteractions }: {
  task: EvolveTask
  implementationId: string
  renderStatus: (status: string) => ReactNode
  renderInteractions: (stepId: string) => ReactNode
}) {
  const steps = (task.steps ?? []).filter((step) => step.taskId === task.task_id)
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
  const matching = steps.filter((step) => {
    const binding = (step as EvolveStep & { stageExtension?: {
      stage: string; mode: string; implementationId: string
    } | null }).stageExtension
    return step.status === 'succeeded' && step.stepType === 'stage_extension' && binding?.stage === 'diagnose'
      && binding.mode === 'preprocess' && binding.implementationId === implementationId
  })
  // Interaction ownership is independent of the output/presentation filter.
  const interactionSteps = [...new Set((task.interactions ?? []).map((item) => item.stepId))]
  return <div className="mt-6 space-y-5">
    <section aria-label="进化工作流" className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-4"><div><h2 className="text-sm font-semibold">进化工作流</h2><p className="mt-1 text-xs text-gray-500">自定义 Stage 保留独立节点、注册名称和真实状态。</p></div>{renderStatus(task.status)}</div>
      {task.error_message && <p role="alert" className="mx-5 mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{task.error_message}</p>}
      {steps.some((step) => step.error) && <div className="mx-5 mt-4 space-y-2">
        {steps.filter((step) => step.error).map((step) => <p role="alert" key={step.stepId} className="rounded-lg bg-red-50 p-3 text-xs text-red-700"><span className="font-mono">{step.stepId}: </span>{step.error?.code ? `${step.error.code}: ` : ''}{step.error?.message}</p>)}
      </div>}
      <StageWorkflow task={task} selectedStepId={selectedStepId} onSelect={setSelectedStepId}
        renderDetails={(step) => <div className="border-t border-gray-100 px-5 py-4 text-xs text-gray-600"><div className="flex flex-wrap items-center justify-between gap-2"><span className="break-all font-mono">{step.stepId} · {step.stepType}</span>{renderStatus(step.status)}</div></div>} />
    </section>
    {interactionSteps.length > 0 && <section aria-label="交互表单" className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold">交互表单</h2>
      {interactionSteps.map((stepId) => <div key={stepId} id={`step-${stepId}`} className="mt-4">
        <p className="break-all font-mono text-xs text-gray-400">{stepId}</p>{renderInteractions(stepId)}
      </div>)}
    </section>}
    <section aria-label="诊断前置结果" className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold">诊断前置结果</h2>
      {!matching.length && <p className="mt-3 text-sm text-gray-500">尚无可关联的诊断前置输出</p>}
      {matching.map((step) => <article key={step.stepId} className="mt-4 border-t border-gray-100 pt-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs"><span className="break-all font-mono text-gray-400">{step.stepId}</span>{renderStatus(step.status)}</div>
        {typeof step.output?.summary === 'string' && step.output.summary.trim()
          ? <div className="space-y-3 break-words text-sm leading-6 text-gray-800 [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_ul]:list-disc [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:whitespace-pre-wrap">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{step.output.summary}</ReactMarkdown>
          </div>
          : <p className="text-sm text-gray-500">尚无 summary 输出</p>}
        <h3 className="mt-4 text-xs font-semibold text-gray-700">变更文件</h3>
        {Array.isArray(step.output?.changed_files) && step.output.changed_files.every((path) => typeof path === 'string')
          ? step.output.changed_files.length
            ? <ul className="mt-2 list-inside list-disc text-xs text-gray-600">{step.output.changed_files.map((path, index) => <li className="break-all font-mono" key={index}>{path}</li>)}</ul>
            : <p className="mt-2 text-xs text-gray-500">未报告变更文件</p>
          : <p className="mt-2 text-xs text-gray-500">尚无可展示的 changed_files 列表</p>}
      </article>)}
    </section>
  </div>
}
