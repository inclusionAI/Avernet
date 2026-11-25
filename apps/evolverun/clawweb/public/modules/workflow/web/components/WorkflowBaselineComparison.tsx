import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { parseDocument } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { workflowReconciliationIsCurrent } from '../api/workflowSnapshotReconciliation'
import type { ReconciliationContext, ReconciliationReview } from '../api/workflowSnapshotReconciliation'
import { serializeUnifiedSpec } from '../api/workflowUnifiedSave'
import { containsUnsafeReviewText } from '../api/workflowReconciliationFiles'
import { validateWorkflowSnapshotRequest } from '../api/validateWorkflowSnapshotRequest'
import { parseWorkflowYaml } from '../editor/yamlDraft'
import { formatSpecForDiff, unifiedDiff } from './WorkflowVersionDiff'
import { sideBySideDiff } from './workflow-version-diff/sideBySide'
import SyntaxHighlightedText from './SyntaxHighlightedText'

/** The Git choice imports values into the editor; it does not write either store. */
export function parseGitWorkflowForImport(text: string, workflowId: string): WorkflowSpec {
  if (containsUnsafeReviewText(text)) throw new Error('Git YAML 含不可安全显示的字符。')
  const document = parseDocument(text, { uniqueKeys: true })
  if (document.errors.length || document.warnings.length) throw new Error('Git YAML 包含无法安全解析的内容。')
  const spec = parseWorkflowYaml(text)
  const pending: unknown[] = [spec], seen = new Set<object>()
  while (pending.length) {
    const value = pending.pop()
    if (typeof value === 'string' && containsUnsafeReviewText(value)) throw new Error('Git YAML 含不可安全显示的字符。')
    if (!value || typeof value !== 'object' || seen.has(value)) continue
    seen.add(value)
    for (const [key, item] of Object.entries(value)) {
      if (containsUnsafeReviewText(key)) throw new Error('Git YAML 含不可安全显示的字段名。')
      pending.push(item)
    }
  }
  return validateWorkflowSnapshotRequest(serializeUnifiedSpec(spec), workflowId)
}

export function escapeUnsafe(text: string): string {
  return Array.from(text, char => containsUnsafeReviewText(char) ? `\\u{${char.codePointAt(0)!.toString(16)}}` : char).join('')
}


export function DiffPanes({ rows, left, right, leftLabel, rightLabel }: {
  rows: ReturnType<typeof sideBySideDiff> | null; left: string; right: string; leftLabel: string; rightLabel: string
}) {
  return <div className="grid gap-3 lg:grid-cols-2">
    {([['left', leftLabel, left], ['right', rightLabel, right]] as const).map(([side, label, text]) => <section key={side} aria-label={label} className="min-w-0 overflow-hidden rounded-lg border border-border bg-background">
      <div className="border-b border-border bg-muted/50 px-3 py-2 text-xs font-semibold text-foreground">{label}</div>
      <div tabIndex={0} role="region" aria-label={`${label}内容`} className="max-h-72 overflow-auto font-mono text-xs leading-6 focus-visible:outline-2 focus-visible:outline-primary">
        {rows ? <div className="w-max min-w-full py-1">{rows.map((row, index) => {
          const line = row[side]
          return <div key={index} className={`flex min-h-6 ${line?.type === 'del' ? 'bg-destructive/10' : line?.type === 'add' ? 'bg-success/10' : ''}`}>
            <span aria-hidden="true" className="w-10 shrink-0 select-none border-r border-border px-2 text-right text-muted-foreground">{side === 'left' ? line?.fromLine : line?.toLine}</span>
            <span className="whitespace-pre px-3 text-foreground"><SyntaxHighlightedText text={line?.text ?? ''} /></span>
          </div>})}</div> : <pre className="m-0 whitespace-pre p-3 text-foreground"><SyntaxHighlightedText text={text} /></pre>}
      </div>
    </section>)}
  </div>
}
type Props = {
  review: ReconciliationReview
  current: ReconciliationContext
  onUseDatabase: () => void
  onUseGit: (spec: WorkflowSpec) => void
  hasPendingEdits?: boolean
  busy?: boolean
  onCancel: () => void
}

