import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ getAccess: vi.fn(), get: vi.fn(), documents: new Map<string, unknown>(), history: vi.fn(), snapshot: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: (id: string) => ({ data: id ? { id, title: id === 'a' ? 'Original' : 'Target', version: '1', nodes: [] } : undefined, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: async (id: string) => {
  const value = await mocks.get(id); mocks.documents.set(id, value); return value
}, getAccess: mocks.getAccess, getHistory: mocks.history, getDeploySnapshot: mocks.snapshot } }, fetchJson: (url: string) => {
  const match = url.match(/^\/api\/workflows\/([^/]+)\/editor-draft$/)
  if (!match) return new Promise(() => {})
  const id = decodeURIComponent(match[1])
  return Promise.resolve({ workflowId: id, packId: 'pack', specJson: JSON.stringify(mocks.documents.get(id)), draftToken: 'a'.repeat(64) })
} }))
vi.mock('@xyflow/react', () => ({ ReactFlowProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: ({ selectedWorkflowId, onSelectWorkflow }: { selectedWorkflowId: string; onSelectWorkflow: (id: string) => void }) => <><span data-testid="selection">{selectedWorkflowId}</span><button onClick={() => onSelectWorkflow('b')}>选择 B</button><button onClick={() => onSelectWorkflow('c')}>选择 C</button></> }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
vi.mock('../../components/ScriptFilesTab', () => ({ default: () => null }))
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { WorkspacePopPendingContext } from '../../editor/usePackNavigationGuard'
import { readDraft, writeDraft } from '../../editor/draftCache'

const A: WorkflowSpec = { id: 'a', title: 'Original', version: '1', nodes: [] }
const B: WorkflowSpec = { id: 'b', title: 'Target', version: '1', nodes: [] }
const state = () => useEditorStore.getState()
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function mount() { render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} initialWorkflowId="a" /></MemoryRouter>) }
function makeDraft() {
  state().updateSpecField('title', 'Unsaved')
  const session = state().sessionId
  writeDraft(session, 'yaml', { text: 'unapplied', baseline: 'original', owner: 'a' })
  state().setPendingEdit('yaml', 'draft')
  return session
}
beforeEach(() => {
  useEditorStore.setState(useEditorStore.getInitialState(), true)
  state().loadSpec(A)
  vi.clearAllMocks(); mocks.documents.clear()
  mocks.history.mockReturnValue(new Promise(() => {}))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe('standalone editor workflow transition', () => {
  it('leaves selection and full draft unchanged when confirmation is cancelled', () => {
    const session = makeDraft(); vi.mocked(window.confirm).mockReturnValue(false); mount()
    fireEvent.click(screen.getByText('选择 B'))
    expect(window.confirm).toHaveBeenCalledTimes(1); expect(mocks.get).not.toHaveBeenCalled()
    expect(state().sessionId).toBe(session); expect(state().spec?.title).toBe('Unsaved')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied'); expect(screen.getByTestId('selection')).toHaveTextContent('a')
  })
  it('does not discard while target is loading or when loading fails', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount()
    fireEvent.click(screen.getByText('选择 B'))
    expect(state().sessionId).toBe(session); expect(screen.getByTestId('selection')).toHaveTextContent('a')
    await act(async () => { target.reject(new Error('offline')); await target.promise.catch(() => {}) })
    expect(await screen.findByText(/工作流读取失败/)).toBeInTheDocument()
    expect(state().spec?.title).toBe('Unsaved'); expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
    expect(screen.getByTestId('selection')).toHaveTextContent('a'); expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('commits valid target and selection only after it arrives, with one confirmation', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount()
    fireEvent.click(screen.getByText('选择 B'))
    expect(state().sessionId).toBe(session)
    await act(async () => { target.resolve(B); await target.promise })
    expect(state().spec).toEqual(B); expect(state()._databaseSpec).toEqual(B); expect(state().pendingEdits).toEqual({})
    expect(screen.getByTestId('selection')).toHaveTextContent('b')
    expect(readDraft(session, 'yaml')).toBeUndefined(); expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('rejects invalid responses and approvals invalidated during a read', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount()
    fireEvent.click(screen.getByText('选择 B'))
    act(() => state().setPendingEdit('node:a:raw', 'late'))
    await act(async () => { target.resolve(B); await target.promise })
    expect(state().sessionId).toBe(session); expect(state().pendingEdits).toHaveProperty('node:a:raw')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied'); expect(screen.getByTestId('selection')).toHaveTextContent('a')
    mocks.get.mockResolvedValueOnce({ ...B, id: 'wrong' })
    fireEvent.click(screen.getByText('选择 B'))
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2))
    expect(state().sessionId).toBe(session); expect(screen.getByTestId('selection')).toHaveTextContent('a')
  })
  it('refuses a structurally malformed target without clearing the existing session', async () => {
    const session = makeDraft(); mocks.get.mockResolvedValueOnce({ ...B, title: undefined }); mount()
    fireEvent.click(screen.getByText('选择 B'))
    expect(await screen.findByText(/工作流读取失败.*必须包含非空 id、title，以及 nodes 数组/)).toBeInTheDocument()
    expect(state().sessionId).toBe(session); expect(state().spec?.title).toBe('Unsaved')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied'); expect(screen.getByTestId('selection')).toHaveTextContent('a')
  })
  it('does not discard when cancelling a local new-workflow confirmation', () => {
    const session = makeDraft(); vi.mocked(window.confirm).mockReturnValue(false); mount()
    fireEvent.click(screen.getByRole('button', { name: '更多操作' })); fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
    const inputs = screen.getAllByPlaceholderText('我的工作流')
    fireEvent.change(inputs[0], { target: { value: 'new' } }); fireEvent.change(inputs[1], { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(state().sessionId).toBe(session); expect(state().spec?.title).toBe('Unsaved')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied'); expect(screen.getByTestId('selection')).toHaveTextContent('a')
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('creates a new local draft after one confirmation without writing the server', () => {
    const session = makeDraft(); mount()
    fireEvent.click(screen.getByRole('button', { name: '更多操作' })); fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
    expect(state().sessionId).toBe(session)
    const inputs = screen.getAllByPlaceholderText('我的工作流')
    fireEvent.change(inputs[0], { target: { value: 'new' } }); fireEvent.change(inputs[1], { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(state().spec).toMatchObject({ id: 'new', title: 'New', nodes: [{ id: 'new_start', title: '开始', phase: 'execute', executor: { type: 'done' } }] })
    expect(state().isDirty).toBe(true); expect(state().sessionId).toBe(session + 1)
    expect(readDraft(session, 'yaml')).toBeUndefined(); expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(mocks.get).not.toHaveBeenCalled()
  })
  it('ignores an older response when another selection has started', async () => {
    const session = makeDraft(); const b = deferred<WorkflowSpec>(); const c = deferred<WorkflowSpec>()
    mocks.get.mockReturnValueOnce(b.promise).mockReturnValueOnce(c.promise); mount()
    fireEvent.click(screen.getByText('选择 B')); fireEvent.click(screen.getByText('选择 C'))
    await act(async () => { b.resolve(B); await b.promise })
    expect(state().sessionId).toBe(session); expect(screen.getByTestId('selection')).toHaveTextContent('a')
    await act(async () => { c.resolve({ id: 'c', title: 'Target', version: '1', nodes: [] }); await c.promise })
    expect(state().spec?.id).toBe('c'); expect(screen.getByTestId('selection')).toHaveTextContent('c')
  })

})


describe('embedded editor controlled target', () => {
  it('defers an early Router POP render until workspace has resolved its transaction', () => {
    const session = makeDraft()
    const view = render(<MemoryRouter><WorkspacePopPendingContext.Provider value={false}><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded workspaceManagedPop initialWorkflowId="a" /></WorkspacePopPendingContext.Provider></MemoryRouter>)
    // Router renders B before the native POP listener has claimed the event.
    view.rerender(<MemoryRouter><WorkspacePopPendingContext.Provider value><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded workspaceManagedPop initialWorkflowId="b" /></WorkspacePopPendingContext.Provider></MemoryRouter>)
    expect(window.confirm).not.toHaveBeenCalled()
    expect(mocks.get).not.toHaveBeenCalled()
    expect(state().sessionId).toBe(session)
    // Cancellation restores A; there is no second confirmation or target read.
    view.rerender(<MemoryRouter><WorkspacePopPendingContext.Provider value={false}><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded workspaceManagedPop initialWorkflowId="a" /></WorkspacePopPendingContext.Provider></MemoryRouter>)
    expect(window.confirm).not.toHaveBeenCalled()
    expect(state().spec?.title).toBe('Unsaved')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
  })

  it('keeps the old draft when a controlled target is cancelled', () => {
    const session = makeDraft(); vi.mocked(window.confirm).mockReturnValue(false)
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    expect(window.confirm).toHaveBeenCalledTimes(1); expect(mocks.get).not.toHaveBeenCalled()
    expect(state().sessionId).toBe(session); expect(state().spec?.id).toBe('a')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
  })
  it('keeps the old draft on a controlled target read failure', async () => {
    const session = makeDraft(); mocks.get.mockRejectedValueOnce(new Error('offline'))
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    expect(await screen.findByText(/工作流读取失败/)).toBeInTheDocument()
    expect(window.confirm).toHaveBeenCalledTimes(1); expect(state().sessionId).toBe(session)
    expect(state().spec?.id).toBe('a'); expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
  })
  it.each(['a', null, undefined] as const)('ignores a late B response after the controlled target changes to %s', async next => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise)
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    expect(mocks.get).toHaveBeenCalledOnce()
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId={next} /></MemoryRouter>)
    await act(async () => { target.resolve(B); await target.promise })
    expect(state().sessionId).toBe(session); expect(state().spec?.id).toBe('a')
    expect(state().spec?.title).toBe('Unsaved'); expect(state().pendingEdits).toHaveProperty('yaml')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('ignores an obsolete B failure after the controlled target returns to A', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise)
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    await act(async () => { target.reject(new Error('obsolete')); await target.promise.catch(() => {}) })
    expect(screen.queryByText(/工作流读取失败/)).not.toBeInTheDocument()
    expect(state().sessionId).toBe(session); expect(state().spec?.id).toBe('a')
    expect(readDraft(session, 'yaml')?.text).toBe('unapplied')
  })
  it('commits a controlled target only after loading, without a second prompt', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise)
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    expect(state().sessionId).toBe(session)
    await act(async () => { target.resolve(B); await target.promise })
    expect(state().spec).toEqual(B); expect(state()._databaseSpec).toEqual(B); expect(readDraft(session, 'yaml')).toBeUndefined()
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('passes the active fallback and its absent DB draft through a controlled switch', async () => {
    mocks.get.mockRejectedValue({ status: 404, body: '{"code":"WORKFLOW_NOT_FOUND"}' })
    mocks.history.mockResolvedValue({ workflowId: 'b', history: [], active: { deployNumber: 2, version: 1, isActive: true, action: 'rollback' } })
    mocks.snapshot.mockResolvedValue({ workflowId: 'b', deployNumber: 2, version: 1, specJson: JSON.stringify(B) })
    const view = render(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="a" /></MemoryRouter>)
    view.rerender(<MemoryRouter><Editor access={{ canView: true, canEdit: true, canDeploy: true }} embedded initialWorkflowId="b" /></MemoryRouter>)
    await waitFor(() => expect(state().spec).toEqual(B))
    expect(state()._databaseSpec).toBeNull()
    expect(mocks.snapshot).toHaveBeenCalledWith('b', 2)
  })

})


describe('real permission queries during workflow navigation', () => {
  it.each([
    ['sidebar', false], ['sidebar', true], ['controlled', false], ['controlled', true],
  ] as const)('allows %s navigation while pending source permission settles to edit=%s', async (mode, canEdit) => {
    const permission = deferred<{ canView: boolean; canEdit: boolean; canExecute: boolean }>()
    const target = deferred<WorkflowSpec>()
    mocks.getAccess.mockImplementation((id: string) => id === 'a' ? permission.promise : Promise.resolve({ canView: true, canEdit: false, canExecute: false }))
    mocks.get.mockReturnValueOnce(target.promise)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><Editor initialWorkflowId={mode === 'controlled' ? 'b' : 'a'} /></MemoryRouter></QueryClientProvider>)
    if (mode === 'sidebar') fireEvent.click(screen.getByText('选择 B'))
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('b'))
    await act(async () => permission.resolve({ canView: true, canEdit, canExecute: false }))
    await waitFor(() => expect(state().editAccess?.canEdit).toBe(canEdit))
    await act(async () => target.resolve(B))
    await waitFor(() => expect(state().spec).toEqual(B))
    expect(screen.getByTestId('selection')).toHaveTextContent('b')
    expect(await screen.findByText(/当前工作流为只读/)).toBeVisible()
    expect(screen.queryByText(/工作流切换未完成/)).not.toBeInTheDocument()
    expect(window.confirm).not.toHaveBeenCalled()
    cleanup(); client.clear()
  })

  it('allows a view-only user to create and edit a local unsaved workflow under existing create policy', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
    client.setQueryData(['workflow-access', 'a'], { canView: true, canEdit: false, canDeploy: false })
    render(<QueryClientProvider client={client}><MemoryRouter><Editor initialWorkflowId="a" /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText(/当前工作流为只读/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '更多操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '新建工作流' }))
    const inputs = screen.getAllByPlaceholderText('我的工作流')
    fireEvent.change(inputs[0], { target: { value: 'local-new' } })
    fireEvent.change(inputs[1], { target: { value: 'New local workflow' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    act(() => state().updateSpecField('title', 'Editable local workflow'))
    expect(state().spec).toMatchObject({ id: 'local-new', title: 'Editable local workflow' })
    expect(state()._savedSpec).toBeNull()
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled()
    cleanup(); client.clear()
  })

  it('still rejects a switch if content changes after permission metadata settles', async () => {
    const permission = deferred<{ canView: boolean; canEdit: boolean; canExecute: boolean }>()
    const target = deferred<WorkflowSpec>()
    mocks.getAccess.mockReturnValue(permission.promise); mocks.get.mockReturnValueOnce(target.promise)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><Editor initialWorkflowId="a" /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByText('选择 B'))
    await act(async () => permission.resolve({ canView: true, canEdit: true, canExecute: false }))
    await waitFor(() => expect(state().editAccess?.canEdit).toBe(true))
    act(() => state().updateSpecField('title', 'Keep my concurrent edit'))
    await act(async () => target.resolve(B))
    expect(await screen.findByText(/工作流切换未完成/)).toBeVisible()
    expect(state().spec?.id).toBe('a')
    expect(state().spec?.title).toBe('Keep my concurrent edit')
    cleanup(); client.clear()
  })
})
