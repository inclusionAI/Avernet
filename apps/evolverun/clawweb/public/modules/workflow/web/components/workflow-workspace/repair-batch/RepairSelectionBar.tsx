import { primary } from './repair-view'

/** One next action, with its blocker visible wherever a user selects suggestions. */
export default function RepairSelectionBar({ count, issueCount, reason, onGenerate, onView, onClear, onTask, fixed = false }: {
  count: number; issueCount: number; reason: string; onGenerate: () => void
  onView?: () => void; onClear?: () => void; onTask?: () => void; fixed?: boolean
}) {
  return <section aria-label="本次修复操作" className={fixed
    ? 'fixed bottom-4 left-1/2 z-30 w-[min(64rem,calc(100%-2rem))] -translate-x-1/2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-lg'
    : 'border-t border-slate-200 bg-white px-5 py-3'}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <p aria-live="polite" className="text-sm font-semibold text-slate-900">已选 {count} 条建议{count > 0 && ` · 涉及 ${issueCount} 个问题`}</p>
        <p className={`mt-1 text-xs leading-5 ${reason && count ? 'text-amber-800' : 'text-slate-600'}`}>
          {reason || '生成后审阅实际修改，不会直接应用或部署。'}</p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {onView && count > 0 && <button type="button" className="text-xs text-blue-700 hover:underline" onClick={onView}>查看已选</button>}
        {onClear && count > 0 && <button type="button" className="text-xs text-slate-600 hover:underline" onClick={onClear}>清空选择</button>}
        {onTask && <button type="button" className="text-xs text-blue-700 hover:underline" onClick={onTask}>继续现有任务</button>}
        <button type="button" className={primary} disabled={!!reason} onClick={onGenerate}>生成修复草稿（{count}）</button>
      </div>
    </div>
  </section>
}
