import { useCallback, useEffect, useRef, useState } from 'react'
import { stringify } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, WorkflowValidationIssue } from '@avernet/clawweb-shared/web/types'
import { useEditorStore } from '../editor/store'
import { clearDraft, readDraft, writeDraft } from '../editor/draftCache'
import { normalizationChanges, parseWorkflowYaml } from '../editor/yamlDraft'

interface YamlEditorProps {
  spec: WorkflowSpec | null
  onImport: (spec: WorkflowSpec) => void
}
interface NormalizationReview {
  spec: WorkflowSpec
  paths: string[]
  revision: number
}

const serialize = (spec: WorkflowSpec | null) => spec ? stringify(spec, { lineWidth: 0 }) : ''

export default function YamlEditor({ spec, onImport }: YamlEditorProps) {
  const { setPendingEdit, sessionId } = useEditorStore()
  const [restored] = useState(() => {
    const draft = readDraft(sessionId, 'yaml')
    let error: string | null = null
    if (draft) {
      try { parseWorkflowYaml(draft.text) }
      catch (reason) { error = reason instanceof Error ? reason.message : 'YAML 语法错误' }
    }
    return { draft, error }
  })
  const [text, setText] = useState(() => restored.draft?.text ?? serialize(spec))
  const [dirty, setDirty] = useState(!!restored.draft)
  const [error, setError] = useState<string | null>(restored.error)
  const [conflict, setConflict] = useState(() => !!restored.draft && restored.draft.baseline !== serialize(spec))
  const [localInvalid, setLocalInvalid] = useState(!!restored.error)
  const [validating, setValidating] = useState(false)
  const [issues, setIssues] = useState<WorkflowValidationIssue[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [review, setReview] = useState<NormalizationReview | null>(null)
  const dirtyRef = useRef(!!restored.draft)
  const revision = useRef(0)
  const mounted = useRef(true)
  const owner = useRef(restored.draft ? restored.draft.owner : spec?.id ?? null)
  const baseline = useRef(restored.draft?.baseline ?? serialize(spec))
  const session = useRef(sessionId)
  const previousSpec = useRef(spec)
  const currentSpec = useRef(spec)
  currentSpec.current = spec

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; revision.current++ }
  }, [])

  useEffect(() => {
    const sessionChanged = session.current !== sessionId
    if (!sessionChanged && previousSpec.current === spec) return
    session.current = sessionId
    previousSpec.current = spec
    if (sessionChanged) {
      // A confirmed discard/new load starts a different editor lifetime, even
      // when a cached workflow has the same ID. View changes do not reach here.
      revision.current++
      dirtyRef.current = false
      setDirty(false)
      setLocalInvalid(false)
      setValidating(false)
      setIssues([])
      setNotice(null)
    } else if (dirtyRef.current) {
      setConflict(true)
      revision.current++
      setValidating(false)
      setReview(null)
      return
    }
    owner.current = spec?.id ?? null
    baseline.current = serialize(spec)
    setText(baseline.current)
    setError(null)
    setConflict(false)
    setReview(null)
  }, [spec, sessionId])

  const changeText = useCallback((value: string) => {
    if (session.current !== useEditorStore.getState().sessionId) return
    revision.current++
    dirtyRef.current = true
    writeDraft(session.current, 'yaml', { text: value, baseline: baseline.current, owner: owner.current })
    setText(value)
    setDirty(true)
    setNotice(null)
    setIssues([])
    setReview(null)
    setValidating(false)
    setPendingEdit('yaml', 'YAML 有未应用的修改，请先校验并应用或放弃草稿')
    try { parseWorkflowYaml(value); setError(null); setLocalInvalid(false) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'YAML 语法错误'); setLocalInvalid(true) }
  }, [setPendingEdit])

  const applySpec = useCallback((value: WorkflowSpec) => {
    if (session.current !== useEditorStore.getState().sessionId) return
    const otherDrafts = Object.keys(useEditorStore.getState().pendingEdits ?? {}).filter(key => key !== 'yaml')
    if (otherDrafts.length) { setError('请先应用或放弃其他配置草稿，再应用 YAML'); return }
    setPendingEdit('yaml', null)
    try {
      onImport(value)
    } catch (reason) {
      setPendingEdit('yaml', 'YAML 尚未应用，请修正错误后重试或放弃草稿')
      setError(reason instanceof Error ? reason.message : 'YAML 应用失败')
      return
    }
    clearDraft(session.current, 'yaml')
    dirtyRef.current = false
    setDirty(false)
    setLocalInvalid(false)
    setConflict(false)
    setReview(null)
    setIssues([])
    setError(null)
    owner.current = value.id
    baseline.current = serialize(value)
    setText(baseline.current)
    setNotice(`已应用 ${value.nodes.length} 个节点的配置。尚未保存到服务器。`)
  }, [onImport, setPendingEdit])

  const apply = useCallback(async () => {
    if (!dirtyRef.current || validating || session.current !== useEditorStore.getState().sessionId) return
    if (owner.current !== currentSpec.current?.id) {
      setError('工作流已切换，请先放弃旧工作流的 YAML 草稿，避免覆盖其他工作流')
      return
    }
    let parsed: WorkflowSpec
    try { parsed = parseWorkflowYaml(text) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'YAML 语法错误'); return }
    const requestRevision = revision.current
    const requestOwner = currentSpec.current?.id
    const requestSession = session.current
    // Read the live store, not only a render/effect ref: reset + same-ID reload
    // can happen in one batch before React observes the new props/session.
    const isCurrentRequest = () => mounted.current && requestRevision === revision.current
      && requestSession === useEditorStore.getState().sessionId && currentSpec.current?.id === requestOwner
    setValidating(true)
    setIssues([])
    setNotice(null)
    setReview(null)
    try {
      const result = await api.workflows.validate(parsed)
      if (!isCurrentRequest()) return
      if (!result.valid) {
        setIssues(result.issues ?? [])
        setError('服务端校验未通过，请修正后重试')
        return
      }
      const normalized = result.normalizedSpec
      const paths = normalized ? normalizationChanges(parsed, normalized) : []
      if (normalized && paths.length) {
        setReview({ spec: normalized, paths, revision: requestRevision })
      } else {
        applySpec(parsed)
      }
    } catch (reason) {
      if (isCurrentRequest()) {
        setError(reason instanceof Error ? reason.message : '校验请求失败')
      }
    } finally {
      if (isCurrentRequest()) setValidating(false)
    }
  }, [text, validating, applySpec])

  const discard = () => {
    if (session.current !== useEditorStore.getState().sessionId) return
    revision.current++
    clearDraft(session.current, 'yaml')
    dirtyRef.current = false
    setDirty(false)
    setLocalInvalid(false)
    setValidating(false)
    setConflict(false)
    setError(null)
    setIssues([])
    setNotice(null)
    setReview(null)
    owner.current = spec?.id ?? null
    baseline.current = serialize(spec)
    setText(baseline.current)
    setPendingEdit('yaml', null)
  }

  return <section className="flex h-full min-h-0 flex-col bg-background" aria-label="YAML 编辑器">
    <header className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-border bg-card px-4 py-2">
      <div className="text-xs text-muted-foreground">YAML 配置 {dirty && <span className="ml-2 text-warning">未应用草稿</span>}</div>
      <div className="flex gap-2">
        {dirty && <button type="button" onClick={discard} className="rounded-md border border-input px-2 py-1 text-xs text-muted-foreground">放弃 YAML 草稿</button>}
        <button type="button" onClick={() => void apply()} disabled={!spec || !dirty || localInvalid || validating}
          className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground disabled:opacity-40">
          {validating ? '校验中…' : '校验并应用'}
        </button>
      </div>
    </header>
    {conflict && <p className="border-b border-border bg-warning/10 px-4 py-2 text-xs text-warning-foreground">画布配置已变化，当前 YAML 草稿已保留。请核对后应用，或放弃草稿以同步画布。</p>}
    {error && <div role="alert" className="border-b border-border bg-destructive/10 px-4 py-2 text-xs text-destructive">
      <p>{error}</p>
      {issues.length > 0 && <ul className="mt-1 list-inside list-disc">{issues.map((issue, index) => <li key={`${issue.path}-${index}`}>{issue.path ? `${issue.path}：` : ''}{issue.message}</li>)}</ul>}
    </div>}
    {notice && <p role="status" className="border-b border-border bg-success/10 px-4 py-2 text-xs text-success">{notice}</p>}
    {review && <div className="max-h-52 overflow-auto border-b border-border bg-warning/10 px-4 py-3 text-xs">
      <p className="font-medium">服务端规范化将改变 {review.paths.length} 项字段，尚未应用。</p>
      <p className="mt-1 text-muted-foreground">包含字段增删或类型变化。保留原文并返回编辑，或明确采用这些变更。</p>
      <ul className="my-2 list-inside list-disc font-mono">{review.paths.map(path => <li key={path}>{path}</li>)}</ul>
      <button type="button" onClick={() => {
        if (revision.current === review.revision) applySpec(review.spec)
      }} className="rounded-md border border-input bg-card px-2 py-1">确认采用规范化结果</button>
    </div>}
    <textarea aria-label="工作流 YAML" value={text} onChange={event => changeText(event.target.value)} spellCheck={false}
      disabled={!spec}
      onKeyDown={event => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); void apply() }
        if (event.key === 'Tab') {
          event.preventDefault()
          const input = event.currentTarget
          const start = input.selectionStart
          changeText(text.slice(0, start) + '  ' + text.slice(input.selectionEnd))
          requestAnimationFrame(() => { if (input.isConnected) input.setSelectionRange(start + 2, start + 2) })
        }
      }}
      className="min-h-0 flex-1 resize-none bg-muted/30 p-4 font-mono text-xs leading-6 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" />
    <footer className="border-t border-border px-4 py-2 text-[10px] text-muted-foreground">先应用到编辑器，再保存到服务器。数据库 JSON 无法恢复原始 YAML 注释和排版。</footer>
  </section>
}
