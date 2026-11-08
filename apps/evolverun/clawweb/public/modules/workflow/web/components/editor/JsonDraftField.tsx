import { useId, useState } from 'react'
import { useEditorStore } from '../../editor/store'
import { clearDraft, readDraft, writeDraft } from '../../editor/draftCache'

export interface JsonDraftFieldProps {
  label: string
  value: unknown
  onApply: (value: unknown) => void
  draftKey: string
  validate?: (value: unknown) => string | null
  description?: string
  rows?: number
  allowRemove?: boolean
  onRemove?: () => void
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function unsafeValue(value: unknown): boolean {
  if (typeof value === 'number') return !Number.isFinite(value)
  if (!value || typeof value !== 'object') return false
  return Object.entries(value).some(([key, entry]) =>
    ['__proto__', 'prototype', 'constructor'].includes(key) || unsafeValue(entry))
}

// JSON replacement is explicit, including nested deletion and replacing objects with scalars.
function removedPaths(before: unknown, after: unknown, path = '$'): string[] {
  if (!before || typeof before !== 'object') return []
  if (!after || typeof after !== 'object') return Object.keys(before).length ? [path] : []
  return Object.entries(before).flatMap(([key, value]) => {
    const nextPath = `${path}.${key}`
    return Object.hasOwn(after, key)
      ? removedPaths(value, (after as Record<string, unknown>)[key], nextPath)
      : [nextPath]
  })
}

const serialize = (value: unknown) => value === undefined ? '' : JSON.stringify(value, null, 2)
const buttonClass = 'rounded-md border border-input px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50'

export default function JsonDraftField(props: JsonDraftFieldProps) {
  const sessionId = useEditorStore(state => state.sessionId)
  return <Draft key={`${sessionId}:${props.draftKey}`} {...props} sessionId={sessionId} />
}

function Draft({ label, value, onApply, draftKey, validate, description, rows = 6, allowRemove, onRemove, sessionId }: JsonDraftFieldProps & { sessionId: number }) {
  const id = useId()
  const serialized = serialize(value)
  const owner = useEditorStore(state => state.spec?.id ?? null)
  const [draft, setDraft] = useState(() => {
    const cached = readDraft(sessionId, draftKey)
    return cached ? { ...cached, dirty: true } : {
      text: serialized, baseline: serialized, dirty: false,
      owner,
    }
  })
  const [applyError, setApplyError] = useState<string | null>(null)
  const setPendingEdit = useEditorStore(state => state.setPendingEdit)
  // Derive pristine text from props. Never replace dirty text on external updates.
  const text = draft.dirty ? draft.text : serialized
  const conflict = draft.dirty && (draft.baseline !== serialized || draft.owner !== owner)
  let parsed: unknown
  let error: string | null = null
  if (draft.dirty) {
    try {
      parsed = JSON.parse(text)
      error = unsafeValue(parsed) ? 'JSON 含有不安全的键或非有限数值，不能应用。' : validate?.(parsed) ?? null
    } catch (cause) {
      error = `JSON 无效：${cause instanceof Error ? cause.message : String(cause)}`
    }
  }
  const message = error || applyError || (conflict ? '原始配置已变化，草稿已保留；请复制需要的内容后放弃草稿，再重新编辑。' : null)

  function discard() {
    if (sessionId !== useEditorStore.getState().sessionId) return
    clearDraft(sessionId, draftKey)
    setDraft({ text: serialized, baseline: serialized, dirty: false, owner: useEditorStore.getState().spec?.id ?? null })
    setApplyError(null)
    setPendingEdit(draftKey, null)
  }

  function blockedByOtherDrafts() {
    // Read at action time: another widget may have registered a draft since render.
    // Store actions return void on rejection, so do not rely on onApply throwing.
    const others = Object.entries(useEditorStore.getState().pendingEdits).filter(([key]) => key !== draftKey)
    if (!others.length) return false
    setApplyError(`还有其他未应用草稿，当前内容已保留。请先复制需要的内容并放弃其他草稿，再应用此项：${others.map(([, message]) => message).join('；')}`)
    return true
  }

  function apply() {
    if (sessionId !== useEditorStore.getState().sessionId || !draft.dirty || error || conflict || blockedByOtherDrafts()) return
    const removed = removedPaths(value, parsed)
    if (removed.length && !window.confirm(`应用将删除以下字段或内容：\n${removed.join('\n')}\n确认删除并应用？`)) return
    setPendingEdit(draftKey, null)
    try {
      onApply(parsed)
      clearDraft(sessionId, draftKey)
      setDraft({ ...draft, text, baseline: serialized, dirty: false })
      setApplyError(null)
    } catch (cause) {
      const failure = cause instanceof Error ? cause.message : String(cause)
      setApplyError(failure)
      setPendingEdit(draftKey, `${label} 未应用：${failure}`)
    }
  }

  function remove() {
    if (sessionId !== useEditorStore.getState().sessionId || draft.dirty || !onRemove || blockedByOtherDrafts() || !window.confirm(`确认删除「${label}」字段？`)) return
    try { onRemove(); setApplyError(null) }
    catch (cause) { setApplyError(cause instanceof Error ? cause.message : String(cause)) }
  }

  // Intentionally no unmount cleanup: a pending key must keep navigation/save guards active.
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-sm font-medium">{label}</label>
      <textarea id={id} value={text} rows={rows} spellCheck={false}
        aria-invalid={!!message} aria-describedby={`${id}-help${message ? ` ${id}-error` : ''}`}
        className="w-full rounded-md border border-input bg-background p-2 font-mono text-xs focus-visible:outline-primary"
        onChange={event => {
          if (sessionId !== useEditorStore.getState().sessionId) return
          const next = event.target.value
          const dirty = next !== serialized
          const updated = { text: next, baseline: draft.dirty ? draft.baseline : serialized, owner: draft.dirty ? draft.owner : owner, dirty }
          if (dirty) writeDraft(sessionId, draftKey, updated)
          else clearDraft(sessionId, draftKey)
          setDraft(updated)
          setApplyError(null)
          setPendingEdit(draftKey, dirty ? `${label} 有未应用的 JSON 草稿` : null)
        }} />
      <p id={`${id}-help`} className="text-xs text-muted-foreground">
        {description || '编辑不会立即写入；合法 JSON 需点击应用。删除字段会再次确认。'}
        {value === undefined && !draft.dirty ? ' 当前未配置。' : ''}
      </p>
      {message && <p id={`${id}-error`} role="alert" className="break-words text-xs text-destructive">{message}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" aria-label={`应用 ${label}`} onClick={apply} disabled={!draft.dirty || !!error || conflict}
          className={`${buttonClass} bg-primary text-primary-foreground`}>应用</button>
        <button type="button" aria-label={`放弃 ${label}`} onClick={discard} disabled={!draft.dirty}
          className={`${buttonClass} bg-background`}>放弃</button>
        {allowRemove && onRemove && <button type="button" aria-label={`删除 ${label}`} onClick={remove}
          disabled={draft.dirty || value === undefined} className={`${buttonClass} text-destructive`}>删除字段</button>}
      </div>
    </div>
  )
}
