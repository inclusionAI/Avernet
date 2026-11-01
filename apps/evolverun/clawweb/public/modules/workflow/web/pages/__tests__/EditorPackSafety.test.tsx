import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserRouter, MemoryRouter, Link, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ id: '', fetchJson: vi.fn(), deploy: vi.fn(), saveDb: vi.fn(), gitSave: vi.fn(), completeSave: vi.fn(), history: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: { id: mocks.id, title: 'Flow', version: '1', nodes: [] }, isLoading: false }),
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saveDb, isPending: false }), useFacadeBindings: () => ({ data: [] }),
  useAutoHealSave: () => ({ mutateAsync: mocks.gitSave, isPending: false }), useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'user' }) }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson, api: { workflows: { getHistory: mocks.history, getDeploySnapshot: vi.fn() } } }))
vi.mock('@xyflow/react', () => ({ ReactFlowProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { useEditorStore } from '../../editor/store'
import { stringify } from 'yaml'
import Editor from '../Editor'
const A = 'a'.repeat(40), B = 'b'.repeat(40)
let seq = 0
function snapshot(version: 'current' | 'draft' = 'current') { return { workflowId: mocks.id, packId: 'pack', canWrite: true, baseRef: `origin/${version === 'current' ? 'wf' : 'saved'}/${mocks.id}`, baseCommit: A, draftHead: version === 'current' ? null : A, versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [{ id: mocks.id, file: 'flows/main.yaml' }] })), mode: '100644' }, { path: 'flows/main.yaml', contentBase64: btoa(stringify({ id: mocks.id, title: 'Flow', version: '1', nodes: [] })), mode: '100644' }, { path: 'scripts/test', contentBase64: 'b2xk', mode: '100644' }] } }
function Shell() { const navigate = useNavigate(); return <><Link to="/outside">离开</Link><button onClick={() => navigate(-1)}>后退</button><Routes><Route path="/editor" element={<Editor embedded initialWorkflowId={mocks.id} />} /><Route path="/outside" element={<h1>外部</h1>} /></Routes></> }
function mount() { return render(<MemoryRouter initialEntries={['/outside', '/editor']} initialIndex={1}><Shell /></MemoryRouter>) }
async function openFile() { fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(await screen.findByRole('button', { name: 'scripts/test' })) }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
beforeEach(() => {
  mocks.id = `pack-safe-${++seq}`; vi.clearAllMocks()
  useEditorStore.getState().reset({ force: true })
  useEditorStore.getState().loadSpec({ id: mocks.id, title: 'Flow', version: '1', nodes: [] })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init.body as Blob) })
      return { branch: `saved/${mocks.id}`, commit: B, requestId: JSON.parse(text).requestId, packId: 'pack', version: 2 }
    }
    return snapshot(url.includes('version=draft') ? 'draft' : 'current')
  })
  vi.spyOn(window, 'alert').mockImplementation(() => {}); vi.spyOn(window, 'confirm').mockReturnValue(false)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Editor and Pack candidate isolation', () => {
  it('saves file edits through the shared Pack path without legacy DB or Git writes', async () => {
    mount(); await openFile()
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'new yaml' } })
    fireEvent.click(screen.getByRole('button', { name: '保存到草稿' }))
    await screen.findByText(new RegExp(`已保存到草稿 commit ${B}`))
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(screen.queryByText(/工作流保存已阻止/)).not.toBeInTheDocument()
    expect(mocks.saveDb).not.toHaveBeenCalled(); expect(mocks.gitSave).not.toHaveBeenCalled(); expect(useEditorStore.getState().isDirty).toBe(false)
  })
  it('blocks deploy and canvas save with a dirty candidate even after switching tabs', async () => {
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'dirty' } })
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    expect(screen.getByText(/切换标签后仍保留/)).toBeInTheDocument()
    expect(screen.getByText(/导出仅包含已应用的画布工作流 spec/)).toHaveTextContent('不包含 Pack 版本文件（包括 YAML、脚本和资源）、未应用草稿或画布节点位置')
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText(/版本文件存在未保存更改/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
    expect(mocks.saveDb).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(screen.getByRole('button', { name: 'scripts/test' }))
    expect(screen.getByLabelText('文件内容')).toHaveValue('dirty')
  })
  it('blocks deploying if file changes while the draft read is in flight', async () => {
    const pending = deferred<unknown>(); mount(); await openFile()
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' }))
    mocks.fetchJson.mockReturnValueOnce(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' })); fireEvent.click(screen.getByRole('button', { name: 'scripts/test' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'late change' } })
    await act(async () => { pending.resolve(snapshot('draft')); await pending.promise })
    expect(screen.getByText(/读取期间编辑内容已变化/)).toBeInTheDocument(); expect(mocks.deploy).not.toHaveBeenCalled()
  })
  it('does not deploy after an unsuccessful Pack save and retains the file', async () => {
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'unsaved' } })
    mocks.fetchJson.mockRejectedValueOnce({ status: 409, body: '{"code":"VERSION_CONFLICT"}' }); fireEvent.click(screen.getByRole('button', { name: '保存到草稿' }))
    expect((await screen.findAllByRole('alert')).some(alert => alert.textContent?.includes('保存冲突'))).toBe(true)
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(mocks.deploy).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    fireEvent.click(screen.getByRole('button', { name: 'scripts/test' })); expect(screen.getByLabelText('文件内容')).toHaveValue('unsaved')
  })
  it('blocks save, deploy, version/workspace switches, links and programmatic back during upload', async () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    mount(); await openFile()
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['new'], 'new')] } })
    expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /草稿版/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('tab', { name: '工作流编排' })); expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('button', { name: '部署工作流' })); expect(mocks.deploy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('link', { name: '离开' })); fireEvent.click(screen.getByRole('button', { name: '后退' }))
    expect(screen.queryByRole('heading', { name: '外部' })).not.toBeInTheDocument(); expect(window.alert).toHaveBeenCalled()
    const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); expect(event.defaultPrevented).toBe(true)
    Object.defineProperty(reader, 'result', { value: new Uint8Array([110, 101, 119]).buffer }); act(() => reader.dispatchEvent(new ProgressEvent('load')))
    expect(screen.getByLabelText('文件内容')).toHaveValue('new')
    expect(screen.getByRole('button', { name: '保存到草稿' })).toBeEnabled()
  })
  it('warns and allows cancellation on route leave while retaining dirty files', async () => {
    mount(); await openFile(); fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'draft' } })
    fireEvent.click(screen.getByRole('link', { name: '离开' }))
    expect(window.confirm).toHaveBeenCalled(); expect(screen.queryByRole('heading', { name: '外部' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('文件内容')).toHaveValue('draft')
  })
  it('blocks a native browser POP during upload before the router can unmount the editor', async () => {
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(() => {})
    const go = vi.spyOn(window.history, 'go').mockImplementation(() => {})
    window.history.replaceState({ idx: 4 }, '', '/editor')
    render(<BrowserRouter><Shell /></BrowserRouter>); await openFile()
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['new'], 'new')] } })
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 3 } })))
    expect(go).toHaveBeenCalledWith(1)
    expect(screen.getByRole('tab', { name: '脚本文件' })).toHaveAttribute('aria-selected', 'true')
    expect(window.alert).toHaveBeenCalled()
    act(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { idx: 4 } })))
    window.history.replaceState(null, '', '/')
  })

  it('rejects a remotely advanced candidate until its baseline has been read', async () => {
    mount()
    await waitFor(() => expect(screen.queryByText('正在读取完整 Git 基线…')).not.toBeInTheDocument())
    mocks.fetchJson.mockResolvedValueOnce({ ...snapshot('draft'), baseCommit: B, draftHead: B })
    fireEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(await screen.findByText(/部署候选与当前已读取基线或画布不一致/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  })

  it('blocks confirmation when Pack files changed after the deployment dialog opened', async () => {
    mount(); await waitFor(() => expect(screen.queryByText('正在读取完整 Git 基线…')).not.toBeInTheDocument()); fireEvent.click(screen.getByRole('button', { name: '部署' }))
    await screen.findByRole('button', { name: '确认部署' })
    fireEvent.click(screen.getByRole('tab', { name: '脚本文件' }))
    fireEvent.click(await screen.findByRole('button', { name: 'scripts/test' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'changed after confirm' } })
    fireEvent.click(screen.getByRole('button', { name: '确认部署' }))
    expect(screen.getByText(/确认期间编辑内容已变化/)).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })

})
