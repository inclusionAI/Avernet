import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
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
import { readDraft, writeDraft } from '../../editor/draftCache'
import { releaseCleanSession, sessionFor, updateVersion } from '../../editor/packVersionSession'

const A: WorkflowSpec = { id: 'a', title: 'Original', version: '1', nodes: [] }
const B: WorkflowSpec = { id: 'b', title: 'Target', version: '1', nodes: [] }
const row = (id: string, title: string) => ({ workflow_id: id, workflow_title: title, run_count: 0, last_status: 'pending' })
const state = () => useEditorStore.getState()
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function Location() { const location = useLocation(); return <output data-testid="location">{location.search}</output> }
function mount() { render(<MemoryRouter initialEntries={['/workspace?workflowId=a&tab=editor']}><WorkflowWorkspace /><Location /></MemoryRouter>) }
function makeDraft() {
  state().updateSpecField('title', 'Unsaved')
  const session = state().sessionId
  writeDraft(session, 'yaml', { text: 'unapplied', baseline: 'original', owner: 'a' })
  state().setPendingEdit('yaml', 'draft')
  return session
}
function chooseB() {
  fireEvent.click(screen.getByRole('button', { name: /切换工作流，当前：/ }))
  fireEvent.click(within(screen.getByRole('dialog', { name: '选择工作流' })).getByRole('button', { name: /Target/ }))
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
  // Source reads obtain the database document and token from the CAS row.
  mocks.fetchJson.mockImplementation((url: string, init?: RequestInit) => {
    const match = /^\/api\/workflows\/([^/]+)\/editor-draft$/.exec(url)
    if (match && init?.method === 'GET') {
      const id = decodeURIComponent(match[1])
      const spec = id === 'a' ? A : id === 'b' ? B : { id, title: 'New', version: '1', nodes: [] }
      return Promise.resolve({ workflowId: id, packId: null, specJson: JSON.stringify(spec), draftToken: 'a'.repeat(64) })
    }
    return new Promise(() => {})
  })
  mocks.history.mockReturnValue(new Promise(() => {}))
  mocks.refetch.mockResolvedValue({ data: mocks.rows })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); state().reset({ force: true }); vi.restoreAllMocks() })

