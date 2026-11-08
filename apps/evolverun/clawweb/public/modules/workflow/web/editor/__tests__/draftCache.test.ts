import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { clearDraft, readDraft, writeDraft } from '../draftCache'
import { useEditorStore } from '../store'

const state = () => useEditorStore.getState()
const draft = { text: '# unparsed\n[', baseline: 'original source', owner: 'workflow-id' }
beforeEach(() => state().reset({ force: true }))
afterEach(() => state().reset({ force: true }))

describe('session-local in-memory draft cache', () => {
  it('retains exact source and metadata by key, with no change to spec or pending', () => {
    const { sessionId, spec, pendingEdits } = state()
    const source = { ...draft }
    writeDraft(sessionId, 'yaml', source)
    source.text = 'mutated outside cache'
    writeDraft(sessionId, 'workflow:input', { ...draft, text: '{broken' })
    expect(readDraft(sessionId, 'yaml')).toEqual(draft)
    expect(Object.isFrozen(readDraft(sessionId, 'yaml'))).toBe(true)
    expect(readDraft(sessionId, 'workflow:input')?.text).toBe('{broken')
    expect(state().spec).toBe(spec)
    expect(state().pendingEdits).toBe(pendingEdits)
    clearDraft(sessionId, 'yaml')
    expect(readDraft(sessionId, 'yaml')).toBeUndefined()
    expect(readDraft(sessionId, 'workflow:input')?.text).toBe('{broken')
  })

  it('clears immediately on session changes without any mounted components', () => {
    const oldSession = state().sessionId
    writeDraft(oldSession, 'yaml', draft)
    state().reset({ force: true })
    expect(readDraft(state().sessionId, 'yaml')).toBeUndefined()
    expect(readDraft(oldSession, 'yaml')).toBeUndefined()
    // Prove eviction rather than merely hiding the old entry behind a session check.
    useEditorStore.setState({ sessionId: oldSession })
    expect(readDraft(oldSession, 'yaml')).toBeUndefined()
  })

  it('does not let stale-session readers, writers or clearers affect the current draft', () => {
    const oldSession = state().sessionId
    state().reset({ force: true })
    const current = state().sessionId
    writeDraft(current, 'yaml', draft)
    writeDraft(oldSession, 'yaml', { ...draft, text: 'stale' })
    clearDraft(oldSession, 'yaml')
    expect(readDraft(oldSession, 'yaml')).toBeUndefined()
    expect(readDraft(current, 'yaml')).toEqual(draft)
  })

  it('keeps text during temporary pending removal so rejected apply is recoverable', () => {
    const { sessionId } = state()
    writeDraft(sessionId, 'yaml', draft)
    state().setPendingEdit('yaml', 'unapplied')
    state().setPendingEdit('yaml', null)
    expect(readDraft(sessionId, 'yaml')).toEqual(draft)
    state().setPendingEdit('yaml', 'apply rejected')
    expect(readDraft(sessionId, 'yaml')).toEqual(draft)
  })
})
