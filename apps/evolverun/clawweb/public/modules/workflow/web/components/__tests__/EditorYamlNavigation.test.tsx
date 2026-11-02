import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ validate: vi.fn(), imported: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { validate: mocks.validate } } }))
import { useEditorStore } from '../../editor/store'
import { confirmDiscardEditor } from '../../editor/navigation'
import YamlEditor from '../YamlEditor'

const state = () => useEditorStore.getState()
const cachedA: WorkflowSpec = { id: 'a', title: 'Cached A', version: '1', nodes: [] }
const cachedB: WorkflowSpec = { id: 'b', title: 'Cached B', version: '1', nodes: [] }
const draftA = stringify({ ...cachedA, title: 'Discarded A draft', custom: false })
const nextDraftA = stringify({ ...cachedA, title: 'Current A draft', custom: 0 })
const editor = () => screen.getByLabelText('工作流 YAML')
const change = (text = draftA) => fireEvent.change(editor(), { target: { value: text } })

// Real store + real navigation + one persistently mounted YAML component. The
// cached objects are reused on navigation, as with a synchronous query cache hit.
function Workspace() {
  const { spec, importYaml } = useEditorStore()
  const [visual, setVisual] = useState(false)
  return <>
    <button onClick={() => { if (confirmDiscardEditor()) state().loadSpec(cachedA) }}>Open A</button>
    <button onClick={() => { if (confirmDiscardEditor()) state().loadSpec(cachedB) }}>Open B</button>
    <button onClick={() => {
      if (!confirmDiscardEditor()) return
      state().loadSpec(cachedB)
      state().loadSpec(cachedA)
    }}>Cached round trip</button>
    <button onClick={() => { confirmDiscardEditor() }}>Leave editor</button>
    <button onClick={() => { if (confirmDiscardEditor()) state().createNew('new', 'New workflow') }}>New workflow</button>
    <button onClick={() => setVisual(value => !value)}>Toggle view</button>
    <div hidden={visual}>
      <YamlEditor spec={spec} onImport={value => { mocks.imported(value); importYaml(value) }} />
    </div>
  </>
}

function mount() {
  state().loadSpec(cachedA)
  render(<Workspace />)
}

function pendingValidation() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: Error) => void
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  mocks.validate.mockReturnValueOnce(promise)
  return { resolve, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
  useEditorStore.setState(useEditorStore.getInitialState(), true)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useEditorStore.setState(useEditorStore.getInitialState(), true)
})