export default function WorkflowBaselineComparison({ review, current, onUseDatabase, onUseGit, onCancel, hasPendingEdits = false, busy = false }: Props) {
  const name = useId()
  const [choice, setChoice] = useState<'database' | 'git'>('database')
  const [confirmGit, setConfirmGit] = useState(false)
  const [error, setError] = useState('')
  const closeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLElement>(null), confirmationRef = useRef<HTMLElement>(null), returnRef = useRef<HTMLButtonElement>(null)
  const applyRef = useRef<HTMLButtonElement>(null), wasConfirm = useRef(false)
  const currentReview = workflowReconciliationIsCurrent(review, current)
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    return () => previous?.focus()
  }, [])
  useEffect(() => {
    if (confirmGit) returnRef.current?.focus()
    else if (wasConfirm.current) applyRef.current?.focus()
    wasConfirm.current = confirmGit
  }, [confirmGit])
  const git = useMemo(() => {
    try { return { spec: parseGitWorkflowForImport(review.gitYaml, review.workflowId), error: '' } }
    catch { return { spec: null, error: 'Git YAML 无法安全解析或不符合工作流要求，暂不能使用 Git。仍可使用数据库内容保存。' } }
  }, [review.gitYaml, review.workflowId])
  const leftRaw = formatSpecForDiff(review.candidateSpecJson)
  const left = escapeUnsafe(leftRaw), right = escapeUnsafe(review.gitYaml)
  const rows = useMemo(() => {
    // LCS uses quadratic memory; fall back to full raw text for large documents.
    if (left.split('\n').length * right.split('\n').length > 250_000) return null
    return sideBySideDiff(unifiedDiff(left, right))
  }, [left, right])
  const choose = () => {
    if (!currentReview) { setError('对比期间工作流或文件已变化，请关闭后重新查看差异。'); return }
    if (choice === 'database') { onUseDatabase(); return }
    if (git.spec) setConfirmGit(true)
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3 sm:p-6" onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); if (confirmGit) setConfirmGit(false); else onCancel(); return }
    if (event.key !== 'Tab') return
    const root = confirmGit ? confirmationRef.current : dialogRef.current
    const targets = root?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')
    if (!targets?.length) { event.preventDefault(); return }
    const first = targets[0], last = targets[targets.length - 1], active = document.activeElement
    if (event.shiftKey && (active === first || !root?.contains(active))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (active === last || !root?.contains(active))) { event.preventDefault(); first.focus() }
  }}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-label="对比数据库和 Git 工作流" className="flex max-h-full w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xl">
      <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div><h2 className="text-base font-semibold text-foreground">对比工作流差异</h2><p className="mt-1 text-xs text-muted-foreground">并排查看当前编辑器内容和 Git YAML；不选择时默认使用数据库工作流。</p></div>
        <button ref={closeRef} type="button" onClick={onCancel} aria-label="关闭对比" className="rounded p-1 text-lg leading-none text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">×</button>
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-5 py-4">
        <p className="text-xs text-muted-foreground">工作流：<span className="font-mono text-foreground">{review.workflowId}</span> · Git 路径：<span className="break-all font-mono text-foreground">{review.workflowPath}</span>。对比不修改数据库或 Git。</p>
        {(containsUnsafeReviewText(leftRaw) || containsUnsafeReviewText(review.gitYaml)) && <p role="status" className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning-foreground">不可安全显示的字符已转义显示；包含此类字符的 Git 内容不能直接载入编辑器。</p>}
        {rows === null && <p role="status" className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">文档较大，展示完整原文，不计算行级差异。</p>}
        <DiffPanes rows={rows} left={left} right={right} leftLabel="数据库工作流 + 当前编辑" rightLabel="Git 原始 YAML" />
        <fieldset className="space-y-2"><legend className="text-sm font-semibold text-foreground">选择工作流来源</legend>
          <p className="text-xs text-muted-foreground">选择只影响编辑器，不会自动合并。可直接保存；部署仍需通过服务端安全校验。</p>
          <div className="grid gap-3 md:grid-cols-2">
            <label className={`flex cursor-pointer gap-2 rounded-lg border p-3 ${choice === 'database' ? 'border-primary bg-primary/5' : 'border-border'}`}>
              <input type="radio" name={name} value="database" disabled={busy} checked={choice === 'database'} onChange={() => { setChoice('database'); setConfirmGit(false) }} className="mt-0.5 accent-primary" />
              <span><span className="block text-xs font-semibold text-foreground">使用数据库工作流 <span className="text-primary">默认</span></span><span className="mt-1 block text-xs text-muted-foreground">保留当前编辑器中的修改，继续使用现有草稿。</span></span>
            </label>
            <label className={`flex gap-2 rounded-lg border p-3 ${git.spec ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'} ${choice === 'git' ? 'border-primary bg-primary/5' : 'border-border'}`}>
              <input type="radio" name={name} value="git" checked={choice === 'git'} disabled={!git.spec || !currentReview || hasPendingEdits || busy} onChange={() => { setChoice('git'); setConfirmGit(false) }} className="mt-0.5 accent-primary" />
              <span><span className="block text-xs font-semibold text-foreground">使用 Git YAML</span><span className="mt-1 block text-xs text-muted-foreground">加载 Git 的工作流内容到编辑器，替换当前未保存的编排修改。</span></span>
            </label>
          </div>
        </fieldset>
        {hasPendingEdits && <p role="status" className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs text-warning-foreground">存在未应用的配置草稿；可继续查看差异。若要载入 Git，请先应用或放弃这些草稿。</p>}
        {busy && <p role="status" className="text-xs text-muted-foreground">正在重新核对数据库和 Git 基线…</p>}
        {git.error && <p role="status" className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">{git.error}</p>}
        {choice === 'git' && <p className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">Git 内容会替换当前编辑器中的编排和未保存修改；应用后仍需保存。保存时 YAML 会重新生成，原注释、格式和锚点可能丢失。</p>}
        {(!currentReview || error) && <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">{error || '对比期间工作流或文件已变化，请关闭后重新查看差异。'}</p>}
        {confirmGit && <section ref={confirmationRef} role="alertdialog" aria-label="确认替换当前编辑" aria-modal="true" className="rounded-lg border border-warning/30 bg-warning/10 p-4 text-sm">
          <h3 className="font-semibold text-foreground">确认使用 Git 内容？</h3><p className="mt-2 text-xs leading-5 text-warning-foreground">这会替换编辑器中的工作流及未保存修改，不会自动合并或直接写入 Git。</p>
          <div className="mt-3 flex gap-2"><button ref={returnRef} type="button" onClick={() => setConfirmGit(false)} className="rounded-md border border-input bg-card px-3 py-2 text-xs">返回</button><button type="button" disabled={!currentReview || busy} onClick={() => { if (currentReview && git.spec && !busy) onUseGit(git.spec) }} className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40">确认替换</button></div>
        </section>}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3"><p className="text-xs text-muted-foreground">可直接保存；部署仍需通过服务端安全校验。</p><div className="flex gap-2"><button type="button" onClick={onCancel} className="rounded-md border border-input px-3 py-2 text-xs">取消</button><button ref={applyRef} type="button" disabled={!currentReview || busy} onClick={choose} className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40">{choice === 'database' ? '保留当前内容' : '使用 Git 内容'}</button></div></footer>
    </section>
  </div>
}

export function WorkflowBaselineNotice({ comparison, canCompare, busy, onCompare, title, description }: {
  comparison: 'different' | 'uncomparable'; canCompare: boolean; busy: boolean; onCompare: () => void
  title?: string; description?: string
}) {
  return <div role="status" className="mx-4 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3">
    <div className="min-w-0"><p className="text-sm font-medium text-warning-foreground">{title ?? (comparison === 'different' ? 'Git 文件与数据库工作流存在差异' : 'Git 工作流与数据库内容暂时无法比较')}</p>
      <p className="mt-1 text-xs text-muted-foreground">{description ?? '默认使用数据库工作流及当前编辑器修改；可直接保存并查看差异。部分运行端可能立即读取保存后的数据库草稿；部署仍需通过服务端安全校验。'}</p></div>
    {canCompare && <button type="button" disabled={busy} onClick={onCompare} className="shrink-0 rounded-md border border-input bg-card px-3 py-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-40">查看差异</button>}
  </div>
}
