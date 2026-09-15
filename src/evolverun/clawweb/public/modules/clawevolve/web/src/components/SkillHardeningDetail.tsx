import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { EvolveTask, EvolveStep } from '../api/client'

export function skillHardeningImplementation(task: EvolveTask): string | null {
  const value = task.config.presentation as { kind?: unknown; spaceId?: unknown; hardeningImplementationId?: unknown } | undefined
  return value?.kind === 'skill_hardening' && typeof value.spaceId === 'string' && value.spaceId.trim()
    && typeof value.hardeningImplementationId === 'string' && value.hardeningImplementationId.trim()
    ? value.hardeningImplementationId : null
}

export default function SkillHardeningDetail({ task, implementationId, renderStatus }: {
  task: EvolveTask
  implementationId: string
  renderStatus: (status: string) => ReactNode
}) {
  const steps = (task.steps ?? []).filter((step) => step.taskId === task.task_id)
  const matching = steps.filter((step) => {
    const binding = (step as EvolveStep & { stageExtension?: {
      stage: string; mode: string; implementationId: string
    } | null }).stageExtension
    return step.status === 'succeeded' && step.stepType === 'stage_extension' && binding?.stage === 'diagnose'
      && binding.mode === 'preprocess' && binding.implementationId === implementationId
  })
  return <section aria-label="诊断前置结果" className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
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
}
