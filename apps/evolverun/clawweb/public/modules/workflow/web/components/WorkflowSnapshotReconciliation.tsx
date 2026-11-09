import { stringify } from 'yaml'
import { assertLosslessNumbers } from '../editor/losslessNumbers'
import { useId, useInsertionEffect, useRef, useState } from 'react'
import { confirmWorkflowReconciliation, invalidateWorkflowReconciliation, revokeWorkflowReconciliationApprovals, workflowReconciliationIsCurrent } from '../api/workflowSnapshotReconciliation'
import { containsUnsafeReviewText, displayReconciliationFile, reconciliationHasBinaryFiles } from '../api/workflowReconciliationFiles'
import type { ReconciliationFile } from '../api/workflowReconciliationFiles'
import type { ReconciliationApproval, ReconciliationContext, ReconciliationReview } from '../api/workflowSnapshotReconciliation'

/** This is a review, not a merge engine and not a write authorization. */
export default function WorkflowSnapshotReconciliation({ review, current, onConfirm, onCancel, busy = false }: {
  review: ReconciliationReview; current: ReconciliationContext
  onConfirm: (approval: ReconciliationApproval) => void; onCancel: () => void; busy?: boolean
}) {
  // Insertion-effect cleanup runs synchronously on removal without React's
  // development StrictMode effect replay. It only revokes a local capability.
  useInsertionEffect(() => () => invalidateWorkflowReconciliation(review), [review])
  // Even an accidentally different owner cannot leave the previously displayed
  // approval alive. Fail closed if React abandons a replacement render.
  const previous = useRef(review)
  if (previous.current !== review) {
    invalidateWorkflowReconciliation(previous.current)
    previous.current = review
  }
  return <ReviewForm key={reviewKey(review)} review={review} current={current} onConfirm={onConfirm} onCancel={onCancel} busy={busy} />
}

const keys = new WeakMap<object, number>()
let nextKey = 0
function reviewKey(review: object): number {
  let key = keys.get(review)
  if (key === undefined) { key = ++nextKey; keys.set(review, key) }
  return key
}

