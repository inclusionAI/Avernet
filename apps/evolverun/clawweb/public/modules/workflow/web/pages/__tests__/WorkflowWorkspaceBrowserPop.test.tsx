import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ get: vi.fn(), fetchJson: vi.fn(), create: vi.fn(), refetch: vi.fn(), history: vi.fn(), snapshot: vi.fn(), rows: [] as Array<Record<string, unknown>> }))
vi.mock('../../api/hooks', () => ({
  useWorkflowTypes: () => ({ data: mocks.rows, isLoading: false, isError: false, error: null, refetch: mocks.refetch }),
  useCreateWorkflow: () => ({ mutateAsync: mocks.create, isPending: false }),
  useEditorWorkflowDocument: (id: string) => ({ data: id ? { id, title: id === 'a' ? 'Original' : 'Target', version: '1', nodes: [] } : undefined, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'user', isAdmin: false }) }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: mocks.get, getHistory: mocks.history, getDeploySnapshot: mocks.snapshot } }, fetchJson: mocks.fetchJson }))
vi.mock('@xyflow/react', () => ({ ReactFlowProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
vi.mock('../../components/workflow-workspace/OverviewTab', () => ({ default: () => <div>概览</div> }))
vi.mock('../../components/workflow-workspace/ManagementTab', () => ({ default: () => null }))
vi.mock('../../components/workflow-workspace/EvolutionTab', () => ({ default: () => null }))
vi.mock('../Dashboard', () => ({ default: () => null }))
import WorkflowWorkspace from '../WorkflowWorkspace'
import { useEditorStore } from '../../editor/store'
import { isWorkflowPopOwned } from '../../editor/usePackNavigationGuard'
import { publish, releaseCleanSession, sessionFor } from '../../editor/packVersionSession'
import { readDraft, writeDraft } from '../../editor/draftCache'

const A: WorkflowSpec = { id: 'a', title: 'Original', version: '1', nodes: [] }
const B: WorkflowSpec = { id: 'b', title: 'Target', version: '1', nodes: [] }
const row = (id: string, title: string) => ({ workflow_id: id, workflow_title: title, run_count: 0, last_status: 'pending' })
const state = () => useEditorStore.getState()
function OtherView() { const navigate = useNavigate(); return <button onClick={() => navigate('/workspace?workflowId=a&tab=overview')}>概览路由</button> }
function Location() { const location = useLocation(); return <output data-testid="location">{location.search}</output> }
function makeDraft() {
  state().updateSpecField('title', 'Unsaved')
  const session = state().sessionId
  writeDraft(session, 'yaml', { text: 'unapplied', baseline: 'original', owner: 'a' })
  state().setPendingEdit('yaml', 'draft')
  return session
}
function unchanged(session: number) {
  expect(state().sessionId).toBe(session)
  expect(state().spec?.title).toBe('Unsaved')
  expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
  expect(screen.getByTestId('location')).toHaveTextContent('workflowId=a')
  expect(screen.getByRole('button', { name: /切换工作流，当前：Original/ })).toBeInTheDocument()
}
beforeEach(() => {
  useEditorStore.setState(useEditorStore.getInitialState(), true); state().loadSpec(A)
  vi.resetAllMocks(); mocks.rows = [row('a', 'Original'), row('b', 'Target')]
  // Navigation reads the validated CAS row after workflows.get. Pack-loading
  // requests remain pending, as they are not part of these navigation stories.
  mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => {
    const match = /^\/api\/workflows\/([^/]+)\/editor-draft$/.exec(url)
    if (match && init?.method === 'GET') {
      const id = decodeURIComponent(match[1])
      const spec = id === 'a' ? A : B
      return Promise.resolve({ workflowId: id, packId: null, specJson: JSON.stringify(spec), draftToken: 'a'.repeat(64) })
    }
    return new Promise(() => {})
  })
  mocks.history.mockReturnValue(new Promise(() => {}))
  mocks.refetch.mockResolvedValue({ data: mocks.rows })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe('BrowserRouter real history workflow POP', () => {
  function mountBrowser() {
    window.history.replaceState({ idx: 0, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
    render(<BrowserRouter><WorkflowWorkspace /><Location /></BrowserRouter>)
  }

  it.each(['back', 'forward'] as const)('restores cancelled %s with one confirmation and keeps all drafts', async direction => {
    if (direction === 'forward') {
      window.history.replaceState({ idx: 0, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
      window.history.pushState({ idx: 1, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
      await new Promise<void>(resolve => { window.addEventListener('popstate', () => resolve(), { once: true }); window.history.back() })
    }
    const session = makeDraft()
    vi.mocked(window.confirm).mockReturnValue(false)
    const listeners = vi.spyOn(window, 'addEventListener')
    if (direction === 'back') mountBrowser()
    else render(<BrowserRouter><WorkflowWorkspace /><Location /></BrowserRouter>)
    // Workspace and Editor share one native dispatcher, regardless of effect order.
    expect(listeners.mock.calls.filter(([name, , options]) => name === 'popstate' && options === true)).toHaveLength(1)
    act(() => window.history[direction]())
    await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(window.location.search).toContain('workflowId=a'))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.get).not.toHaveBeenCalled()
    expect(window.history.state.key).toBe('source')
    unchanged(session)
  })

  it.each(['back', 'forward'] as const)('shows a %s target read failure after one approval and retains drafts', async direction => {
    if (direction === 'forward') {
      window.history.replaceState({ idx: 0, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
      window.history.pushState({ idx: 1, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
      await new Promise<void>(resolve => { window.addEventListener('popstate', () => resolve(), { once: true }); window.history.back() })
    }
    const session = makeDraft()
    mocks.get.mockRejectedValue(new Error('offline'))
    if (direction === 'back') mountBrowser()
    else render(<BrowserRouter><WorkflowWorkspace /><Location /></BrowserRouter>)
    act(() => window.history[direction]())
    expect(await screen.findByRole('alert')).toHaveTextContent('切换未完成，原草稿仍保留')
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(mocks.get).toHaveBeenCalledTimes(1)
    expect(mocks.get).toHaveBeenCalledWith('b')
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(window.location.search).toContain('workflowId=a')
    expect(window.history.state.key).toBe('source')
    unchanged(session)
  })
  it('commits Back and Forward without replacing either history entry', async () => {
    mocks.get.mockImplementation(async (id: string) => id === 'b' ? B : A)
    mountBrowser()
    // Intermediate restore POPs are consumed by the workspace. Observe the
    // replayed POP that reaches the router, not a transient history position.
    const committedPops = vi.fn()
    window.addEventListener('popstate', committedPops)
    try {
      act(() => window.history.back())
      await waitFor(() => expect(committedPops).toHaveBeenCalledTimes(1))
      expect(committedPops.mock.calls[0][0].state).toMatchObject({ idx: 0, key: 'target' })
      await waitFor(() => expect(state().spec?.id).toBe('b'))
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=b'))
      expect(isWorkflowPopOwned()).toBe(false)
      expect(window.history.state.idx).toBe(0)
      expect(screen.getByRole('button', { name: /切换工作流，当前：Target/ })).toBeInTheDocument()
      // The editor starts its Pack load in an effect after the POP commits.
      // Wait for that real request before starting the next independent POP:
      // starting it during the next approval would invalidate its revision.
      await waitFor(() => expect(mocks.fetchJson).toHaveBeenCalledWith(
        '/api/workflows/b/pack-version?version=draft', expect.objectContaining({ signal: expect.anything() }),
      ))
      expect(sessionFor('b').state.versions.draft.loading).toBe(true)
      act(() => window.history.forward())
      await waitFor(() => expect(committedPops).toHaveBeenCalledTimes(2))
      expect(committedPops.mock.calls[1][0].state).toMatchObject({ idx: 1, key: 'source' })
      await waitFor(() => expect(state().spec?.id).toBe('a'))
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=a'))
      expect(isWorkflowPopOwned()).toBe(false)
      expect(window.history.state.idx).toBe(1)
      expect(screen.getByRole('button', { name: /切换工作流，当前：Original/ })).toBeInTheDocument()
    } finally {
      window.removeEventListener('popstate', committedPops)
    }
  })


  it('keeps unload protection after the editor is unmounted', async () => {
    mountBrowser(); act(() => { makeDraft() })
    fireEvent.click(screen.getByRole('button', { name: '运行概览' }))
    await waitFor(() => expect(screen.getByText('概览')).toBeInTheDocument())
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(state().spec?.title).toBe('Unsaved')
  })
  it('still asks before an unrelated navigation while a workflow POP read is pending', async () => {
    let resolve!: (value: WorkflowSpec) => void
    mocks.get.mockReturnValue(new Promise<WorkflowSpec>(done => { resolve = done }))
    window.history.replaceState({ idx: 0, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
    render(<BrowserRouter><WorkflowWorkspace /><Location /><OtherView /></BrowserRouter>)
    act(() => { makeDraft() }); act(() => window.history.back())
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('b'))
    expect(isWorkflowPopOwned()).toBe(true)
    vi.mocked(window.confirm).mockClear().mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '概览路由' }))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('location')).toHaveTextContent('tab=editor')
    expect(state().spec?.title).toBe('Unsaved')
    await act(async () => { resolve(B) })
  })

  it('blocks an unrelated navigation when an upload starts while POP is loading', async () => {
    let resolve!: (value: WorkflowSpec) => void
    mocks.get.mockReturnValue(new Promise<WorkflowSpec>(done => { resolve = done }))
    window.history.replaceState({ idx: 0, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
    render(<BrowserRouter><WorkflowWorkspace /><Location /><OtherView /></BrowserRouter>)
    act(() => window.history.back())
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('b'))
    const session = sessionFor('pop-upload-test')
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    try {
      act(() => publish(session, { ...session.state, uploading: true }))
      fireEvent.click(screen.getByRole('button', { name: '概览路由' }))
      expect(alert).toHaveBeenCalledTimes(1)
      expect(screen.getByTestId('location')).toHaveTextContent('tab=editor')
    } finally {
      act(() => publish(session, { ...session.state, uploading: false }))
      releaseCleanSession('pop-upload-test', session)
      await act(async () => { resolve(B) })
    }
  })

  it('loads the workspace target when returning from a different route with an old editor session', async () => {
    mocks.get.mockImplementation(async (id: string) => id === 'b' ? B : A)
    window.history.replaceState({ idx: 0, key: 'workspace-target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'external' }, '', '/outside')
    render(<BrowserRouter><Routes><Route path="/workspace" element={<WorkflowWorkspace />} /><Route path="/outside" element={<p>外部页面</p>} /></Routes><Location /></BrowserRouter>)
    expect(screen.getByText('外部页面')).toBeInTheDocument()
    act(() => window.history.back())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=b'))
    await waitFor(() => expect(state().spec?.id).toBe('b'))
    expect(mocks.get).toHaveBeenCalledWith('b')
  })

  it('asks before replacing a dirty session when returning from outside on POP', async () => {
    mocks.get.mockImplementation(async (id: string) => id === 'b' ? B : A)
    const original = makeDraft()
    window.history.replaceState({ idx: 0, key: 'workspace-target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'external' }, '', '/outside')
    render(<BrowserRouter><Routes><Route path="/workspace" element={<WorkflowWorkspace />} /><Route path="/outside" element={<p>外部页面</p>} /></Routes><Location /></BrowserRouter>)
    act(() => window.history.back())
    await waitFor(() => expect(state().spec?.id).toBe('b'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(state().sessionId).not.toBe(original)
    expect(mocks.get).toHaveBeenCalledWith('b')
  })

  it('retains the original draft when declining the externally entered POP target', async () => {
    const original = makeDraft()
    vi.mocked(window.confirm).mockReturnValue(false)
    window.history.replaceState({ idx: 0, key: 'workspace-target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'external' }, '', '/outside')
    render(<BrowserRouter><Routes><Route path="/workspace" element={<WorkflowWorkspace />} /><Route path="/outside" element={<p>外部页面</p>} /></Routes><Location /></BrowserRouter>)
    act(() => window.history.back())
    await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(1))
    expect(mocks.get).not.toHaveBeenCalled()
    expect(state().sessionId).toBe(original)
    expect(state().spec?.title).toBe('Unsaved')
    expect(readDraft(original, 'yaml')?.text).toBe('unapplied')
  })

  it('releases POP ownership when another route supersedes a pending read', async () => {
    let resolve!: (value: WorkflowSpec) => void
    mocks.get.mockReturnValue(new Promise<WorkflowSpec>(done => { resolve = done }))
    window.history.replaceState({ idx: 0, key: 'target' }, '', '/workspace?workflowId=b&tab=editor')
    window.history.pushState({ idx: 1, key: 'source' }, '', '/workspace?workflowId=a&tab=editor')
    render(<BrowserRouter><WorkflowWorkspace /><Location /><OtherView /></BrowserRouter>)
    act(() => { makeDraft() }); act(() => window.history.back())
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('b'))
    expect(isWorkflowPopOwned()).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '概览路由' }))
    await waitFor(() => expect(screen.getByText('概览')).toBeInTheDocument())
    await act(async () => { resolve(B) })
    expect(isWorkflowPopOwned()).toBe(false)
    expect(state().spec?.title).toBe('Unsaved')
    vi.mocked(window.confirm).mockClear().mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '概览路由' }))
    expect(window.confirm).toHaveBeenCalled()
  })

  it('retains edits made while the target is loading and keeps history at the source', async () => {
    let resolve!: (value: WorkflowSpec) => void
    mocks.get.mockReturnValue(new Promise<WorkflowSpec>(done => { resolve = done }))
    mountBrowser()
    act(() => window.history.back())
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('b'))
    act(() => state().updateSpecField('title', 'Late edit'))
    await act(async () => { resolve(B) })
    expect(await screen.findByRole('alert')).toHaveTextContent('原草稿仍保留')
    expect(state().spec?.title).toBe('Late edit')
    expect(window.location.search).toContain('workflowId=a')
    expect(window.history.state.idx).toBe(1)
  })

  it('preserves a null DB baseline when committing an active-only browser POP target', async () => {
    mocks.get.mockRejectedValue({ status: 404, body: '{"code":"WORKFLOW_NOT_FOUND"}' })
    mocks.history.mockResolvedValue({ workflowId: 'b', history: [], active: { deployNumber: 2, version: 1, isActive: true, action: 'rollback' } })
    mocks.snapshot.mockResolvedValue({ workflowId: 'b', deployNumber: 2, version: 1, specJson: JSON.stringify(B) })
    mountBrowser(); act(() => window.history.back())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=b'))
    expect(state().spec).toEqual(B); expect(state()._databaseSpec).toBeNull()
    expect(mocks.snapshot).toHaveBeenCalledWith('b', 2)
  })

})
