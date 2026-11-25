import { useId, useMemo, useState } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { WorkflowAiDraft } from '../services/workflow-ai-draft'

interface WorkflowAiDraftPreviewProps {
  draft: WorkflowAiDraft
  baseSpec: WorkflowSpec
  stale: boolean
  busy: boolean
  blockedReason?: string
  onApply: (draft: WorkflowAiDraft) => void
}

const kindLabels = { add: '新增', update: '更新', remove: '删除' }
const codeClass = 'max-h-56 overflow-auto rounded-md border border-input bg-muted p-3 font-mono text-[11px] leading-5'

export default function WorkflowAiDraftPreview({
  draft, baseSpec, stale, busy, blockedReason, onApply,
}: WorkflowAiDraftPreviewProps) {
  const confirmationId = useId()
  const [confirmed, setConfirmed] = useState(false)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set())
  const [expandedMetadata, setExpandedMetadata] = useState<Set<string>>(() => new Set())
  const before = useMemo(() => new Map(baseSpec.nodes.map((node) => [node.id, node])), [baseSpec])
  const after = useMemo(() => new Map(draft.spec.nodes.map((node) => [node.id, node])), [draft])
  const removals = [...new Set([
    ...draft.removedNodeIds,
    ...draft.changes.filter((change) => change.kind === 'remove').map((change) => change.nodeId),
    ...baseSpec.nodes.filter((node) => !after.has(node.id)).map((node) => node.id),
  ])]
  const needsConfirmation = removals.length > 0 || draft.metadataChanges.length > 0
  const canApply = !blockedReason && !busy && !stale && (!needsConfirmation || confirmed)

  return (
    <section aria-label="AI 草稿预览" className="space-y-3 border-t border-input pt-4">
      <div className="rounded-lg border border-input bg-card p-3">
        <h3 className="text-sm font-semibold">草稿待审阅</h3>
        <p className="mt-2 whitespace-pre-wrap text-xs leading-5">{draft.summary}</p>
      </div>
      {stale && (
        <p role="status" className="rounded-md border border-input bg-muted p-3 text-xs">
          当前工作流已更改，草稿已过期。请重新生成后再应用。
        </p>
      )}
      <div className="space-y-2">
        <h4 className="text-xs font-semibold">节点变更 · {draft.changes.length}</h4>
        {draft.changes.length === 0 && <p className="text-xs text-muted-foreground">没有节点变更，请检查元数据和 YAML。</p>}
        {draft.changes.map((change) => (
          <article key={`${change.kind}-${change.nodeId}`} className="rounded-lg border border-input bg-card p-3">
            <h5 className="text-xs font-medium">
              {kindLabels[change.kind]} · {change.title}
              <span className="ml-2 font-mono text-muted-foreground">{change.nodeId}</span>
            </h5>
            <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{change.reason}</p>
            <details className="mt-2 text-xs" onToggle={(event) => {
              const isOpen = event.currentTarget.open
              setExpandedNodes((current) => {
                const next = new Set(current)
                if (isOpen) next.add(change.nodeId)
                else next.delete(change.nodeId)
                return next
              })
            }}>
              <summary className="cursor-pointer font-medium">查看节点前后对比 · {change.nodeId}</summary>
              {/* Per-node serialization is lazy and linear, avoiding whole-workflow LCS costs. */}
              {expandedNodes.has(change.nodeId) && (
                <div className="mt-2 space-y-2">
                  <p className="text-muted-foreground">修改前</p>
                  <pre aria-label={`${change.nodeId} 修改前`} className={codeClass}>
                    {before.has(change.nodeId) ? stringifyYaml(before.get(change.nodeId), { lineWidth: 0 }) : '（不存在）'}
                  </pre>
                  <p className="text-muted-foreground">修改后</p>
                  <pre aria-label={`${change.nodeId} 修改后`} className={codeClass}>
                    {after.has(change.nodeId) ? stringifyYaml(after.get(change.nodeId), { lineWidth: 0 }) : '（已删除）'}
                  </pre>
                </div>
              )}
            </details>
          </article>
        ))}
      </div>
      {draft.metadataChanges.length > 0 && (
        <div className="rounded-lg border border-input p-3 text-xs">
          <h4 className="font-semibold">顶层元数据变更</h4>
          <ul className="mt-2 space-y-2 break-words">
            {draft.metadataChanges.map((field) => (
              <li key={field}>
                <details onToggle={(event) => {
                  const isOpen = event.currentTarget.open
                  setExpandedMetadata((current) => {
                    const next = new Set(current)
                    if (isOpen) next.add(field)
                    else next.delete(field)
                    return next
                  })
                }}>
                  <summary className="cursor-pointer font-medium">查看元数据前后对比 · {field}</summary>
                  {expandedMetadata.has(field) && (
                    <div className="mt-2 space-y-2">
                      <p className="text-muted-foreground">修改前</p>
                      <pre aria-label={`元数据 ${field} 修改前`} className={codeClass}>
                        {Object.hasOwn(baseSpec, field) ? stringifyYaml(baseSpec[field], { lineWidth: 0 }) : '（不存在）'}
                      </pre>
                      <p className="text-muted-foreground">修改后</p>
                      <pre aria-label={`元数据 ${field} 修改后`} className={codeClass}>
                        {Object.hasOwn(draft.spec, field) ? stringifyYaml(draft.spec[field], { lineWidth: 0 }) : '（已删除）'}
                      </pre>
                    </div>
                  )}
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
      {removals.length > 0 && <p className="break-words text-xs text-destructive">将删除节点：{removals.join('、')}</p>}
      {draft.warnings.length > 0 && (
        <div className="rounded-lg border border-input bg-muted p-3 text-xs">
          <h4 className="font-semibold">注意事项</h4>
          <ul className="mt-1 list-inside list-disc space-y-1">
            {draft.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
          </ul>
        </div>
      )}
      <details className="rounded-lg border border-input p-3 text-xs">
        <summary className="cursor-pointer font-medium">查看完整 YAML 草稿</summary>
        <pre aria-label="生成的 YAML" className={`mt-2 ${codeClass}`}>{draft.yaml}</pre>
      </details>
      {needsConfirmation && (
        <label htmlFor={confirmationId} className="flex items-start gap-2 rounded-lg border border-input p-3 text-xs leading-5">
          <input id={confirmationId} type="checkbox" checked={confirmed} disabled={Boolean(blockedReason) || busy || stale}
            onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />
          我已审阅并确认删除节点或修改顶层元数据
        </label>
      )}
      <button type="button" disabled={!canApply} onClick={() => { if (canApply) onApply(draft) }}
        className="w-full rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-40">
        应用草稿到当前 Editor
      </button>
      <p className="text-xs text-muted-foreground">仅更新编辑器内容；请审阅后自行保存或部署。</p>
    </section>
  )
}
