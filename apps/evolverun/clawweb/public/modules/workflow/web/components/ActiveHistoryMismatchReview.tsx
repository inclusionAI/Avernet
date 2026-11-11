import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { containsUnsafeReviewText } from '../api/workflowReconciliationFiles'
import { formatSpecForDiff, unifiedDiff } from './WorkflowVersionDiff'
import { sideBySideDiff } from './workflow-version-diff/sideBySide'
import { DiffPanes, escapeUnsafe, WorkflowBaselineNotice } from './WorkflowBaselineComparison'

export type ActiveHistoryChoice = 'database' | 'git'
export type ActiveHistoryEvidence = {
  workflowId: string
  deployNumber: number
  gitPath: string
  databaseText: string | null
  gitYaml: string | null
}

/** The choice changes only the #N baseline used for editor display. It never
 * imports an old workflow into the editable candidate or changes #N+1 bytes. */
export default function ActiveHistoryMismatchReview({
  evidence, deployNumber, selected, onChoose,
}: {
  evidence: ActiveHistoryEvidence | null
  deployNumber: number | null
  selected: ActiveHistoryChoice
  onChoose: (choice: ActiveHistoryChoice) => void
}) {
  const name = useId()
  const [open, setOpen] = useState(false)
  const [choice, setChoice] = useState(selected)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => { setChoice(selected) }, [selected])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    return () => previous?.focus()
  }, [open])
  const visibleNumber = evidence?.deployNumber ?? deployNumber
  const databaseText = evidence?.databaseText ?? null
  const gitYaml = evidence?.gitYaml ?? null
  const comparable = !!databaseText && !!gitYaml
  const leftRaw = databaseText ? formatSpecForDiff(databaseText) : ''
  const left = escapeUnsafe(leftRaw)
  const right = escapeUnsafe(gitYaml ?? '')
  const rows = useMemo(() => {
    if (!databaseText || !gitYaml) return null
    // The line-diff guard is shared with the editor's DB/Git comparison.
    if (left.split('\n').length * right.split('\n').length > 250_000) return null
    return sideBySideDiff(unifiedDiff(left, right))
  }, [databaseText, gitYaml, left, right])
  const label = visibleNumber === null ? '生效版本' : `生效 #${visibleNumber}`
  return <>
    <WorkflowBaselineNotice
      comparison={comparable ? 'different' : 'uncomparable'}
      canCompare={comparable}
      busy={false}
      onCompare={() => setOpen(true)}
      title={`${label}的数据库发布记录与 Git 文件存在差异`}
      description={comparable
        ? `默认使用数据库发布记录作为对比基线；不影响后续新版本部署。当前以${choice === 'database' ? '数据库发布记录' : 'Git YAML'}为准。`
        : '两侧原文暂不可完整读取，仍保留告警；不影响后续新版本部署。'}
    />
    {open && evidence && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3 sm:p-6" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false) } }}>
        <section role="dialog" aria-modal="true" aria-label="对比生效数据库记录和 Git 工作流" className="flex max-h-full w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xl">
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">对比工作流差异</h2>
              <p className="mt-1 text-xs text-muted-foreground">并排查看生效发布记录和 Git YAML；不选择时默认使用数据库发布记录。</p>
            </div>
            <button ref={closeRef} type="button" onClick={() => setOpen(false)} aria-label="关闭对比" className="rounded p-1 text-lg leading-none text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">×</button>
          </header>
          <div className="min-h-0 flex-1 space-y-4 overflow-auto px-5 py-4">
            <p className="text-xs text-muted-foreground">工作流：<span className="font-mono text-foreground">{evidence.workflowId}</span> · Git 路径：<span className="break-all font-mono text-foreground">{evidence.gitPath}</span>。选择只影响编辑器显示的生效基线，不修改数据库或 Git。</p>
            {(databaseText !== null && containsUnsafeReviewText(databaseText)) || (gitYaml !== null && containsUnsafeReviewText(gitYaml))
              ? <p role="status" className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning-foreground">不可安全显示的字符已转义显示；选择仅用于展示。</p>
              : null}
            {rows === null && <p role="status" className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">文档较大，展示完整原文，不计算行级差异。</p>}
            <DiffPanes rows={rows} left={left} right={right} leftLabel={`数据库发布记录 #${evidence.deployNumber}`} rightLabel="Git 原始 YAML" />
            <fieldset className="space-y-2">
              <legend className="text-sm font-semibold text-foreground">选择 #{evidence.deployNumber} 的对比基线</legend>
              <p className="text-xs text-muted-foreground">选择不会写入历史记录，也不会把旧生效内容带入本次部署。</p>
              <div className="grid gap-3 md:grid-cols-2">
                <label className={`flex cursor-pointer gap-2 rounded-lg border p-3 ${choice === 'database' ? 'border-primary bg-primary/5' : 'border-border'}`}>
                  <input type="radio" name={name} value="database" checked={choice === 'database'} onChange={() => setChoice('database')} className="mt-0.5 accent-primary" />
                  <span><span className="block text-xs font-semibold text-foreground">使用数据库发布记录</span><span className="mt-1 block text-xs text-muted-foreground">保留现有编辑器内容，继续按数据库 history 展示生效基线。</span></span>
                </label>
                <label className={`flex cursor-pointer gap-2 rounded-lg border p-3 ${choice === 'git' ? 'border-primary bg-primary/5' : 'border-border'}`}>
                  <input type="radio" name={name} value="git" checked={choice === 'git'} onChange={() => setChoice('git')} className="mt-0.5 accent-primary" />
                  <span><span className="block text-xs font-semibold text-foreground">使用 Git YAML</span><span className="mt-1 block text-xs text-muted-foreground">按实际生效的 Git tag YAML 展示差异基线。</span></span>
                </label>
              </div>
            </fieldset>
          </div>
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-3">
            <p className="text-xs text-muted-foreground">不会自动合并；后续新版本部署仍发布当前已保存的候选快照。</p>
            <div className="flex gap-2">
              <button type="button" onClick={() => setOpen(false)} className="rounded-md border border-input px-3 py-2 text-xs">取消</button>
              <button type="button" disabled={!comparable} onClick={() => { onChoose(choice); setOpen(false) }} className="rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40">{choice === 'database' ? '保留当前基线' : '使用 Git YAML'}</button>
            </div>
          </footer>
        </section>
      </div>
    )}
  </>
}
