import { useState } from 'react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { getResultBranches, withResultBranches, type ResultBranch } from '../../editor/contracts'
import { useEditorStore } from '../../editor/store'
import JsonDraftField, { isRecord } from './JsonDraftField'
import { TextField } from './NodeFields'

const buttonClass = 'rounded-md border border-input bg-background px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50'

function matchError(value: unknown): string | null {
  if (!isRecord(value)) return 'match 必须是对象。'
  return Object.values(value).some(item => item !== null && !['string', 'number', 'boolean'].includes(typeof item))
    ? '匹配值仅支持字符串、数字、布尔值或 null；复杂原始配置请保留在完整 JSON 中。' : null
}

/** Shared by the raw node entry and the structured branch editor. */
export function validateBranchChange(before: WorkflowNode, after: WorkflowNode, nodes: WorkflowNode[]): string | null {
  if (JSON.stringify(before.onResult) === JSON.stringify(after.onResult)) return null
  const branches = getResultBranches(after)
  for (const old of getResultBranches(before) ?? []) {
    const users = nodes.filter(node => node.id !== before.id && node.branchId === old.branchId)
    // complete is preserved compatibility data, not a branch-membership switch.
    if (users.length && !branches?.some(branch => branch.branchId === old.branchId)) {
      return `分支 ${old.branchId} 被 ${users.map(node => node.id).join('、')} 引用，不能删除或改标识。`
    }
  }
  if (branches === null) return null // Legacy/extension syntax remains raw, never auto-migrated.
  const used = new Set(nodes.filter(node => node.id !== before.id).flatMap(node => (getResultBranches(node) ?? []).map(branch => branch.branchId)))
  for (const [index, branch] of branches.entries()) {
    if (!branch.branchId.trim() || used.has(branch.branchId)) return '分支标识不能为空或重复。'
    used.add(branch.branchId)
    const error = matchError(branch.match)
    if (error) return error
    if (!Object.keys(branch.match).length && index !== branches.length - 1) return '兜底匹配 {} 必须位于最后。'
  }
  return null
}

