import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { changeSummary, sourceRunCount } from './RepairItemDetail'
import { exclusion, itemTitle, states } from './repair-view'

export default function IssueRepairPreview({ items, total, selected, canEdit, limit, onToggle, onDetail, onMore }: {
  items: RepairInboxItem[]; total: number; selected: string[]; canEdit: boolean; limit: number
  onToggle: (id: string) => void; onDetail: (item: RepairInboxItem) => void; onMore: () => void
}) {
  if (!total) return <p className="mt-3 text-xs text-slate-500">暂无可供选择的修复建议，仍可查看问题原因与证据。</p>
  return <section aria-label="本问题修复建议" className="mt-3 rounded-lg border border-slate-200 bg-white">
    <p className="border-b border-slate-100 px-3 py-2 text-xs font-medium text-slate-600">修复建议 · {total} 条</p>
    <div className="divide-y divide-slate-100">{items.map(item => {
      const checked = selected.includes(item.itemId)
      const reason = exclusion(item)
      const runs = sourceRunCount(item)
      return <article key={item.itemId} className={`flex items-start gap-3 px-3 py-2.5 ${checked ? 'bg-blue-50/50' : ''}`}>
        <input type="checkbox" aria-label={`选择 ${itemTitle(item)}`} checked={checked}
          disabled={!canEdit || !!reason || !checked && selected.length >= limit} onChange={() => onToggle(item.itemId)}
          className="mt-1 h-4 w-4 shrink-0 accent-blue-600" />
        <div className="min-w-0 flex-1">
          <button type="button" className="text-left text-sm font-medium leading-6 text-slate-900 hover:text-blue-700"
            aria-label={`查看建议 ${itemTitle(item)}`} onClick={() => onDetail(item)}>{itemTitle(item)}</button>
          <p className="mt-0.5 line-clamp-1 text-xs leading-5 text-slate-600">{changeSummary(item)}</p>
          <p className="mt-1 text-xs text-slate-500">{states[item.state]} · {runs === null ? '来源运行数未知' : `依据覆盖 ${runs} 个运行`}{reason && ` · ${reason}`}</p>
        </div>
      </article>
    })}</div>
    {total > items.length && <button type="button" className="w-full border-t border-slate-100 px-3 py-2 text-left text-xs text-blue-700 hover:bg-blue-50"
      onClick={onMore}>查看全部 {total} 条建议（已展示 {items.length} 条）</button>}
  </section>
}
