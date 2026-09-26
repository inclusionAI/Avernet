import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { EvolveStep } from '../api/client'

/** Generic result view for every custom Stage implementation. */
export default function StageExtensionResultDetail({ step, title = '自定义 Stage', stageLabel, modeLabel, custom = true }: {
  step: EvolveStep
  title?: string
  stageLabel?: string
  modeLabel?: string
  custom?: boolean
}) {
  const summary = typeof step.output?.summary === 'string' ? step.output.summary : ''
  const changedFiles = Array.isArray(step.output?.changed_files)
    && step.output.changed_files.every((path) => typeof path === 'string')
    ? step.output.changed_files as string[] : null
  const summaryIncludesChangedFiles = /^#{1,6}\s*变更文件\s*$/m.test(summary)
  return <section className="mt-4 rounded-xl border border-gray-200 bg-gray-50/60 p-4">
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-sm font-semibold text-gray-950">{title}</h2>
      <span className="rounded bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-700">{custom ? '自定义 Stage' : '平台默认'}</span>
      {(stageLabel || modeLabel) && <span className="text-[11px] text-gray-500">{[stageLabel, modeLabel].filter(Boolean).join(' · ')}</span>}
    </div>
    {summary.trim()
      ? <div className="mt-3 space-y-3 break-words text-sm leading-6 text-gray-800 [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_ul]:list-disc [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:whitespace-pre-wrap">
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{summary}</ReactMarkdown>
      </div>
      : <p className="mt-3 text-sm text-gray-500">尚无 summary 输出</p>}
    {changedFiles !== null && !summaryIncludesChangedFiles && <><h3 className="mt-4 text-xs font-semibold text-gray-700">变更文件</h3>
      {changedFiles.length
        ? <ul className="mt-2 list-inside list-disc text-xs text-gray-600">{step.output.changed_files.map((path, index) => <li className="break-all font-mono" key={index}>{path}</li>)}</ul>
        : <p className="mt-2 text-xs text-gray-500">未报告变更文件</p>}</>}
  </section>
}