function ReviewForm({ review, current, onConfirm, onCancel, busy }: {
  review: ReconciliationReview; current: ReconciliationContext
  onConfirm: (approval: ReconciliationApproval) => void; onCancel: () => void; busy: boolean
}) {
  const radioName = useId()
  const [choice, setChoice] = useState<'db' | 'manual' | ''>('')
  const [manual, setManual] = useState(review.candidateSpecJson)
  const [readDb, setReadDb] = useState(false)
  const [readGit, setReadGit] = useState(false)
  const [understand, setUnderstand] = useState(false)
  const [error, setError] = useState('')
  const currentReview = workflowReconciliationIsCurrent(review, current)
  // A mismatch permanently invalidates the review. Before React renders,
  // callers must still call owner.edited() synchronously for editor changes.
  const binary = reconciliationHasBinaryFiles(review.filesBefore, review.filesAfter)
  const unsafeDocument = [review.dbSpecJson, review.gitYaml, review.candidateSpecJson].some(containsUnsafeReviewText)
  const edit = () => revokeWorkflowReconciliationApprovals(review)
  const selected = choice === 'db' ? review.dbSpecJson : manual
  let yamlPreview: string | null = null
  if (choice) {
    try { assertLosslessNumbers(selected); yamlPreview = stringify(JSON.parse(selected), { sortMapEntries: true, lineWidth: 0 }) }
    catch { /* Invalid or lossy JSON must not be presented as a YAML candidate. */ }
  }
  function confirm() {
    if (busy || !choice || !readDb || !readGit || !understand) return
    try { onConfirm(confirmWorkflowReconciliation(review, current, selected)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '无法确认文档。') }
  }
  const confirmDisabled = binary || unsafeDocument || !currentReview || !choice || !readDb || !readGit || !understand

  return <section aria-label="工作流快照审阅" className="mx-4 my-4 overflow-hidden rounded-2xl border border-border bg-card text-sm shadow-sm">
    <fieldset disabled={busy} className="min-w-0">
      <header className="border-b border-border bg-muted/40 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <span aria-hidden="true" className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-warning/15 text-sm font-bold text-warning-foreground">!</span>
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-foreground">保存前需要人工核对</h2>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">数据库草稿是已持久化的最新真值；当前编辑器候选可能包含之后尚未保存的修改。数据库与 Git 内容可能不同。这里不会自动合并；请选定最终 JSON 后，再确认要写入的内容。</p>
            </div>
          </div>
          <span className="shrink-0 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning-foreground">{busy ? '正在保存…' : '未发送写入'}</span>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <MetaItem label="工作流" value={review.workflowId} mono />
          <MetaItem label="Pack / 路径" value={`${review.packId} · ${review.workflowPath}`} />
          <MetaItem label="来源版本" value={review.sourceVersion} />
          <MetaItem label="草稿 head" value={review.expectedSavedHead ?? '无'} mono />
        </div>
      </header>

      <div className="space-y-4 p-5">
        <section aria-label="来源文档">
          <SectionHeading title="来源文档" hint="先看清三份内容，再决定最终 JSON" />
          <div className="grid gap-3 xl:grid-cols-3">
            <DocumentCard title="数据库原始文档" badge="数据库" tone="primary" content={review.dbSpecJson} />
            <DocumentCard title="Git 原始 YAML" badge="只读来源" tone="neutral" content={review.gitYaml} />
            <DocumentCard title="当前编辑器候选" badge="未确认" tone="warning" content={review.candidateSpecJson} />
          </div>
          <p className="mt-2 text-[11px] leading-5 text-muted-foreground">Git 版本：<span className="font-mono">{review.sourceVersion}</span> · 原 commit：<span className="font-mono break-all">{review.sourceCommit}</span> · 数据库 token：<span className="font-mono break-all">{review.expectedDraftToken}</span></p>
          <p className="mt-2 text-[11px] leading-5 text-muted-foreground">脚本和其他 Pack 文件按候选快照原样保留；工作流 YAML 会根据所选最终 JSON 重新生成。</p>
        </section>

        <details open className="group rounded-xl border border-border bg-background">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 font-medium text-foreground [&::-webkit-details-marker]:hidden">
            <span className="flex items-center gap-2"><span aria-hidden="true" className="text-muted-foreground transition-transform group-open:rotate-90">›</span>完整文件快照</span>
            <span className="text-xs font-normal text-muted-foreground">原始 {review.filesBefore.length} · 候选 {review.filesAfter.length} · 可展开/收起</span>
          </summary>
          <div className="space-y-3 border-t border-border p-4">
            <p className="text-xs leading-5 text-muted-foreground">候选工作流 YAML 仅为编辑器文件内容；实际保存内容由最终 JSON 在服务端重新生成。候选快照中的脚本与其他 Pack 文件保持原样。</p>
            <div className="grid gap-3 lg:grid-cols-2">
              <FileSnapshot label="原始文件" files={review.filesBefore} />
              <FileSnapshot label="候选文件" files={review.filesAfter} />
            </div>
          </div>
        </details>

        <details open className="group rounded-xl border border-border bg-background">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 font-medium text-foreground [&::-webkit-details-marker]:hidden">
            <span className="flex items-center gap-2"><span aria-hidden="true" className="text-muted-foreground transition-transform group-open:rotate-90">›</span>提交的文件操作</span>
            <span className="text-xs font-normal text-muted-foreground">{review.changes.length} 项 · 可展开/收起</span>
          </summary>
          <pre className="max-h-64 overflow-auto border-t border-border bg-slate-950 px-4 py-3 font-mono text-[11px] leading-5 text-slate-100">{JSON.stringify(review.changes, null, 2)}</pre>
        </details>

        {(unsafeDocument || binary) && <div className="space-y-2">
          {unsafeDocument && <p role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs leading-5 text-destructive">审阅文档含不可安全显示的字符；无法确认，请先使用支持安全转义的流程。</p>}
          {binary && <p role="alert" className="rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning-foreground">存在二进制或无法安全显示的文件；不能声称已阅读其内容，当前审阅不能确认保存。请先移除相关文件或使用支持二进制审阅的流程。</p>}
        </div>}

        <section className="rounded-xl border border-primary/20 bg-primary/5 p-4" aria-label="最终文档选择">
          <SectionHeading title="1 · 选择最终文档" hint="数据库原始 JSON 更稳妥；需要保留编辑器修改时再手工调整" />
          <fieldset className="mt-3 grid gap-2 md:grid-cols-2">
            <legend className="sr-only">选择最终文档</legend>
            <ChoiceCard checked={choice === 'db'} onChange={() => { edit(); setChoice('db'); setUnderstand(false) }} name={radioName} value="db" title="使用数据库原始 JSON" description="放弃当前候选中的编排修改，仅按已持久化内容重新生成 YAML。" />
            <ChoiceCard checked={choice === 'manual'} onChange={() => { edit(); setChoice('manual'); setUnderstand(false) }} name={radioName} value="manual" title="手工编辑最终 JSON" description="从当前编辑器候选开始，确认并修正最终要保存的文档。" />
          </fieldset>
        </section>

        {choice === 'manual' && <section className="rounded-xl border border-border bg-card p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <label htmlFor="reconciliation-final-json" className="font-medium text-foreground">最终 JSON</label>
            <span className="text-[11px] text-muted-foreground">修改后需要重新勾选确认项</span>
          </div>
          <textarea id="reconciliation-final-json" aria-label="最终 JSON" className="mt-3 block min-h-64 w-full resize-y rounded-lg border border-input bg-background px-3 py-2 font-mono text-xs leading-5 text-foreground outline-none transition focus:border-ring focus:ring-2 focus:ring-ring/20" rows={12} value={manual} onChange={event => { edit(); setManual(event.target.value); setUnderstand(false) }} />
        </section>}

        {choice && <section aria-label="将保存的 YAML 预览" className="rounded-xl border border-border bg-background">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div>
              <h3 className="font-medium text-foreground">2 · 将保存的 YAML 预览</h3>
              <p className="mt-0.5 text-[11px] text-muted-foreground">{review.workflowPath} · 服务端仍会再次校验</p>
            </div>
            <span className="rounded-full bg-muted px-2 py-1 text-[10px] text-muted-foreground">由最终 JSON 生成</span>
          </div>
          {yamlPreview === null ? <p role="alert" className="px-4 py-4 text-xs text-destructive">最终 JSON 无效或无法无损表示，无法预览。</p> : <pre className="max-h-80 overflow-auto px-4 py-3 font-mono text-xs leading-5 text-foreground">{yamlPreview}</pre>}
        </section>}

        <section className="rounded-xl border border-border bg-background p-4" aria-label="确认审阅结果">
          <SectionHeading title="3 · 确认审阅结果" hint="三项都完成后才允许发送统一保存" />
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <ReviewCheck checked={readDb} onChange={event => { edit(); setReadDb(event.target.checked) }} label="我已审阅数据库原始文档" />
            <ReviewCheck checked={readGit} onChange={event => { edit(); setReadGit(event.target.checked) }} label="我已审阅 Git 原始 YAML" />
            <ReviewCheck checked={understand} onChange={event => { edit(); setUnderstand(event.target.checked) }} label="我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成" />
          </div>
          {!currentReview && <p role="alert" className="mt-3 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">审阅期间基线或草稿已变化，请取消并重新核对。</p>}
          {error && <p role="alert" className="mt-3 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}
        </section>
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-muted/30 px-5 py-3">
        <p className="text-[11px] text-muted-foreground">确认后将按最终 JSON 重建 YAML；原 YAML 的注释、格式、锚点和标签可能丢失。</p>
        <div className="flex shrink-0 gap-2">
          <button type="button" onClick={() => { invalidateWorkflowReconciliation(review); onCancel() }} className="rounded-lg border border-input bg-card px-3 py-2 text-xs font-medium text-foreground transition hover:bg-muted">取消</button>
          <button type="button" disabled={confirmDisabled} onClick={confirm} className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40">确认最终文档</button>
        </div>
      </footer>
    </fieldset>
  </section>
}

function MetaItem({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2">
    <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
    <div className={`mt-1 truncate text-xs text-foreground ${mono ? 'font-mono' : ''}`} title={value}>{value}</div>
  </div>
}

function SectionHeading({ title, hint }: { title: string; hint: string }) {
  return <div className="flex flex-wrap items-baseline justify-between gap-2">
    <h3 className="font-semibold text-foreground">{title}</h3>
    <span className="text-[11px] text-muted-foreground">{hint}</span>
  </div>
}

function DocumentCard({ title, badge, tone, content }: { title: string; badge: string; tone: 'primary' | 'neutral' | 'warning'; content: string }) {
  const toneClass = tone === 'primary' ? 'border-primary/20 bg-primary/5' : tone === 'warning' ? 'border-warning/20 bg-warning/5' : 'border-border bg-background'
  const badgeClass = tone === 'primary' ? 'bg-primary/10 text-primary' : tone === 'warning' ? 'bg-warning/10 text-warning-foreground' : 'bg-muted text-muted-foreground'
  return <article className={`min-w-0 overflow-hidden rounded-xl border ${toneClass}`}>
    <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2.5">
      <h4 className="truncate text-xs font-semibold text-foreground">{title}</h4>
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${badgeClass}`}>{badge}</span>
    </div>
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-3 py-3 font-mono text-[11px] leading-5 text-foreground">{content}</pre>
  </article>
}

function ChoiceCard({ checked, onChange, name, value, title, description }: { checked: boolean; onChange: () => void; name: string; value: string; title: string; description: string }) {
  const descriptionId = useId()
  const accessibleTitle = value === 'manual' ? `${title}（可从候选文档开始）` : title
  return <label className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-3 transition ${checked ? 'border-primary bg-card shadow-sm ring-1 ring-primary/20' : 'border-border bg-background hover:border-primary/40'}`}>
    <input type="radio" name={name} value={value} aria-label={accessibleTitle} aria-describedby={descriptionId} checked={checked} onChange={onChange} className="mt-0.5 h-4 w-4 accent-primary" />
    <span className="min-w-0"><span className="block text-xs font-medium text-foreground">{accessibleTitle}</span><span id={descriptionId} className="mt-1 block text-[11px] leading-5 text-muted-foreground">{description}</span></span>
  </label>
}

function ReviewCheck({ checked, onChange, label }: { checked: boolean; onChange: (event: React.ChangeEvent<HTMLInputElement>) => void; label: string }) {
  return <label className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2.5 text-xs leading-5 transition ${checked ? 'border-success/30 bg-success/10 text-foreground' : 'border-border bg-card text-muted-foreground hover:border-primary/40'}`}>
    <input type="checkbox" checked={checked} onChange={onChange} className="mt-1 h-3.5 w-3.5 accent-primary" />
    <span>{label}</span>
  </label>
}

function FileSnapshot({ label, files }: { label: string; files: ReconciliationFile[] }) {
  return <section aria-label={label} className="min-w-0 rounded-xl border border-border bg-card p-3">
    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-xs font-semibold text-foreground">{label}</h4><span className="text-[10px] text-muted-foreground">{files.length} 个文件</span></div>
    <div className="space-y-2">
      {files.map(file => {
        const content = displayReconciliationFile(file)
        return <section key={file.path} aria-label={`${label}: ${file.path}`} className="overflow-hidden rounded-lg border border-border bg-background">
          <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2"><h5 className="min-w-0 break-all whitespace-normal font-mono text-[11px] text-foreground" title={file.path}>{file.path}</h5><span className="shrink-0 text-[10px] text-muted-foreground">模式 {file.mode}</span></div>
          {content.kind === 'text' ? <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-5 text-foreground">{content.text}</pre>
            : <p className="px-3 py-3 text-[11px] leading-5 text-warning-foreground">二进制或不可安全显示的内容；以下 base64 仅用于识别，不能视为内容已审阅。</p>}
          <details className="border-t border-border"><summary className="cursor-pointer px-3 py-2 text-[10px] text-muted-foreground">查看原始 base64</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all border-t border-border bg-slate-950 px-3 py-2 font-mono text-[10px] leading-4 text-slate-100">{file.contentBase64}</pre></details>
        </section>
      })}
      {files.length === 0 && <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">没有文件</p>}
    </div>
  </section>
}
