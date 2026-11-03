import { useEditorStore } from './store'

/** Unapplied source text only: never part of the workflow spec or persistent storage. */
export interface CachedDraft {
  readonly text: string
  readonly baseline: string
  readonly owner: string | null
}

const drafts = new Map<string, CachedDraft>()

// The subscription outlives the widgets, so a confirmed reset/load while away
// also releases old text. Pending removal alone is not a discard signal: apply
// temporarily clears its key before a consumer can reject the write.
const unsubscribe = useEditorStore.subscribe((state, previous) => {
  if (state.sessionId !== previous.sessionId) drafts.clear()
})
if (import.meta.hot) import.meta.hot.dispose(unsubscribe)

export function readDraft(sessionId: number, key: string): CachedDraft | undefined {
  return sessionId === useEditorStore.getState().sessionId ? drafts.get(key) : undefined
}

export function writeDraft(sessionId: number, key: string, draft: CachedDraft): void {
  if (sessionId !== useEditorStore.getState().sessionId) return
  const { text, baseline, owner } = draft
  drafts.set(key, Object.freeze({ text, baseline, owner }))
}

export function clearDraft(sessionId: number, key: string): void {
  if (sessionId === useEditorStore.getState().sessionId) drafts.delete(key)
}
