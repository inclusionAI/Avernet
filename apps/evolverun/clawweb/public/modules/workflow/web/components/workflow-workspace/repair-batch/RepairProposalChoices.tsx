import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, entry]) => JSON.stringify(key) + ':' + canonical(entry)).join(',') + '}'
  return JSON.stringify(value) ?? 'undefined'
}
/** Read-only comparison, never a new selection or proposal identity. */
export function proposalChoices(items: RepairInboxItem[]) {
  const groups = new Map<string, { field: string; choices: Array<{ value: string; items: RepairInboxItem[] }> }>()
  for (const item of items) for (const raw of Array.isArray(item.proposal?.operations) ? item.proposal.operations : []) {
    if (!raw || typeof raw !== 'object') continue
    const operation = raw as Record<string, unknown>
    if (typeof operation.path !== 'string' || operation.op === 'test') continue
    const field = `${String(operation.nodeId ?? '工作流')} ${operation.path}`
    const value = canonical({ op: operation.op ?? 'replace', value: operation.value })
    const group = groups.get(field) ?? { field, choices: [] }
    const choice = group.choices.find(choice => choice.value === value)
    if (choice) { if (!choice.items.some(prior => prior.itemId === item.itemId)) choice.items.push(item) }
    else group.choices.push({ value, items: [item] })
    groups.set(field, group)
  }
  return [...groups.values()]
}
export default function RepairProposalChoices({ items, total, onInspect }: { items: RepairInboxItem[]; total: number; onInspect?: (item: RepairInboxItem) => void }) {
  const groups = proposalChoices(items)
  if (items.length < 2) return null
  return <section aria-label="按实际修改比较方案" className="my-3 rounded-lg border border-input bg-muted/30 p-3 text-xs">
    <h5 className="font-semibold text-foreground">先比较修改方向，再选择方案</h5>
    <p className="mt-1 text-muted-foreground">本页 {items.length} 条 / 共 {total} 条。按实际字段与取值分组，不按标题合并；原始建议、来源和选择独立保留。</p>
    <p className="mt-1 text-muted-foreground">同字段不同取值需要取舍；不同字段仍需核对组合风险。按原顺序展示，不默认推荐或选择；缺少同基线效果验证时不能称为“已验证方案”。跨页方案在已选范围统一检查冲突。</p>
    {!groups.length && <p className="mt-2">当前建议没有可比的结构化修改，请逐项核对原始要求。</p>}
    {groups.map(group => <details key={group.field} open={groups.length <= 3} className="mt-2 rounded border border-input bg-card p-2">
      <summary className="cursor-pointer font-medium text-foreground">{group.field} · 同字段有 {group.choices.length} 种实际修改</summary>
      {group.choices.map((choice, i) => <details key={choice.value} open={i < 3} className="mt-2 border-t border-border pt-2">
        <summary className="cursor-pointer font-medium">方案方向 {i + 1} · 关联 {choice.items.length} 条建议</summary>
        <p className="mt-1 line-clamp-2 break-all font-mono text-muted-foreground">{choice.value}</p>
        <details className="mt-1"><summary>完整修改值与原始建议</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{choice.value}</pre>
          {choice.items.map(item => <button type="button" key={item.itemId} disabled={!onInspect} className="mt-1 block text-primary underline" onClick={() => onInspect?.(item)}>查看关联建议及证据</button>)}
        </details>
      </details>)}
    </details>)}
  </section>
}
