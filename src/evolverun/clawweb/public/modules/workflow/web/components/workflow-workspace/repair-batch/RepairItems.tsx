import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { button, exclusion, isLegacyPreview, itemTitle, JsonDetails, states } from './repair-view'

function groupTitle(items: RepairInboxItem[]) {
  const signature = items.map(item => item.context?.signature).find(value => typeof value === 'string' && value.trim())
  if (typeof signature === 'string') return signature
  for (const item of items) {
    const label = [item.context?.nodeId, item.context?.failureMode].filter(value => typeof value === 'string' && value.trim()).join(' · ')
    if (label) return label
  }
  return '问题与建议'
}

export default function RepairItems({ items, allItems = items, selected, onToggle, canEdit, limit, taskId, onDisposition }: {
  items: RepairInboxItem[]; allItems?: RepairInboxItem[]; selected: string[]; onToggle: (id: string) => void;
  canEdit: boolean; limit: number; taskId?: string; onDisposition?: (item: RepairInboxItem, action: 'no_action' | 'restore') => void;
}) {
  const groups = [...new Set(items.map(item => item.groupKey))]
  return <div className="divide-y divide-slate-200">{groups.map(group => {
    const groupItems = items.filter(item => item.groupKey === group)
    const all = allItems.filter(item => item.groupKey === group)
    const counts = Object.entries(states).filter(([state]) => all.some(item => item.state === state))
    const title = groupTitle(all)
    return <section key={group} className="py-3" aria-label={`问题组 ${title}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs"><span className="font-semibold text-slate-700">{title}</span>
        {counts.length > 1 && <span className="rounded bg-amber-50 px-2 py-1 text-amber-700">混合状态</span>}
        {counts.map(([state, label]) => <span className="text-slate-500" key={state}>{label} {all.filter(item => item.state === state).length}</span>)}
      </div>
      <div className="space-y-2">{groupItems.map(item => {
        const reason = exclusion(item, taskId)
        const title = itemTitle(item)
        return <article key={item.itemId} className="rounded-lg border border-slate-200 bg-white p-3">
          <div className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-blue-600" aria-label={`选择 ${title}`}
            checked={selected.includes(item.itemId)} disabled={!canEdit || !!reason || (!selected.includes(item.itemId) && selected.length >= limit)} onChange={() => onToggle(item.itemId)} />
            <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><p className="break-words text-sm font-medium text-slate-800">{title}</p><span className="text-xs text-slate-500">{states[item.state]}</span></div>
              <p className="mt-1 text-xs leading-5 text-slate-500">内容 v{item.contentRevision} · {item.sources.length} 个来源</p>
              {reason && <p className="text-xs text-amber-700">{reason}</p>}
              <JsonDetails title="查看建议与证据" value={{ instruction: item.instruction, proposal: item.proposal, sources: item.sources, ...('context' in item ? { context: item.context } : {}) }} />
              {item.disposition && <p className="mt-2 text-xs text-slate-500">处置记录：{item.disposition.reason}</p>}
              {canEdit && onDisposition && !isLegacyPreview(item) && (item.state === 'pending' || item.state === 'no_action') && <button type="button" className={`${button} mt-2`}
                aria-label={`${item.state === 'no_action' ? '恢复' : '暂不处理'} ${title}`} onClick={() => onDisposition(item, item.state === 'no_action' ? 'restore' : 'no_action')}>
                {item.state === 'no_action' ? '恢复待处理' : '暂不处理'}
              </button>}
            </div>
          </div>
        </article>
      })}</div>
    </section>
  })}</div>
}
