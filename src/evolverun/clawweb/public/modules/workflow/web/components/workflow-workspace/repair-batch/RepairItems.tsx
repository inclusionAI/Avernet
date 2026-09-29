import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { button, exclusion, isLegacyPreview, itemTitle, states } from './repair-view'

function groupTitle(items: RepairInboxItem[]) {
  const signature = items.map(item => item.context?.signature).find(value => typeof value === 'string' && value.trim())
  if (typeof signature === 'string') return signature
  for (const item of items) {
    const label = [item.context?.nodeId, item.context?.failureMode].filter(value => typeof value === 'string' && value.trim()).join(' · ')
    if (label) return label
  }
  return '问题与建议'
}

function repairTheme(item: RepairInboxItem): { key: string; title: string } | null {
  const operations = item.proposal?.operations
  if (!Array.isArray(operations) || !operations.length) return null
  const nodes = [...new Set(operations.flatMap(operation => operation && typeof operation === 'object' && !Array.isArray(operation)
    && typeof operation.nodeId === 'string' && operation.nodeId.trim() ? [operation.nodeId.trim()] : []))].sort()
  if (!nodes.length) return null
  return { key: `${item.groupKey}:${nodes.join('\u0000')}`, title: nodes.join('、') }
}

function runCount(items: RepairInboxItem[]): number | null {
  const runs = new Set(items.flatMap(item => item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : [])))
  return runs.size || null
}

export default function RepairItems({ items, allItems = items, selected, onToggle, canEdit, limit, taskId, onDisposition,
  details, detailLoading, detailErrors, onLoadDetail }: {
  items: RepairInboxItem[]; allItems?: RepairInboxItem[]; selected: string[]; onToggle: (id: string) => void;
  canEdit: boolean; limit: number; taskId?: string; onDisposition?: (item: RepairInboxItem, action: 'no_action' | 'restore') => void;
  details?: Record<string, RepairInboxItem>; detailLoading?: Record<string, boolean>; detailErrors?: Record<string, string>;
  onLoadDetail?: (itemId: string) => void;
}) {
  const groups = [...new Set(items.map(item => item.groupKey))]
  return <div className="divide-y divide-slate-200">{groups.map(group => {
    const groupItems = items.filter(item => item.groupKey === group)
    const all = allItems.filter(item => item.groupKey === group)
    const counts = Object.entries(states).filter(([state]) => all.some(item => item.state === state))
    const title = groupTitle(all)
    const themed = new Map<string, { title: string; items: RepairInboxItem[] }>()
    for (const item of groupItems) {
      const theme = repairTheme(item)
      const key = theme?.key ?? `item:${item.itemId}`
      const entry = themed.get(key) ?? { title: theme?.title ?? '', items: [] }
      entry.items.push(item); themed.set(key, entry)
    }
    const renderItem = (item: RepairInboxItem) => {
      const reason = exclusion(item, taskId)
      const title = itemTitle(item)
      return <article key={item.itemId} className="rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-blue-600" aria-label={`选择 ${title}`}
          checked={selected.includes(item.itemId)} disabled={!canEdit || !!reason || (!selected.includes(item.itemId) && selected.length >= limit)} onChange={() => onToggle(item.itemId)} />
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><p className="break-words text-sm font-medium text-slate-800">{title}</p><span className="text-xs text-slate-500">{states[item.state]}</span></div>
            <p className="mt-1 text-xs leading-5 text-slate-500">内容 v{item.contentRevision} · {item.sources.length} 个来源</p>
            {reason && <p className="text-xs text-amber-700">{reason}</p>}
            <details className="mt-2 text-xs" onToggle={event => { if (event.currentTarget.open) onLoadDetail?.(item.itemId) }}>
              <summary className="cursor-pointer text-slate-500">查看建议与证据</summary>
              {detailLoading?.[item.itemId] ? <p role="status" className="mt-2 text-slate-500">加载证据…</p>
                : detailErrors?.[item.itemId] ? <p role="alert" className="mt-2 text-red-600">证据读取失败：{detailErrors[item.itemId]}</p>
                  : <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-50 p-3 text-[11px]">{JSON.stringify((() => {
                    const value = details?.[item.itemId] ?? item
                    return { instruction: value.instruction, proposal: value.proposal, sources: value.sources, ...('context' in value ? { context: value.context } : {}) }
                  })(), null, 2)}</pre>}
            </details>
            {item.disposition && <p className="mt-2 text-xs text-slate-500">处置记录：{item.disposition.reason}</p>}
            {canEdit && onDisposition && !isLegacyPreview(item) && (item.state === 'pending' || item.state === 'no_action') && <button type="button" className={`${button} mt-2`}
              aria-label={`${item.state === 'no_action' ? '恢复' : '暂不处理'} ${title}`} onClick={() => onDisposition(item, item.state === 'no_action' ? 'restore' : 'no_action')}>
              {item.state === 'no_action' ? '恢复待处理' : '暂不处理'}
            </button>}
          </div>
        </div>
      </article>
    }
    return <section key={group} className="py-3" aria-label={`问题组 ${title}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs"><span className="font-semibold text-slate-700">{title}</span>
        {counts.length > 1 && <span className="rounded bg-amber-50 px-2 py-1 text-amber-700">混合状态</span>}
        {counts.map(([state, label]) => <span className="text-slate-500" key={state}>{label} {all.filter(item => item.state === state).length}</span>)}
      </div>
      <div className="space-y-2">{[...themed.entries()].map(([key, theme]) => theme.items.length > 1
        ? <details key={key} role="group" aria-label={`修复主题 ${theme.title}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-800">{theme.title} · {theme.items.length} 个建议变体
            <span className="ml-2 text-xs font-normal text-slate-500">{runCount(theme.items) === null ? '影响运行数未知' : `影响 ${runCount(theme.items)} 个运行`} · 已选择 {theme.items.filter(item => selected.includes(item.itemId)).length} 项</span>
          </summary>
          <div className="mt-3 space-y-2">{theme.items.map(renderItem)}</div>
        </details> : renderItem(theme.items[0]))}</div>
    </section>
  })}</div>
}
