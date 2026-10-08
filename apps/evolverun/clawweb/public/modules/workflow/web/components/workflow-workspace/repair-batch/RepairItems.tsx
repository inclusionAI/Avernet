import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { exclusion, itemTitle, states } from './repair-view'
import { changeSummary, sourceRunCount } from './RepairItemDetail'

export function RepairStateCounts({ items }: { items: RepairInboxItem[] }) {
  return <span className="flex flex-wrap gap-2 text-xs text-slate-500">{Object.entries(states)
    .filter(([state]) => items.some(item => item.state === state))
    .map(([state, label]) => <span key={state}>{label} {items.filter(item => item.state === state).length}</span>)}</span>
}

export default function RepairItems({ items, selected, onToggle, canEdit, limit, onOpenDetail, embedded = false }: {
  items: RepairInboxItem[]; selected: string[]; onToggle: (id: string) => void;
  canEdit: boolean; limit: number; onOpenDetail?: (item: RepairInboxItem) => void; embedded?: boolean;
}) {
  return <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
    {!embedded && <p className="px-3 py-2 text-xs text-slate-500">本页修复建议 · {items.length} 条</p>}
    {items.map(item => {
      const reason = exclusion(item)
      const runs = sourceRunCount(item)
      return <article key={item.itemId} className="flex items-start gap-3 px-3 py-3">
        <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-blue-600" aria-label={`选择 ${itemTitle(item)}`}
          checked={selected.includes(item.itemId)} disabled={!canEdit || !!reason || (!selected.includes(item.itemId) && selected.length >= limit)}
          onChange={() => onToggle(item.itemId)} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium text-slate-800" title={itemTitle(item)}>{itemTitle(item)}</p>
          <p className="mt-1 text-xs leading-5 text-slate-600">修改：{changeSummary(item)}</p>
          <p className="mt-1 text-xs text-slate-500">{runs === null ? '来源运行数未知' : `本建议来源覆盖 ${runs} 个运行`} · {item.sources.length} 条来源引用</p>
          {reason && <p className="mt-1 text-xs text-amber-700">{reason}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span className="text-xs text-slate-500">{states[item.state]}</span>
          <button type="button" onClick={() => onOpenDetail?.(item)} className="rounded px-1 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600">建议详情</button>
        </div>
      </article>
    })}
  </div>
}
