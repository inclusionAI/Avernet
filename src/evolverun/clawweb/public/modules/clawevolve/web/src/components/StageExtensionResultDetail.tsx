import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { EvolveStep } from '../api/client'

/** Reusable Stage result view. Business-specific matching stays in the host. */
export default function StageExtensionResultDetail({ step, title = '自定义 Stage 结果' }: {
  step: EvolveStep
  title?: string
}) {
  return <section className="mt-4 rounded-xl border border-emerald-100 bg-emerald-50/40 p-4">
    <h2 className="text-sm font-semibold text-emerald-950">{title}</h2>
    {typeof step.output?.summary === 'string' && step.output.summary.trim()
      ? <div className="mt-3 space-y-3 break-words text-sm leading-6 text-gray-800 [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_ul]:list-disc [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:whitespace-pre-wrap">
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt}</span> }}>{step.output.summary}</ReactMarkdown>
      </div>
      : <p className="mt-3 text-sm text-gray-500">尚无 summary 输出</p>}
    <h3 className="mt-4 text-xs font-semibold text-gray-700">变更文件</h3>
    {Array.isArray(step.output?.changed_files) && step.output.changed_files.every((path) => typeof path === 'string')
      ? step.output.changed_files.length
        ? <ul className="mt-2 list-inside list-disc text-xs text-gray-600">{step.output.changed_files.map((path, index) => <li className="break-all font-mono" key={index}>{path}</li>)}</ul>
        : <p className="mt-2 text-xs text-gray-500">未报告变更文件</p>
      : <p className="mt-2 text-xs text-gray-500">尚无可展示的 changed_files 列表</p>}
  </section>
}
