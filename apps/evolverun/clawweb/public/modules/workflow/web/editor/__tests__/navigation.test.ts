import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mock = vi.hoisted(() => ({ isDirty: false, pendingEdits: {} as Record<string, string>, reset: vi.fn(), setPendingEdit: vi.fn() }))
vi.mock('../store', () => ({ useEditorStore: { getState: () => mock } }))
import { confirmDiscardEditor } from '../navigation'
beforeEach(() => { mock.isDirty = false; mock.pendingEdits = {}; vi.clearAllMocks() })
afterEach(() => vi.restoreAllMocks())
describe('explicit workspace navigation', () => {
  it('does not prompt for a clean document', () => {
    const confirm = vi.spyOn(window, 'confirm')
    expect(confirmDiscardEditor()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
    expect(mock.reset).not.toHaveBeenCalled()
  })
  it('preserves unapplied drafts when the user cancels leaving', () => {
    mock.pendingEdits = { yaml: 'pending' }
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    expect(confirmDiscardEditor()).toBe(false)
    expect(mock.reset).not.toHaveBeenCalled()
    expect(mock.setPendingEdit).not.toHaveBeenCalled()
  })
  it('only clears guards and resets after explicit confirmation', () => {
    mock.isDirty = true; mock.pendingEdits = { yaml: 'pending' }
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    expect(confirmDiscardEditor()).toBe(true)
    expect(mock.setPendingEdit).toHaveBeenCalledWith('yaml', null)
    expect(mock.reset).toHaveBeenCalledExactlyOnceWith({ force: true })
  })
})
