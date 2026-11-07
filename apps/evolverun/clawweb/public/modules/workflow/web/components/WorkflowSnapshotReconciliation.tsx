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
  return <section aria-label="工作流快照审阅" className="rounded border border-input bg-card p-4 text-sm">
    <fieldset disabled={busy}>
    <p>数据库草稿是已持久化的最新真值；当前编辑器候选可能包含之后尚未保存的修改。数据库与 Git 内容可能不同。这里不会自动合并或判断 YAML 是否可无损解析。请对照原文自行决定最终 JSON；服务端会从该 JSON 重新生成 YAML，原 YAML 的注释、格式、锚点、标签等可能丢失或语义不同。无法安全表达的内容请取消，先人工处理。</p>
    <p>工作流：{review.workflowId}；Pack：{review.packId}；路径：{review.workflowPath}</p>
    <p>Git 版本：{review.sourceVersion}；原 commit：{review.sourceCommit}；预期 草稿(wf) head：{review.expectedSavedHead ?? '无'}；数据库 token：{review.expectedDraftToken}</p>
    <div className="grid gap-4 md:grid-cols-2">
      <div><h3>数据库原始文档</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap">{review.dbSpecJson}</pre></div>
      <div><h3>Git 原始 YAML</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap">{review.gitYaml}</pre></div>
    </div>
    <h3>当前编辑器候选文档</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{review.candidateSpecJson}</pre>
    <h3>完整文件快照：原始版本与候选版本</h3><p>候选工作流 YAML 仅为编辑器文件内容；实际保存内容由最终 JSON 在服务端重新生成。</p>
    <div className="grid gap-4 md:grid-cols-2">
      <FileSnapshot label="原始文件" files={review.filesBefore} />
      <FileSnapshot label="候选文件" files={review.filesAfter} />
    </div>
    <p>除下列明确列出的文件操作外，脚本和其他 Pack 文件按候选快照原样保留；工作流 YAML 会根据所选最终 JSON 生成。</p>
    <h3>提交的文件操作</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(review.changes, null, 2)}</pre>
    {unsafeDocument && <p role="alert">审阅文档含不可安全显示的字符；无法确认，请先使用支持安全转义的流程。</p>}
    {binary && <p role="alert">存在二进制或无法安全显示的文件；不能声称已阅读其内容，当前审阅不能确认保存。请先移除相关文件或使用支持二进制审阅的流程。</p>}
    <fieldset><legend>选择最终文档</legend>
      <label><input type="radio" name={radioName} checked={choice === 'db'} onChange={() => { edit(); setChoice('db'); setUnderstand(false) }} /> 使用数据库原始 JSON</label>{' '}
      <label><input type="radio" name={radioName} checked={choice === 'manual'} onChange={() => { edit(); setChoice('manual'); setUnderstand(false) }} /> 手工编辑最终 JSON（可从候选文档开始）</label>
    </fieldset>
    {choice === 'manual' && <label>最终 JSON<textarea aria-label="最终 JSON" className="block w-full border border-input bg-background" rows={12} value={manual} onChange={event => { edit(); setManual(event.target.value); setUnderstand(false) }} /></label>}
    {choice && <section aria-label="将保存的 YAML 预览"><h3>将保存的 YAML 预览（{review.workflowPath}）</h3>
      {yamlPreview === null ? <p role="alert">最终 JSON 无效或无法无损表示，无法预览。</p> : <pre className="max-h-96 overflow-auto whitespace-pre-wrap">{yamlPreview}</pre>}
      <p>以上为基于所选最终 JSON 的预览；保存仍须经过服务端校验和确认。</p></section>}
    <div><label><input type="checkbox" checked={readDb} onChange={event => { edit(); setReadDb(event.target.checked) }} /> 我已审阅数据库原始文档</label></div>
    <div><label><input type="checkbox" checked={readGit} onChange={event => { edit(); setReadGit(event.target.checked) }} /> 我已审阅 Git 原始 YAML</label></div>
    <div><label><input type="checkbox" checked={understand} onChange={event => { edit(); setUnderstand(event.target.checked) }} /> 我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成</label></div>
    {!currentReview && <p role="alert">审阅期间基线或草稿已变化，请取消并重新核对。</p>}
    {error && <p role="alert">{error}</p>}
    <div className="mt-3 flex gap-2"><button type="button" disabled={binary || unsafeDocument || !currentReview || !choice || !readDb || !readGit || !understand} onClick={confirm} className="rounded bg-primary px-3 py-2 text-primary-foreground">确认最终文档</button>
      <button type="button" onClick={() => { invalidateWorkflowReconciliation(review); onCancel() }} className="rounded border border-input px-3 py-2">取消</button></div>
    </fieldset>
  </section>
}

function FileSnapshot({ label, files }: { label: string; files: ReconciliationFile[] }) {
  return <section aria-label={label}><h4>{label}（{files.length}）</h4>
    {files.map(file => {
      const content = displayReconciliationFile(file)
      return <section key={file.path} aria-label={`${label}: ${file.path}`}>
        <h5>{file.path} — 模式 {file.mode}</h5>
        {content.kind === 'text' ? <pre className="max-h-64 overflow-auto whitespace-pre-wrap">{content.text}</pre>
          : <p>二进制或不可安全显示的内容；以下 base64 仅用于识别，不能视为内容已审阅。</p>}
        <details><summary>原始 base64</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{file.contentBase64}</pre></details>
      </section>
    })}
  </section>
}