describe('real workspace and embedded editor transitions', () => {
  it('does not navigate or clear the draft when selection confirmation is cancelled', () => {
    const session = makeDraft(); vi.mocked(window.confirm).mockReturnValue(false); mount(); chooseB()
    unchanged(session); expect(window.confirm).toHaveBeenCalledTimes(1); expect(mocks.get).not.toHaveBeenCalled()
  })
  it('keeps selection, URL and draft during a failed target read', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount(); chooseB()
    unchanged(session)
    await act(async () => { target.reject(new Error('offline')); await target.promise.catch(() => {}) })
    expect(await screen.findByRole('alert')).toHaveTextContent('切换未完成'); unchanged(session)
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('commits a fetched target and route after a single confirmation', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount(); chooseB()
    unchanged(session)
    await act(async () => { target.resolve(B); await target.promise })
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=b'))
    expect(state().spec).toEqual(B); expect(state()._databaseSpec).toEqual(B); expect(readDraft(session, 'yaml')).toBeUndefined()
    expect(screen.getByRole('button', { name: /切换工作流，当前：Target/ })).toBeInTheDocument()
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('rejects a stale approval or invalid target without navigating', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount(); chooseB()
    act(() => state().setPendingEdit('late', 'edit'))
    await act(async () => { target.resolve(B); await target.promise })
    expect(await screen.findByRole('alert')).toHaveTextContent('原草稿仍保留'); unchanged(session)
    expect(state().pendingEdits).toHaveProperty('late')
  })
  it('rejects a mismatched target before any route change', async () => {
    const session = makeDraft(); mocks.get.mockResolvedValueOnce({ ...B, id: 'wrong' }); mount(); chooseB()
    expect(await screen.findByRole('alert')).toHaveTextContent('工作流响应身份不匹配'); unchanged(session)
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('confirms canvas and Pack risk once and leaves the Pack draft in memory', async () => {
    const packId = 'workspace-combined-pack', pack = sessionFor(packId)
    const file = { path: 'workflows/a.yaml', contentBase64: 'b2xk', mode: '100644' as const }
    const snapshot = { workflowId: packId, packId, baseRef: `refs/tags/deploy/${packId}/#1`, baseCommit: 'a'.repeat(40), draftHead: null, versionFiles: [file] }
    updateVersion(pack, 'current', { snapshot, files: [{ ...file, contentBase64: 'bmV3' }] })
    makeDraft(); mocks.get.mockResolvedValueOnce(B); mount(); chooseB()
    await waitFor(() => expect(state().spec?.id).toBe('b'))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(pack.state.versions.current.files[0].contentBase64).toBe('bmV3')
    act(() => { updateVersion(pack, 'current', { files: [file] }); releaseCleanSession(packId, pack) })
  })
  it('restores the old URL if the final session commit refuses the target', async () => {
    useEditorStore.setState({ replaceSession: () => false })
    const session = makeDraft(); mocks.get.mockResolvedValueOnce(B); mount(); chooseB()
    expect(await screen.findByRole('alert')).toHaveTextContent('切换未完成')
    unchanged(session); expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('cancels a pending switch when the user reselects the current workflow', async () => {
    const session = makeDraft(); const target = deferred<WorkflowSpec>(); mocks.get.mockReturnValueOnce(target.promise); mount(); chooseB()
    fireEvent.click(screen.getByRole('button', { name: /切换工作流，当前：/ }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '选择工作流' })).getByRole('button', { name: /Original/ }))
    await act(async () => { target.resolve(B); await target.promise })
    unchanged(session)
  })
  it('opening and cancelling new workflow never clears the old draft', () => {
    const session = makeDraft(); mount()
    fireEvent.click(screen.getByRole('button', { name: '新建工作流' }))
    expect(window.confirm).not.toHaveBeenCalled(); unchanged(session)
    fireEvent.click(screen.getByRole('button', { name: '关闭' })); unchanged(session)
  })
  it('cancels before creating and preserves the selected draft', async () => {
    const session = makeDraft(); vi.mocked(window.confirm).mockReturnValue(false); mount()
    fireEvent.click(screen.getByRole('button', { name: '新建工作流' }))
    fireEvent.change(screen.getByPlaceholderText('例如 my-workflow'), { target: { value: 'new' } })
    fireEvent.change(screen.getByPlaceholderText('例如 我的工作流'), { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))
    expect(await screen.findByText(/已取消切换/)).toBeInTheDocument()
    expect(mocks.create).not.toHaveBeenCalled(); unchanged(session); expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('preserves the draft and explains a partial create if readback fails', async () => {
    const session = makeDraft(); mocks.create.mockResolvedValueOnce({}); mocks.get.mockRejectedValueOnce(new Error('offline')); mount()
    fireEvent.click(screen.getByRole('button', { name: '新建工作流' }))
    fireEvent.change(screen.getByPlaceholderText('例如 my-workflow'), { target: { value: 'new' } })
    fireEvent.change(screen.getByPlaceholderText('例如 我的工作流'), { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))
    expect(await screen.findByText(/工作流已创建，但未切换/)).toHaveTextContent('请勿盲目重试')
    unchanged(session); expect(window.confirm).toHaveBeenCalledTimes(1)
  })
  it('commits a created and read-back target only after it is present in the refreshed list', async () => {
    const session = makeDraft(); const target: WorkflowSpec = { id: 'new', title: 'New', version: '1', nodes: [] }
    mocks.create.mockResolvedValueOnce({}); mocks.get.mockResolvedValueOnce(target)
    mocks.refetch.mockImplementationOnce(async () => { mocks.rows = [...mocks.rows, row('new', 'New')]; return { data: mocks.rows } })
    mount(); fireEvent.click(screen.getByRole('button', { name: '新建工作流' }))
    fireEvent.change(screen.getByPlaceholderText('例如 my-workflow'), { target: { value: 'new' } })
    fireEvent.change(screen.getByPlaceholderText('例如 我的工作流'), { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=new'))
    expect(state().spec).toEqual(target); expect(readDraft(session, 'yaml')).toBeUndefined()
    expect(screen.getByRole('button', { name: /切换工作流，当前：New/ })).toBeInTheDocument()
    expect(window.confirm).toHaveBeenCalledTimes(1); expect(mocks.create).toHaveBeenCalledTimes(1)
  })

  it('commits an active-only target with a null DB baseline rather than inventing a draft', async () => {
    mocks.get.mockRejectedValue({ status: 404, body: '{"code":"WORKFLOW_NOT_FOUND"}' })
    mocks.history.mockResolvedValue({ workflowId: 'b', history: [], active: { deployNumber: 2, version: 1, isActive: true, action: 'rollback' } })
    mocks.snapshot.mockResolvedValue({ workflowId: 'b', deployNumber: 2, version: 1, specJson: JSON.stringify(B) })
    mount(); chooseB()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('workflowId=b'))
    expect(state().spec).toEqual(B); expect(state()._databaseSpec).toBeNull()
    expect(mocks.snapshot).toHaveBeenCalledWith('b', 2)
  })

})
