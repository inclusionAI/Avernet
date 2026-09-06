export default function EvolveTaskOverview({
  label,
  subtitle,
  stages,
  deliverables,
}: {
  label: string
  subtitle: string
  stages: ReadonlyArray<ReadonlyArray<string>>
  deliverables: ReadonlyArray<ReadonlyArray<string>>
}) {
  return (
    <aside className="space-y-4 lg:sticky lg:top-6">
      <section className="rounded-2xl border border-blue-100 bg-gradient-to-b from-blue-50/80 to-white p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-gray-900">流程预览</p>
            <p className="mt-1 text-[11px] text-gray-500">{subtitle}</p>
          </div>
          <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-medium text-blue-700">{label}</span>
        </div>
        <ol className="mt-5 space-y-0">
          {stages.map(([title, description], index) => (
            <li key={title} className="relative flex gap-3 pb-5 last:pb-0">
              {index < stages.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-20px)] w-px bg-blue-200" />}
              <span className="relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-semibold text-white">{index + 1}</span>
              <div className="pt-0.5">
                <p className="text-xs font-semibold text-gray-900">{title}</p>
                <p className="mt-1 text-[10px] leading-4 text-gray-500">{description}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="rounded-2xl border border-gray-200 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">主要交付物</p>
        <div className="mt-4 grid grid-cols-2 gap-2.5">
          {deliverables.map(([title, description]) => (
            <div key={title} className="rounded-xl bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-800">{title}</p>
              <p className="mt-1 text-[10px] leading-4 text-gray-400">{description}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 border-t border-gray-100 pt-3 text-[10px] leading-4 text-gray-400">流程说明仅用于预览；实际节点、命令和结果以任务详情为准。</p>
      </section>
    </aside>
  )
}