export default function ResultBranchesEditor({ node, onPatch }: { node: WorkflowNode; onPatch: (patch: Partial<WorkflowNode>) => void }) {
  const nodes = useEditorStore(state => state.spec?.nodes ?? [])
  const pending = useEditorStore(state => Object.keys(state.pendingEdits).length > 0)
  const [candidate, setCandidate] = useState<ResultBranch | null>(null)
  const branches = getResultBranches(node)
  const raw = <JsonDraftField label="完整结果路由" value={node.onResult} draftKey={`node:${node.id}:onResult`}
    validate={value => validateBranchChange(node, { ...node, onResult: value as WorkflowNode['onResult'] }, nodes)}
    onApply={value => onPatch({ onResult: value as WorkflowNode['onResult'] })}
    allowRemove={node.onResult !== undefined} onRemove={() => onPatch({ onResult: undefined })} />
  if (branches === null) return <section className="space-y-3 border-t border-input pt-5">
    <h3 className="text-sm font-semibold">结果分支</h3>
    <p className="text-xs text-muted-foreground">保留原始配置模式：旧数组、if/then/else 或扩展配置不自动迁移，也不混写现代分支。</p>
    {raw}
  </section>

  function errorFor(next: ResultBranch[]) {
    return validateBranchChange(node, { ...node, ...withResultBranches(node, next) }, nodes)
  }
  function save(next: ResultBranch[]) {
    const error = errorFor(next)
    if (error) { useEditorStore.getState().setActionError(error); return }
    try { onPatch(withResultBranches(node, next)) }
    catch (cause) { useEditorStore.getState().setActionError(cause instanceof Error ? cause.message : String(cause)) }
  }
  function moved(index: number, offset: number) {
    const next = [...branches!]
    ;[next[index], next[index + offset]] = [next[index + offset], next[index]]
    return next
  }
  function insert(branch: ResultBranch) {
    const next = [...branches!]
    const fallback = next.findIndex(item => Object.keys(item.match).length === 0)
    next.splice(fallback < 0 ? next.length : fallback, 0, branch)
    return next
  }

  return <section className="space-y-3 border-t border-input pt-5">
    <div className="flex items-baseline justify-between"><h3 className="text-sm font-semibold">结果分支</h3><span className="text-xs text-muted-foreground">{branches.length} 条</span></div>
    <p className="text-xs text-muted-foreground">按顺序首个匹配生效。true 与 "true" 不同；兜底匹配 {'{}'} 必须放在最后。</p>
    {branches.map((branch, index) => {
      const referenced = nodes.some(item => item.id !== node.id && item.branchId === branch.branchId)
      return <fieldset key={index} aria-label={`分支 ${branch.branchId}`} className="min-w-0 space-y-3 rounded-md border border-input p-3">
        <legend className="max-w-full truncate px-1 text-xs text-muted-foreground">{index + 1} · {branch.branchId}{referenced ? ' · 已引用' : ''}</legend>
        <TextField label="分支标识" value={branch.branchId} readOnly={referenced}
          onChange={value => save(branches.map((item, i) => i === index ? { ...item, branchId: value } : item))} />
        <JsonDraftField label="匹配条件" value={branch.match} draftKey={`node:${node.id}:branch:${branch.branchId}:match`} rows={3}
          description={'JSON 保留标量类型：布尔值 true、字符串 "true"、数字与 null 不相同。'}
          validate={value => matchError(value) || errorFor(branches.map((item, i) => i === index ? { ...item, match: value as ResultBranch['match'] } : item))}
          onApply={value => onPatch(withResultBranches(node, branches.map((item, i) => i === index ? { ...item, match: value as ResultBranch['match'] } : item)))} />
        <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClass} disabled={pending || index === 0 || !!(index > 0 && errorFor(moved(index, -1)))} onClick={() => save(moved(index, -1))}>上移</button>
          <button type="button" className={buttonClass} disabled={pending || index === branches.length - 1 || !!(index < branches.length - 1 && errorFor(moved(index, 1)))} onClick={() => save(moved(index, 1))}>下移</button>
          <button type="button" className={`${buttonClass} text-destructive`} disabled={pending || referenced}
            onClick={() => { if (window.confirm(`确认删除分支 ${branch.branchId}？`)) save(branches.filter((_, i) => i !== index)) }}>删除分支</button>
        </div>
        {referenced && <p className="text-xs text-muted-foreground">已有节点使用此标识；先解除分支归属才能删除或改名。</p>}
      </fieldset>
    })}
    {candidate ? <div className="space-y-3 rounded-md border border-input p-3">
      <JsonDraftField label="新分支配置" value={candidate} draftKey={`node:${node.id}:branch:new`}
        description="填写 match 条件后应用；新分支将插入兜底之前，不自动连接节点。"
        validate={value => {
          if (!isRecord(value) || typeof value.branchId !== 'string' || !isRecord(value.match)
            || (value.complete !== undefined && typeof value.complete !== 'boolean')) return '需要 branchId 字符串、match 对象以及可选的 complete 布尔值。'
          return errorFor(insert(value as ResultBranch))
        }}
        onApply={value => { onPatch(withResultBranches(node, insert(value as ResultBranch))); setCandidate(null) }} />
      <button type="button" className={buttonClass} disabled={pending} onClick={() => setCandidate(null)}>取消新增分支</button>
    </div> : <button type="button" className={`${buttonClass} w-full py-2 text-primary`} disabled={pending}
      onClick={() => setCandidate({ branchId: `branch-${crypto.randomUUID()}`, match: {} })}>添加结果分支</button>}
    <details className="rounded-md border border-input p-3">
      <summary className="cursor-pointer text-xs text-muted-foreground">完整结果路由 JSON（含扩展字段）</summary>
      <div className="mt-3">{raw}</div>
    </details>
  </section>
}
