import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { BrowserRouter, Link, MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useEditorStore } from '../store'
import { claimWorkflowPop, releaseWorkflowPop, usePackNavigationGuard } from '../usePackNavigationGuard'
import { publish, releaseCleanSession, sessionFor, updateVersion } from '../packVersionSession'

function EditorPage() {
  usePackNavigationGuard()
  const navigate = useNavigate()
  return <><h1>画布编辑器</h1><Link to="/outside">离开</Link><Link replace to="/outside">替换</Link><button onClick={() => navigate(-1)}>后退</button></>
}
function App() {
  return <Routes><Route path="/editor" element={<EditorPage />} /><Route path="/outside" element={<h1>外部</h1>} /></Routes>
}
function mount() {
  return render(<MemoryRouter initialEntries={['/outside', '/editor']} initialIndex={1}><App /></MemoryRouter>)
}
const store = () => useEditorStore.getState()

beforeEach(() => {
  useEditorStore.setState(useEditorStore.getInitialState(), true)
  store().loadSpec({ id: 'navigation', title: 'Original', version: '1', nodes: [] })
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  vi.spyOn(window, 'alert').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useEditorStore.setState(useEditorStore.getInitialState(), true)
})

describe('canvas and Pack route guard', () => {
  it.each(['离开', '替换', '后退'])('cancels %s when an applied canvas change is dirty', action => {
    mount()
    act(() => store().updateSpecField('title', 'Unsaved'))
    fireEvent.click(screen.getByText(action))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('画布有未保存或未应用的修改'))
    expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
    expect(store().spec?.title).toBe('Unsaved')
    expect(store().isDirty).toBe(true)
  })

  it('checks live pending state even when the canvas spec is clean', () => {
    mount()
    act(() => store().setPendingEdit('yaml', 'unapplied source'))
    expect(store().isDirty).toBe(false)
    fireEvent.click(screen.getByText('离开'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(store().pendingEdits).toEqual({ yaml: 'unapplied source' })
    expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
  })

  it('allows a clean route change without prompting', () => {
    mount()
    fireEvent.click(screen.getByText('离开'))
    expect(window.confirm).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: '外部' })).toBeInTheDocument()
  })

  it('allows explicit approval without clearing the in-memory canvas', () => {
    vi.mocked(window.confirm).mockReturnValue(true)
    mount()
    act(() => { store().updateSpecField('title', 'Unsaved'); store().setPendingEdit('yaml', 'unapplied source') })
    fireEvent.click(screen.getByText('离开'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('heading', { name: '外部' })).toBeInTheDocument()
    expect(store().spec?.title).toBe('Unsaved')
    expect(store().pendingEdits).toEqual({ yaml: 'unapplied source' })
  })

  it('protects a position-only edit on navigation and unload', () => {
    act(() => store().loadSpec({ id: 'navigation', title: 'Original', version: '1', nodes: [{ id: 'a', title: 'A', phase: 'work', executor: { type: 'done' } }] }))
    mount()
    act(() => store().moveNode('a', 123, 456))
    expect(store().isDirty).toBe(false)
    expect(store().isLayoutDirty).toBe(true)
    fireEvent.click(screen.getByText('离开'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(store().positions.a).toEqual({ x: 123, y: 456 })
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    act(() => store().undo())
    expect(store().isLayoutDirty).toBe(false)
    act(() => store().redo())
    expect(store().isLayoutDirty).toBe(true)
  })

  it.each(['spec', 'pending'])('still warns on unload after SPA leave with %s edits', kind => {
    vi.mocked(window.confirm).mockReturnValue(true)
    mount()
    act(() => kind === 'spec' ? store().updateSpecField('title', 'Unsaved') : store().setPendingEdit('yaml', 'draft'))
    fireEvent.click(screen.getByText('离开'))
    expect(screen.getByRole('heading', { name: '外部' })).toBeInTheDocument()
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
  })

  it('reports canvas and Pack risks together and keeps both when cancelled', () => {
    const id = 'navigation-combined'
    const session = sessionFor(id)
    const file = { path: 'workflows/flow.yaml', contentBase64: 'b2xk', mode: '100644' as const }
    const snapshot = { workflowId: id, packId: id, baseRef: `refs/tags/deploy/${id}/#1`, baseCommit: 'a'.repeat(40), draftHead: null, versionFiles: [file] }
    updateVersion(session, 'current', { snapshot, files: [{ ...file, contentBase64: 'bmV3' }] })
    mount()
    act(() => store().updateSpecField('title', 'Unsaved'))
    fireEvent.click(screen.getByText('离开'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/画布有未保存或未应用的修改.*Pack 版本文件有未保存更改/))
    expect(store().spec?.title).toBe('Unsaved')
    expect(session.state.versions.current.files[0].contentBase64).toBe('bmV3')
    updateVersion(session, 'current', { files: [file] })
    releaseCleanSession(id, session)
  })

  it.each(['离开', '替换', '后退'])('protects %s while another POP transaction owns the native event', action => {
    const owner = claimWorkflowPop()
    try {
      mount()
      act(() => store().updateSpecField('title', 'Unsaved'))
      fireEvent.click(screen.getByText(action))
      expect(window.confirm).toHaveBeenCalledTimes(1)
      expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
      expect(store().spec?.title).toBe('Unsaved')
    } finally { releaseWorkflowPop(owner) }
  })

  it('blocks navigation during Pack upload even if the canvas is dirty and the user would approve', () => {
    const id = 'navigation-upload'
    const session = sessionFor(id)
    publish(session, { ...session.state, uploading: true })
    vi.mocked(window.confirm).mockReturnValue(true)
    mount()
    act(() => store().updateSpecField('title', 'Unsaved'))
    fireEvent.click(screen.getByText('离开'))
    expect(window.alert).toHaveBeenCalledTimes(1)
    expect(window.confirm).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
    publish(session, { ...session.state, uploading: false })
    releaseCleanSession(id, session)
  })

  it('rechecks risk when a programmatic browser back is followed by new edits', () => {
    const go = vi.spyOn(window.history, 'go').mockImplementation(() => {})
    window.history.replaceState({ idx: 4 }, '', '/editor')
    render(<BrowserRouter><App /></BrowserRouter>)
    fireEvent.click(screen.getByText('后退'))
    expect(go).toHaveBeenCalledWith(-1)
    expect(window.confirm).not.toHaveBeenCalled()
    act(() => store().updateSpecField('title', 'Late edit'))
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 3 } })))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(go).toHaveBeenCalledWith(1)
    expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
    expect(store().spec?.title).toBe('Late edit')
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 4 } })))
    window.history.replaceState(null, '', '/')
  })

  it('cancels native browser POP and restores its history index for a dirty canvas', () => {
    const go = vi.spyOn(window.history, 'go').mockImplementation(() => {})
    window.history.replaceState({ idx: 4 }, '', '/editor')
    render(<BrowserRouter><App /></BrowserRouter>)
    act(() => store().updateSpecField('title', 'Unsaved'))
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 3 } })))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(go).toHaveBeenCalledWith(1)
    expect(screen.getByRole('heading', { name: '画布编辑器' })).toBeInTheDocument()
    expect(store().spec?.title).toBe('Unsaved')
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 4 } })))
    window.history.replaceState(null, '', '/')
  })
})
