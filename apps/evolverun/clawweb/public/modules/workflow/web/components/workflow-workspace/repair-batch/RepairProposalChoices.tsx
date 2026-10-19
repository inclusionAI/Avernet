import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { proposalConflicts } from '../../../../server/contracts/repair-proposal'
import { itemHeading } from './repair-view'

const pathOf = (path: string) => path.startsWith('/') ? path : '/' + path.replaceAll('.', '/')
function operationsAt(item: RepairInboxItem, nodeId: string, path: string) {
  return (Array.isArray(item.proposal?.operations) ? item.proposal.operations : []).filter((raw): raw is Record<string, unknown> => {
    if (!raw || typeof raw !== 'object') return false
    const operation = raw as Record<string, unknown>
    if (operation.op === 'test' || typeof operation.path !== 'string' || String(operation.nodeId ?? '工作流') !== nodeId) return false
    const target = pathOf(operation.path)
    return target === path || target.startsWith(path + '/') || path.startsWith(target + '/')
  })
}
function modification(operation: Record<string, unknown>) {
  const value = operation.op === 'remove' ? '删除' : typeof operation.value === 'string' ? operation.value : JSON.stringify(operation.value) ?? '未提供值'
  return `${String(operation.path)} → ${value.length > 80 ? value.slice(0, 80) + '…' : value}`
}
/** Only selected structural conflicts need an extra decision. Selection stays in the original list. */
export default function RepairProposalChoices({ items, onRemove, disabled = false }: {
  items: RepairInboxItem[]; onRemove?: (id: string) => void; disabled?: boolean
}) {
  const conflicts = proposalConflicts(items)
  if (!conflicts.length) return null
  return <section role="alert" aria-label="已选建议冲突" className="space-y-3 rounded-lg border border-destructive/30 p-3 text-xs text-destructive">
    <p className="font-semibold">所选方案有 {conflicts.length} 处冲突，请保留其中一项后生成。</p>
    {conflicts.map(conflict => <div key={conflict.itemIds.join(':')}>
      <p className="font-medium break-all">{conflict.nodeId} · {conflict.path}</p><p>{conflict.reason}</p>
      {conflict.itemIds.map(id => {
        const item = items.find(item => item.itemId === id)!
        const operations = operationsAt(item, conflict.nodeId, conflict.path)
        return <div key={id} className="mt-2 text-foreground">
          <p className="font-medium">{itemHeading(item)}</p>
          {operations.map((operation, i) => <p key={i} className="break-all text-muted-foreground">{modification(operation)}</p>)}
          <div className="mt-1 flex flex-wrap items-start gap-3">
            {onRemove && <button type="button" aria-label={`取消选择 ${itemHeading(item)}`} disabled={disabled} className="text-primary underline disabled:opacity-50" onClick={() => onRemove(id)}>取消此项</button>}
            <details className="min-w-0"><summary className="cursor-pointer text-muted-foreground">完整修改</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(operations, null, 2)}</pre></details>
          </div>
        </div>
      })}
    </div>)}
  </section>
}