describe('persistent YAML editor across confirmed navigation', () => {
  it('never resurrects a discarded A draft after cached A → B → A', async () => {
    mount()
    const originalTextarea = editor()
    change()
    expect(state().pendingEdits).toHaveProperty('yaml')
    fireEvent.click(screen.getByRole('button', { name: 'Open B' }))
    fireEvent.click(screen.getByRole('button', { name: 'Open A' }))
    expect(editor()).toBe(originalTextarea)
    expect(state().spec).toEqual(cachedA)
    expect(state().pendingEdits).toEqual({})
    expect(editor()).toHaveValue(stringify(cachedA, { lineWidth: 0 }))
    expect(screen.queryByText('未应用草稿')).not.toBeInTheDocument()
    expect(screen.queryByText(/画布配置已变化/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    expect(mocks.validate).not.toHaveBeenCalled()

    // The new session owns its new text and can apply it normally.
    mocks.validate.mockResolvedValueOnce({ valid: true })
    change(nextDraftA)
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    await waitFor(() => expect(state().spec!.title).toBe('Current A draft'))
    expect(mocks.imported).toHaveBeenCalledTimes(1)
    expect(state().pendingEdits).toEqual({})
  })

  it('handles a batched cached A → B → A without needing an intermediate empty render', () => {
    mount(); change()
    fireEvent.click(screen.getByRole('button', { name: 'Cached round trip' }))
    expect(state().spec).toEqual(cachedA)
    expect(state().pendingEdits).toEqual({})
    expect(editor()).toHaveValue(stringify(cachedA, { lineWidth: 0 }))
    expect(screen.queryByText('未应用草稿')).not.toBeInTheDocument()
  })

  it('clears invalid text, local errors and draft state on confirmed reset even before another load', () => {
    mount(); change('null')
    expect(screen.getByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Leave editor' }))
    expect(state().spec).toBeNull()
    expect(state().pendingEdits).toEqual({})
    expect(editor()).toHaveValue('')
    expect(editor()).toBeDisabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('未应用草稿')).not.toBeInTheDocument()
    expect(screen.queryByText(/画布配置已变化/)).not.toBeInTheDocument()
  })

  it('clears the old draft when a confirmed reset is immediately followed by createNew', () => {
    mount(); change('null')
    fireEvent.click(screen.getByRole('button', { name: 'New workflow' }))
    expect(state().spec).toMatchObject({ id: 'new', nodes: [] })
    expect(editor()).toHaveValue(stringify(state().spec, { lineWidth: 0 }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(state().pendingEdits).toEqual({})
    change(stringify({ ...state().spec!, title: 'New draft' }))
    expect(screen.getByRole('button', { name: '校验并应用' })).toBeEnabled()
  })

  it('preserves a draft and its pending guard when navigation is cancelled', () => {
    mount(); change()
    vi.mocked(window.confirm).mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: 'Open B' }))
    expect(state().spec).toEqual(cachedA)
    expect(editor()).toHaveValue(draftA)
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(screen.getByText('未应用草稿')).toBeInTheDocument()
  })

  it('preserves a draft across ordinary hidden view toggles and same-ID save completion', () => {
    mount(); change()
    const pending = { ...state().pendingEdits }
    fireEvent.click(screen.getByRole('button', { name: 'Toggle view' }))
    act(() => { state().markClean(cachedA) })
    fireEvent.click(screen.getByRole('button', { name: 'Toggle view' }))
    expect(editor()).toHaveValue(draftA)
    expect(state().pendingEdits).toEqual(pending)
    expect(screen.getByText('未应用草稿')).toBeInTheDocument()
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('does not use an unconfirmed load or reset rejection as a discard signal', () => {
    mount(); change()
    act(() => { state().loadSpec(cachedB); state().createNew('new', 'New'); state().reset() })
    expect(state().spec).toEqual(cachedA)
    expect(editor()).toHaveValue(draftA)
    expect(state().pendingEdits).toHaveProperty('yaml')
  })

  it.each(['success', 'normalization', 'invalid', 'rejection'] as const)(
    'invalidates old-session validation %s before React can commit a same-ID replacement', async outcome => {
      mount(); change()
      const response = pendingValidation()
      fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
      expect(screen.getByRole('button', { name: '校验中…' })).toBeDisabled()
      await act(async () => {
        expect(confirmDiscardEditor()).toBe(true)
        state().loadSpec(cachedA)
        if (outcome === 'rejection') response.reject(new Error('Old session network failure'))
        else if (outcome === 'invalid') response.resolve({ valid: false, issues: [{ path: 'title', message: 'Old session failure' }] })
        else if (outcome === 'normalization') response.resolve({ valid: true, normalizedSpec: { ...cachedA, title: 'Old normalized title' } })
        else response.resolve({ valid: true })
        // Resolve while updates are batched, before a component effect can be
        // relied upon to invalidate the old request's revision or owner ID.
        await Promise.resolve()
        expect(mocks.imported).not.toHaveBeenCalled()
      })
      expect(state().spec).toEqual(cachedA)
      expect(editor()).toHaveValue(stringify(cachedA, { lineWidth: 0 }))
      expect(state().pendingEdits).toEqual({})
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
      expect(screen.queryByText(/服务端规范化将改变/)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: '校验并应用' })).toBeDisabled()
    },
  )

  it('clears an existing normalization review and never applies it in the next session', async () => {
    mount(); change()
    mocks.validate.mockResolvedValue({ valid: true, normalizedSpec: { ...cachedA, title: 'Normalized old draft' } })
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    await screen.findByText(/服务端规范化将改变/)
    fireEvent.click(screen.getByRole('button', { name: 'Cached round trip' }))
    expect(screen.queryByRole('button', { name: '确认采用规范化结果' })).not.toBeInTheDocument()
    expect(editor()).toHaveValue(stringify(cachedA, { lineWidth: 0 }))
    expect(mocks.imported).not.toHaveBeenCalled()
  })

  it('does not let an old rejected request cancel a new-session validation or its pending guard', async () => {
    mount(); change()
    const oldResponse = pendingValidation()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cached round trip' }))
    change(nextDraftA)
    const currentResponse = pendingValidation()
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    await act(async () => { oldResponse.reject(new Error('Old request error')) })
    expect(screen.getByRole('button', { name: '校验中…' })).toBeDisabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(editor()).toHaveValue(nextDraftA)
    expect(state().pendingEdits).toHaveProperty('yaml')
    await act(async () => { currentResponse.resolve({ valid: true }) })
    expect(state().spec!.title).toBe('Current A draft')
    expect(mocks.imported).toHaveBeenCalledTimes(1)
    expect(state().pendingEdits).toEqual({})
  })

  it('keeps another field pending guard and YAML text when applying within the same session', async () => {
    mount(); change()
    act(() => { state().setPendingEdit('node:a:args', 'Unapplied JSON') })
    mocks.validate.mockResolvedValue({ valid: true })
    fireEvent.click(screen.getByRole('button', { name: '校验并应用' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('其他配置草稿')
    expect(editor()).toHaveValue(draftA)
    expect(state().pendingEdits).toHaveProperty('node:a:args', 'Unapplied JSON')
    expect(state().pendingEdits).toHaveProperty('yaml')
    expect(mocks.imported).not.toHaveBeenCalled()
  })
})
